"""
Speaking evaluation pipeline.

Replaces score_speaking_stub() in scoring.py once an invitation has audio recordings.

Pipeline per audio file:
  1. Whisper API  -> transcript + word-level timestamps
  2. Azure Speech -> pronunciation accuracy + fluency scores
  3. Python       -> confidence score from filler words, pauses, restarts

Then aggregated across all 3 questions:
  4. GPT-4o reads all transcripts -> grammar + vocabulary scores

Final rubric (must sum to 100):
  Pronunciation  20%   (Azure AccuracyScore)
  Fluency        25%   (Azure FluencyScore)
  Grammar        20%   (GPT-4o)
  Vocabulary     15%   (GPT-4o)
  Confidence     20%   (Python signals)

Failure handling: each stage is wrapped in try/except. A failure on one stage
for one question doesn't kill the whole eval — that dimension's score for that
question is recorded as None and excluded from the average.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from statistics import mean
from typing import Optional

from sqlalchemy.orm import Session

from models import AudioRecording, Invitation, SpeakingTopic

log = logging.getLogger("speaking_eval")


# ------------------------------------------------------------------
# Debug printer.
#
# We print to stdout (not the logger) because:
#   1. uvicorn surfaces stdout immediately in the terminal
#   2. flush=True bypasses any buffering so we see live progress
#   3. We want this on by default during development; the logger
#      framework would require config changes to show INFO level
#
# To silence: set env var SPEAKING_DEBUG=0 before starting uvicorn.
# ------------------------------------------------------------------
_DEBUG_ON = os.getenv("SPEAKING_DEBUG", "1") != "0"


def dbg(prefix: str, message: str = "") -> None:
    """Print one labeled debug line. prefix is a short tag like 'whisper'."""
    if not _DEBUG_ON:
        return
    if message:
        print(f"  [{prefix}] {message}", flush=True)
    else:
        print(f"  [{prefix}]", flush=True)


def dbg_section(title: str) -> None:
    """Print a banner for a new section."""
    if not _DEBUG_ON:
        return
    print(f"\n{title}", flush=True)


# ------------------------------------------------------------------
# Rubric weights — single source of truth.
# Changing these here automatically changes the final score formula.
# ------------------------------------------------------------------
RUBRIC_WEIGHTS = {
    "pronunciation": 0.20,
    "fluency":       0.25,
    "grammar":       0.20,
    "vocabulary":    0.15,
    "confidence":    0.20,
}
assert abs(sum(RUBRIC_WEIGHTS.values()) - 1.0) < 1e-9, "Rubric weights must sum to 1.0"

# Audio shorter than this is treated as "no real attempt" — all dimensions = 0.
MIN_AUDIO_SECONDS = 5.0

# Whisper expects max 25MB. We don't expect anywhere near that for 60-90s of speech,
# but cap defensively.
MAX_AUDIO_BYTES = 25 * 1024 * 1024

# Common English filler words — used by confidence calculation.
# Kept conservative; a non-native speaker saying "well" once shouldn't be penalized.
# Variants here mirror the disfluency prompt sent to Whisper, so what we ask
# Whisper to preserve is what we actually count.
FILLER_WORDS = {
    "um", "uh", "umm", "uhh", "er", "erm", "ah", "ahh",
    "hmm", "mhm", "mhmm", "uhm", "ehm",
    "like", "you know", "i mean", "sort of", "kind of",
    "basically", "actually", "literally",
}


# ==================================================================
# Stage 1: Whisper transcription
# ==================================================================
def transcribe_with_whisper(audio_path: Path) -> dict:
    """
    Returns:
      {
        "text": str,                       # full transcript
        "words": [{"word": str, "start": float, "end": float}, ...],
        "duration": float,                 # seconds
        "language": str,                   # full name, e.g. "english", "hindi" (NOT ISO code in verbose_json)
      }

    Raises RuntimeError on API failure so the caller can mark this question failed.
    """
    from openai import OpenAI

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set in environment")

    client = OpenAI(api_key=api_key)

    if audio_path.stat().st_size > MAX_AUDIO_BYTES:
        raise RuntimeError(f"Audio file {audio_path} exceeds {MAX_AUDIO_BYTES} bytes")

    file_size_kb = audio_path.stat().st_size / 1024
    dbg("file", f"{audio_path}  (size: {file_size_kb:.1f} KB)")
    dbg("whisper", "sending to OpenAI Whisper API...")

    # verbose_json + word timestamps gives us everything we need for fluency/confidence.
    # We force language="en" — non-English answers will transcribe poorly and that's
    # exactly the signal we want (low scores rather than auto-translation).
    #
    # The `prompt` parameter biases Whisper toward keeping disfluencies (umm, uh, ahh)
    # in the transcript instead of cleaning them up. This is a soft hint, not a hard
    # rule — Whisper still drops some, especially short or quiet ones. But it
    # measurably improves filler-word detection rates.
    DISFLUENCY_PROMPT = (
        "This is a spontaneous spoken response from an English assessment test. "
        "It may contain hesitation sounds like um, uh, umm, ahh, hmm, er, erm, mhm, "
        "and self-corrections. Transcribe them as spoken without cleaning them up."
    )

    t0 = time.time()
    with open(audio_path, "rb") as f:
        result = client.audio.transcriptions.create(
            file=f,
            model="whisper-1",
            response_format="verbose_json",
            timestamp_granularities=["word"],
            language="en",
            prompt=DISFLUENCY_PROMPT,
        )
    elapsed = time.time() - t0

    # The OpenAI Python SDK returns a Pydantic model; .model_dump() flattens it.
    data = result.model_dump() if hasattr(result, "model_dump") else dict(result)

    # Normalize the words list. The SDK has shipped multiple shapes over versions:
    #   - dicts with keys "word"/"start"/"end"
    #   - dicts with keys "text"/"start"/"end"
    #   - Pydantic objects with attributes word/start/end
    # Downstream code (calculate_confidence) needs uniform dicts, so we coerce here.
    raw_words = data.get("words") or []
    words: list[dict] = []
    for w in raw_words:
        if isinstance(w, dict):
            token = w.get("word") or w.get("text") or ""
            start = w.get("start", 0.0)
            end = w.get("end", 0.0)
        else:
            # Pydantic object — use getattr so missing fields default safely
            token = getattr(w, "word", None) or getattr(w, "text", "")
            start = getattr(w, "start", 0.0)
            end = getattr(w, "end", 0.0)
        try:
            start = float(start) if start is not None else 0.0
            end = float(end) if end is not None else 0.0
        except (TypeError, ValueError):
            start, end = 0.0, 0.0
        words.append({"word": str(token), "start": start, "end": end})

    text = (data.get("text") or "").strip()
    duration = float(data.get("duration") or 0.0)
    language = data.get("language") or "unknown"

    dbg("whisper", f"OK in {elapsed:.1f}s | duration={duration:.1f}s | language='{language}' | {len(words)} words")
    dbg("whisper", f"transcript: \"{text[:120]}{'...' if len(text) > 120 else ''}\"")
    if words:
        dbg("whisper", "first 5 words with timestamps:")
        for w in words[:5]:
            print(f"           {w['start']:5.2f}-{w['end']:5.2f}  {w['word']}", flush=_DEBUG_ON)

    return {
        "text": text,
        "words": words,
        "duration": duration,
        "language": language,
    }


# ==================================================================
# Stage 2: Azure pronunciation assessment
# ==================================================================
def assess_pronunciation_with_azure(audio_path: Path, reference_text: str) -> dict:
    """
    Runs Azure pronunciation assessment in unscripted, continuous mode.
    "Unscripted" because our prompts are open-ended ("describe a conflict") —
    we don't know what the candidate will say.

    reference_text is the Whisper transcript. Azure uses it to align phonemes
    against what the candidate actually said.

    Returns:
      {
        "accuracy": float,        # 0-100, phoneme accuracy
        "fluency":  float,        # 0-100, pace + pause structure
        "completeness": float,    # 0-100, did they speak vs. silence
      }

    Raises RuntimeError on failure.
    """
    try:
        import azure.cognitiveservices.speech as speechsdk
    except ImportError as e:
        raise RuntimeError(
            "azure-cognitiveservices-speech not installed. "
            "Run: pip install azure-cognitiveservices-speech"
        ) from e

    speech_key = os.getenv("AZURE_SPEECH_KEY")
    speech_region = os.getenv("AZURE_SPEECH_REGION")
    if not speech_key or not speech_region:
        raise RuntimeError(
            "AZURE_SPEECH_KEY and AZURE_SPEECH_REGION must be set in environment"
        )

    dbg("azure", f"sending to Azure Speech ({speech_region})...")

    # Azure SDK requires WAV/PCM 16kHz mono. Browser MediaRecorder produces
    # webm/opus. We convert via ffmpeg into a temp file.
    wav_path = _convert_to_wav_16k_mono(audio_path)
    dbg("azure", f"converted to WAV: {wav_path.stat().st_size / 1024:.1f} KB")

    try:
        speech_config = speechsdk.SpeechConfig(
            subscription=speech_key, region=speech_region
        )
        speech_config.speech_recognition_language = "en-US"

        audio_config = speechsdk.audio.AudioConfig(filename=str(wav_path))

        # Unscripted mode: we don't pass reference text in the JSON config,
        # we let Azure recognize freely AND score what it heard.
        pa_config = speechsdk.PronunciationAssessmentConfig(
            reference_text="",  # empty = unscripted
            grading_system=speechsdk.PronunciationAssessmentGradingSystem.HundredMark,
            granularity=speechsdk.PronunciationAssessmentGranularity.Phoneme,
            enable_miscue=False,  # not supported in continuous/unscripted mode
        )

        recognizer = speechsdk.SpeechRecognizer(
            speech_config=speech_config, audio_config=audio_config
        )
        pa_config.apply_to(recognizer)

        # Continuous recognition needed for >30s audio. We collect events.
        scores = {"accuracy": [], "fluency": [], "completeness": []}
        done = False

        def on_recognized(evt):
            try:
                pa_result = speechsdk.PronunciationAssessmentResult(evt.result)
                scores["accuracy"].append(pa_result.accuracy_score)
                scores["fluency"].append(pa_result.fluency_score)
                scores["completeness"].append(pa_result.completeness_score)
            except Exception as ex:
                log.warning("Azure on_recognized parse failed: %s", ex)

        def on_session_stopped(evt):
            nonlocal done
            done = True

        recognizer.recognized.connect(on_recognized)
        recognizer.session_stopped.connect(on_session_stopped)
        recognizer.canceled.connect(on_session_stopped)

        t0 = time.time()
        recognizer.start_continuous_recognition()
        # Wait for completion. Azure SDK is event-driven; this is the simplest poll.
        timeout = 120  # seconds — generous; 60s of audio rarely takes more than 30s to process
        elapsed = 0.0
        while not done and elapsed < timeout:
            time.sleep(0.5)
            elapsed += 0.5
        recognizer.stop_continuous_recognition()
        api_elapsed = time.time() - t0

        if not scores["accuracy"]:
            raise RuntimeError("Azure returned no recognized segments")

        result = {
            "accuracy": round(mean(scores["accuracy"]), 2),
            "fluency": round(mean(scores["fluency"]), 2),
            "completeness": round(mean(scores["completeness"]), 2),
        }
        dbg("azure", f"OK in {api_elapsed:.1f}s | {len(scores['accuracy'])} segments")
        dbg("azure", f"accuracy={result['accuracy']} | fluency={result['fluency']} | completeness={result['completeness']}")
        return result
    finally:
        try:
            wav_path.unlink(missing_ok=True)
        except Exception:
            pass


def _convert_to_wav_16k_mono(src_path: Path) -> Path:
    """
    Convert any audio file to WAV PCM 16kHz mono using ffmpeg.
    Returns the path to a new temp file. Caller is responsible for cleanup.

    Requires ffmpeg in PATH. On Windows: install via choco/scoop. On Linux: apt install ffmpeg.
    """
    import subprocess
    out_fd, out_path = tempfile.mkstemp(suffix=".wav")
    os.close(out_fd)
    out_path = Path(out_path)

    cmd = [
        "ffmpeg", "-y", "-i", str(src_path),
        "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
        str(out_path),
    ]
    try:
        subprocess.run(
            cmd, check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            timeout=30,
        )
    except FileNotFoundError as e:
        raise RuntimeError("ffmpeg not found in PATH. Install ffmpeg first.") from e
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"ffmpeg conversion failed: {e.stderr.decode(errors='ignore')[:300]}") from e
    return out_path


# ==================================================================
# Stage 3: Confidence score from transcript + word timestamps
# ==================================================================
def calculate_confidence(transcript: str, words: list[dict], duration: float) -> dict:
    """
    Confidence is a derived metric from observable nervousness markers.

    Word-based signals (from transcript):
      - filler words per minute (more = less confident)
      - long pauses (>1.5s gaps between consecutive words)
      - self-corrections / restarts (repeated words)

    Time-based signals (from word timestamps + total audio duration —
    these catch hesitation that Whisper dropped from the transcript):
      - speech ratio (spoken time / total time; low = lots of dead air)
      - leading silence (delay before first word; high = stalling)

    Returns 0-100 score plus the raw signal counts so HR can see WHY.
    """
    if duration <= 0 or not transcript:
        dbg("confidence", "skipped (zero duration or empty transcript)")
        return {
            "score": 0,
            "filler_per_min": 0,
            "long_pauses": 0,
            "restarts": 0,
            "speech_ratio": 0,
            "leading_silence": 0,
        }

    dbg("confidence", "tokenizing transcript...")

    # ---- Filler words ----
    text_lower = transcript.lower()
    # Count single-word fillers via tokenization
    tokens = re.findall(r"\b[\w']+\b", text_lower)
    found_single = [t for t in tokens if t in FILLER_WORDS]
    # Multi-word fillers are scanned as substrings
    multi_fillers = 0
    found_multi: list[str] = []
    for f in FILLER_WORDS:
        if " " in f:
            count_here = text_lower.count(f)
            if count_here:
                multi_fillers += count_here
                found_multi.extend([f] * count_here)
    single_fillers = len(found_single)
    total_fillers = single_fillers + multi_fillers
    minutes = duration / 60.0
    filler_per_min = total_fillers / minutes if minutes > 0 else 0

    all_found = found_single + found_multi
    dbg("confidence",
        f"filler words found: {all_found if all_found else '(none)'} "
        f"({total_fillers} total)")
    dbg("confidence", f"filler rate: {filler_per_min:.1f} per minute")

    # ---- Long pauses (gaps between consecutive words >1.5s) ----
    # Excludes leading silence (before first word) and trailing silence.
    long_pauses = 0
    pause_details: list[str] = []
    for i in range(1, len(words)):
        gap = float(words[i].get("start", 0)) - float(words[i - 1].get("end", 0))
        if gap > 1.5:
            long_pauses += 1
            pause_details.append(
                f"between '{words[i-1]['word']}' ({words[i-1]['end']:.1f}s) "
                f"and '{words[i]['word']}' ({words[i]['start']:.1f}s) = {gap:.1f}s gap"
            )
    dbg("confidence", f"long pauses (>1.5s): {long_pauses}")
    for detail in pause_details:
        print(f"           - {detail}", flush=_DEBUG_ON)

    # ---- Self-corrections / restarts ----
    # Heuristic: same word appearing twice within a 3-word window suggests a restart.
    # ("I— I think" or "the the meeting" — natural in nervous speech)
    restarts = 0
    restart_details: list[str] = []
    for i in range(1, len(tokens)):
        if tokens[i] == tokens[i - 1] and len(tokens[i]) > 1:
            restarts += 1
            restart_details.append(f"'{tokens[i]}' repeated at position {i}")
    dbg("confidence", f"restarts: {restarts}")
    for detail in restart_details:
        print(f"           - {detail}", flush=_DEBUG_ON)

    # ---- Dead time / speech ratio ----
    # Whisper transcribes only spoken words. Anything Whisper didn't transcribe
    # (silence, "umm" it dropped, breath sounds) shows up as a gap between
    # word_end and the next word_start — OR as time before the first word OR
    # after the last word. We compute the ratio of "actual speech" to "total
    # audio duration." Low ratio = lots of dead air, even if no individual
    # gap exceeded our 1.5s threshold above.
    spoken_seconds = 0.0
    if words:
        for w in words:
            ws = float(w.get("start", 0))
            we = float(w.get("end", 0))
            if we > ws:
                spoken_seconds += (we - ws)
    speech_ratio = spoken_seconds / duration if duration > 0 else 0
    dbg("confidence",
        f"speech ratio: {spoken_seconds:.1f}s spoken / {duration:.1f}s total "
        f"= {speech_ratio*100:.0f}%")

    # ---- Leading silence ----
    # Time from audio start to the first word. Reflects "hesitation before
    # answering." A confident speaker starts within 1s; >2s means they
    # stalled, possibly with umms Whisper dropped.
    leading_silence = float(words[0].get("start", 0)) if words else duration
    dbg("confidence", f"leading silence: {leading_silence:.1f}s before first word")

    # ---- Combine into a 0-100 score ----
    # Penalty model: start at 100, subtract for each marker, clamp at 0.
    # Tunable; see comments for calibration logic.
    #
    # Word-level penalties (from transcript):
    #   5 fillers/min  → -10  (light hedging, normal)
    #   10 fillers/min → -25  (clearly nervous)
    #   1 long pause   → -3   (forgivable for thinking)
    #   5 long pauses  → -15  (struggling)
    #   1 restart      → -2
    #   5 restarts     → -10
    #
    # Time-level penalties (from timestamps — catches what Whisper drops):
    #   speech ratio 70% → -0   (normal pacing)
    #   speech ratio 50% → -10  (lots of dead air / hesitation)
    #   speech ratio 30% → -20  (more silence than speech)
    #   leading silence 1s → -0   (normal)
    #   leading silence 3s → -6   (clearly stalled)
    #   leading silence 5s+ → -10 (very hesitant start)
    p_filler = min(filler_per_min * 2.5, 35)
    p_pause = min(long_pauses * 3, 20)
    p_restart = min(restarts * 2, 15)
    # Speech ratio: penalty kicks in below 70%, scales linearly to 30% floor.
    if speech_ratio >= 0.70:
        p_dead = 0.0
    elif speech_ratio <= 0.30:
        p_dead = 20.0
    else:
        # Linear from (0.70, 0) to (0.30, 20)
        p_dead = (0.70 - speech_ratio) * 50.0
    p_dead = min(p_dead, 20)
    # Leading silence: 0 below 1s, scales to -10 at 5s+
    if leading_silence <= 1.0:
        p_lead = 0.0
    elif leading_silence >= 5.0:
        p_lead = 10.0
    else:
        p_lead = (leading_silence - 1.0) * 2.5
    p_lead = min(p_lead, 10)

    raw_score = 100.0 - p_filler - p_pause - p_restart - p_dead - p_lead
    score = max(0, min(100, round(raw_score)))

    dbg("confidence",
        f"formula: 100 "
        f"- filler({filler_per_min:.1f}/min={p_filler:.1f}) "
        f"- pauses({long_pauses}={p_pause:.0f}) "
        f"- restarts({restarts}={p_restart:.0f}) "
        f"- dead({speech_ratio*100:.0f}%={p_dead:.1f}) "
        f"- lead({leading_silence:.1f}s={p_lead:.1f}) "
        f"= {score}")

    return {
        "score": score,
        "filler_per_min": round(filler_per_min, 1),
        "long_pauses": long_pauses,
        "restarts": restarts,
        "speech_ratio": round(speech_ratio, 2),
        "leading_silence": round(leading_silence, 1),
    }


# ==================================================================
# Stage 4: GPT-4o grades grammar + vocabulary
# ==================================================================
# Design: per-question grading (not bulk), calibrated for global English
# (native + non-native), with off-topic detection as a flag (not a score).
# Followed by a small synthesis call to produce one HR-readable paragraph.
# ==================================================================

# Calibration anchors. These are illustrative transcript snippets at each band.
# Adding them to the prompt anchors GPT-4o's judgment so two similar transcripts
# get similar scores, instead of drifting based on the model's mood.
_GRAMMAR_ANCHORS = """
GRAMMAR band examples (for global English — native AND non-native speakers):
  90-100: "I believe my strongest contribution to the team has been mentoring
          junior engineers, which has helped reduce onboarding time significantly."
  70-89:  "I have done my Bachelors of Engineering in Computer Science and
          have skills in Python, Data Structures, and Machine Learning."
          (Indian English phrasing — meaning is fully clear, score in this band.)
  50-69:  "I prefer working with people because is very boring to work alone
          and I need some fun in my life around me."
          (Frequent missing articles and tense slips, but still understandable.)
  30-49:  "I no want work in team, I do better when I am alone, no people."
  0-29:   "Team... yes... working... I think... not sure how to say."
""".strip()

_VOCAB_ANCHORS = """
VOCABULARY band examples:
  90-100: Uses precise professional terms ("articulate", "advocate", "synthesize")
          with accurate context. Varied register.
  70-89:  Uses domain vocabulary correctly ("Bachelors of Engineering",
          "AI Agents", "Machine Learning", "Software Engineer"). Some repetition
          allowed. Professional non-native speakers normally score in this band.
  50-69:  Limited range. Words like "good", "nice", "very" repeat. Cannot
          reach for precise terms when needed.
  30-49:  Very basic words only. Struggles to name common concepts.
  0-29:   Cannot find words for basic ideas. Long silences while searching.
""".strip()


def _grade_one_response(prompt: str, transcript: str, qno: int) -> dict:
    """
    Grade a single (question, answer) pair. Returns:
      {
        "grammar": int 0-100 or None,
        "vocabulary": int 0-100 or None,
        "on_topic": bool,
        "observation": str,        # 1-2 sentences, for synthesis later
      }

    Off-topic detection: we ask GPT-4o whether the answer addresses the question.
    This is a FLAG, not a score — does not feed into grammar/vocab numbers.
    A candidate who reads the question aloud then says "I can't say that"
    will get on_topic=false even if their grammar is fine.
    """
    from openai import OpenAI

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    client = OpenAI(api_key=api_key)

    # Empty / unusable answer — return zeros without burning an API call
    if not transcript or len(transcript.strip()) < 10:
        return {
            "grammar": 0,
            "vocabulary": 0,
            "on_topic": False,
            "observation": f"Q{qno}: no usable answer captured.",
        }

    system_msg = (
        "You are an English proficiency assessor for job recruitment. You grade "
        "ONE spoken response (provided as a Whisper transcript). The candidate may "
        "be a native or non-native English speaker; you score for "
        "CLARITY AND COMMUNICATIVE EFFECTIVENESS, not for adherence to native "
        "phrasing. Indian, Singaporean, and other regional English variants are "
        "treated as legitimate English; do not penalize for accent-driven word order "
        "or article usage as long as meaning is clear.\n\n"
        "Return ONLY a JSON object:\n"
        '{"grammar": <int 0-100>, "vocabulary": <int 0-100>, '
        '"on_topic": <true|false>, "observation": "<one sentence about quality>"}\n\n'
        f"{_GRAMMAR_ANCHORS}\n\n"
        f"{_VOCAB_ANCHORS}\n\n"
        "on_topic guidance:\n"
        "  true  = the answer addresses the question, even imperfectly.\n"
        "  false = the answer evades the question, refuses ('I can't say that'),\n"
        "          reads the question back without answering, or talks about\n"
        "          something unrelated.\n\n"
        "observation = ONE sentence (max 25 words) describing the most notable "
        "strength or weakness in this response. Be specific. No score recap. "
        "No generic praise. Return only the JSON, nothing else."
    )

    user_msg = f"Question: {prompt}\n\nAnswer (transcript): {transcript}\n\nGrade this response."

    t0 = time.time()
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        response_format={"type": "json_object"},
        temperature=0.2,  # low temp for grading consistency
        max_tokens=200,
    )
    elapsed = time.time() - t0

    raw = response.choices[0].message.content or "{}"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        raise RuntimeError(f"GPT-4o returned non-JSON for Q{qno}: {raw[:150]}")

    def _clamp_int(v) -> Optional[int]:
        if v is None:
            return None
        try:
            n = int(v)
        except (TypeError, ValueError):
            return None
        return max(0, min(100, n))

    g = _clamp_int(parsed.get("grammar"))
    v = _clamp_int(parsed.get("vocabulary"))
    on_topic = bool(parsed.get("on_topic", True))
    observation = str(parsed.get("observation", ""))[:200]

    flag_marker = "" if on_topic else " [OFF-TOPIC]"
    dbg("gpt4o", f"Q{qno} graded in {elapsed:.1f}s | grammar={g} | vocab={v}{flag_marker}")
    dbg("gpt4o", f"Q{qno} observation: \"{observation[:120]}{'...' if len(observation) > 120 else ''}\"")

    return {
        "grammar": g,
        "vocabulary": v,
        "on_topic": on_topic,
        "observation": observation,
    }


def _synthesize_feedback(per_q_results: list[dict], off_topic_qs: list[int]) -> str:
    """
    Take 3 per-question observations + any off-topic flags and produce ONE
    short paragraph for the HR dashboard. This is a single small GPT-4o call
    so the final feedback reads like one unified assessment, not three
    disjointed bullets.

    On failure, falls back to concatenating the observations directly.
    """
    from openai import OpenAI

    raw_obs = [r["observation"] for r in per_q_results if r.get("observation")]
    if not raw_obs:
        return "Speaking section evaluated."

    # Cheap fallback in case the synthesis call itself fails
    fallback = " ".join(raw_obs)
    if off_topic_qs:
        fallback += " (Off-topic: " + ", ".join(f"Q{q}" for q in off_topic_qs) + ".)"

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return fallback
    client = OpenAI(api_key=api_key)

    obs_text = "\n".join(f"Q{i+1}: {obs}" for i, obs in enumerate(raw_obs))
    off_topic_text = (
        "\nFlagged off-topic: " + ", ".join(f"Q{q}" for q in off_topic_qs)
        if off_topic_qs else ""
    )

    system_msg = (
        "You write brief assessment summaries for HR. Given short per-question "
        "observations about a candidate's spoken English, produce ONE coherent "
        "paragraph (max 70 words) that captures overall strengths and weaknesses. "
        "If any questions are flagged off-topic, mention that explicitly so HR "
        "can review. No score recap. No generic praise. No bullet points. "
        "Return only the paragraph text, no JSON."
    )
    user_msg = f"Per-question observations:\n{obs_text}{off_topic_text}\n\nSynthesize."

    try:
        t0 = time.time()
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.3,
            max_tokens=150,
        )
        elapsed = time.time() - t0
        text = (response.choices[0].message.content or "").strip()
        dbg("gpt4o", f"feedback synthesis OK in {elapsed:.1f}s")
        return text[:600] if text else fallback
    except Exception as e:
        dbg("gpt4o", f"feedback synthesis FAILED: {type(e).__name__}: {e}")
        log.warning("Feedback synthesis failed: %s", e)
        return fallback


def grade_responses_with_gpt4o(transcripts: list[str], topic_prompts: list[str]) -> dict:
    """
    Grade all responses per-question, then average. Returns:
      {
        "grammar":   int 0-100 or None,
        "vocabulary": int 0-100 or None,
        "feedback":  str,
        "off_topic_qs": list[int],   # 1-indexed question numbers flagged off-topic
      }
    """
    dbg("gpt4o", f"per-question grading: {len(transcripts)} responses")
    per_q_results: list[dict] = []
    for i, (prompt, transcript) in enumerate(zip(topic_prompts, transcripts), start=1):
        try:
            result = _grade_one_response(prompt, transcript, qno=i)
        except Exception as e:
            dbg("gpt4o", f"Q{i} FAILED: {type(e).__name__}: {e}")
            log.warning("GPT-4o grading failed for Q%s: %s", i, e)
            result = {
                "grammar": None,
                "vocabulary": None,
                "on_topic": True,
                "observation": f"Q{i}: grading failed.",
            }
        per_q_results.append(result)

    # Average grammar/vocab across questions, skipping None
    g_vals = [r["grammar"] for r in per_q_results if r["grammar"] is not None]
    v_vals = [r["vocabulary"] for r in per_q_results if r["vocabulary"] is not None]
    grammar_avg = round(mean(g_vals)) if g_vals else None
    vocab_avg = round(mean(v_vals)) if v_vals else None

    off_topic_qs = [
        i + 1 for i, r in enumerate(per_q_results) if not r.get("on_topic", True)
    ]

    dbg("gpt4o", f"per-Q grammar:    {[r['grammar'] for r in per_q_results]} -> avg {grammar_avg}")
    dbg("gpt4o", f"per-Q vocabulary: {[r['vocabulary'] for r in per_q_results]} -> avg {vocab_avg}")
    if off_topic_qs:
        dbg("gpt4o", f"OFF-TOPIC questions flagged: {off_topic_qs}")
    else:
        dbg("gpt4o", "all questions on-topic")

    feedback = _synthesize_feedback(per_q_results, off_topic_qs)

    return {
        "grammar": grammar_avg,
        "vocabulary": vocab_avg,
        "feedback": feedback,
        "off_topic_qs": off_topic_qs,
    }


# ==================================================================
# Top-level orchestrator
# ==================================================================
def score_speaking(invitation: Invitation, db: Session) -> dict:
    """
    Run the full pipeline for one invitation. Designed to be called from
    scoring.score_invitation() in place of score_speaking_stub().

    Returns the SAME shape score_speaking_stub returns:
      {
        "breakdown": {"pronunciation": int|None, "fluency": int|None, ...},
        "total": int|None,
        "feedback": str,
      }

    On total failure (no audio at all), returns total=0 with explanatory feedback.
    On partial failure, dimensions that couldn't be computed are None and the total
    is computed from successful dimensions only (weights renormalized).
    """
    recordings: list[AudioRecording] = list(invitation.audio_recordings or [])

    dbg_section(f"================ SPEAKING EVAL: invitation {invitation.id} ================")
    dbg("found", f"{len(recordings)} audio recordings to process")

    if not recordings:
        dbg("end", "no audio — returning total=0")
        return {
            "breakdown": {k: None for k in RUBRIC_WEIGHTS},
            "total": 0,
            "feedback": "No audio recordings were submitted for this candidate.",
        }

    # Build a topic_id -> prompt_text map so we can pass prompts to GPT-4o
    topic_ids = list({r.topic_id for r in recordings})
    topic_map = {
        t.id: t.prompt_text
        for t in db.query(SpeakingTopic).filter(SpeakingTopic.id.in_(topic_ids)).all()
    }

    # Per-question results: list of dicts, one per recording
    per_q_pron: list[Optional[float]] = []
    per_q_fluency: list[Optional[float]] = []
    per_q_confidence: list[Optional[int]] = []
    transcripts: list[str] = []
    prompts_in_order: list[str] = []
    failure_notes: list[str] = []

    # Process each audio file
    for q_idx, rec in enumerate(sorted(recordings, key=lambda r: r.id), start=1):
        dbg_section(f"------- Q{q_idx} (recording id={rec.id}) -------")

        audio_path = Path(rec.file_path)
        prompt = topic_map.get(rec.topic_id, "")
        prompts_in_order.append(prompt)

        if not audio_path.is_file():
            dbg("file", f"MISSING: {audio_path}")
            failure_notes.append(f"Audio file missing for recording {rec.id}")
            transcripts.append("")
            per_q_pron.append(None)
            per_q_fluency.append(None)
            per_q_confidence.append(None)
            continue

        # ---- Stage 1: Whisper ----
        try:
            whisper_result = transcribe_with_whisper(audio_path)
            transcripts.append(whisper_result["text"])
            # Persist transcript so HR sees it in the dashboard
            rec.transcript = whisper_result["text"]
            rec.duration_seconds = int(whisper_result["duration"]) if whisper_result["duration"] else None
        except Exception as e:
            dbg("whisper", f"FAILED: {type(e).__name__}: {e}")
            log.warning("Whisper failed for recording %s: %s", rec.id, e)
            failure_notes.append(f"Transcription failed for question {rec.id}")
            transcripts.append("")
            per_q_pron.append(None)
            per_q_fluency.append(None)
            per_q_confidence.append(None)
            continue

        # Insufficient audio guard — short or non-English
        duration = whisper_result["duration"]
        text = whisper_result["text"]
        words = whisper_result["words"]

        if duration < MIN_AUDIO_SECONDS:
            dbg("guard", f"audio too short ({duration:.1f}s < {MIN_AUDIO_SECONDS}s) — zeroing all dimensions for this Q")
            failure_notes.append(f"Question {rec.id}: audio too short ({duration:.1f}s)")
            per_q_pron.append(0)
            per_q_fluency.append(0)
            per_q_confidence.append(0)
            continue

        # Whisper's verbose_json returns the full language NAME ("english", "hindi"),
        # while plain json format returns the ISO code ("en", "hi"). We accept both.
        # Normalize to lowercase, then check for an English variant.
        detected_lang = (whisper_result["language"] or "").lower()
        if detected_lang not in ("en", "english"):
            dbg("guard", f"non-English audio (detected '{whisper_result['language']}') — zeroing this Q")
            failure_notes.append(
                f"Question {rec.id}: detected language '{whisper_result['language']}' (expected English)"
            )
            per_q_pron.append(0)
            per_q_fluency.append(0)
            per_q_confidence.append(0)
            continue

        # ---- Stage 2: Azure pronunciation ----
        try:
            azure_result = assess_pronunciation_with_azure(audio_path, text)
            per_q_pron.append(azure_result["accuracy"])
            per_q_fluency.append(azure_result["fluency"])
        except Exception as e:
            dbg("azure", f"SKIPPED: {type(e).__name__}: {e}")
            log.warning("Azure pronunciation failed for recording %s: %s", rec.id, e)
            failure_notes.append(f"Pronunciation assessment failed for question {rec.id}")
            per_q_pron.append(None)
            per_q_fluency.append(None)

        # ---- Stage 3: Confidence (Python) ----
        try:
            conf_result = calculate_confidence(text, words, duration)
            per_q_confidence.append(conf_result["score"])
        except Exception as e:
            dbg("confidence", f"FAILED: {type(e).__name__}: {e}")
            log.warning("Confidence calc failed for recording %s: %s", rec.id, e)
            per_q_confidence.append(None)

    # ---- Stage 4: GPT-4o grammar + vocab on aggregated transcripts ----
    dbg_section("------- AGGREGATION -------")

    grammar_score: Optional[int] = None
    vocabulary_score: Optional[int] = None
    llm_feedback = ""
    off_topic_qs: list[int] = []
    if any(t.strip() for t in transcripts):
        try:
            grading = grade_responses_with_gpt4o(transcripts, prompts_in_order)
            grammar_score = grading["grammar"]
            vocabulary_score = grading["vocabulary"]
            llm_feedback = grading["feedback"]
            off_topic_qs = grading.get("off_topic_qs", [])
            if off_topic_qs:
                failure_notes.append(
                    "Off-topic answer flagged for "
                    + ", ".join(f"Q{q}" for q in off_topic_qs)
                    + " (HR review recommended)"
                )
        except Exception as e:
            dbg("gpt4o", f"FAILED: {type(e).__name__}: {e}")
            log.warning("GPT-4o grading failed: %s", e)
            failure_notes.append("Grammar/vocabulary grading failed")
    else:
        dbg("gpt4o", "skipped (no usable transcripts)")
        failure_notes.append("No usable transcripts for grammar/vocabulary grading")

    # ---- Aggregate per-dimension averages (skipping None) ----
    def _avg(values: list) -> Optional[int]:
        successful = [v for v in values if v is not None]
        if not successful:
            return None
        return round(mean(successful))

    breakdown = {
        "pronunciation": _avg(per_q_pron),
        "fluency": _avg(per_q_fluency),
        "grammar": grammar_score,
        "vocabulary": vocabulary_score,
        "confidence": _avg(per_q_confidence),
    }

    # Show per-question raw values so it's clear what's being averaged
    dbg("per-Q pron", str(per_q_pron))
    dbg("per-Q fluency", str(per_q_fluency))
    dbg("per-Q confidence", str(per_q_confidence))

    # ---- Compute weighted total over successful dimensions only ----
    weighted_sum = 0.0
    weight_used = 0.0
    formula_parts = []
    for dim, weight in RUBRIC_WEIGHTS.items():
        v = breakdown[dim]
        if v is None:
            formula_parts.append(f"{dim}=None×{weight}")
            continue
        contribution = v * weight
        weighted_sum += contribution
        weight_used += weight
        formula_parts.append(f"{dim}={v}×{weight}={contribution:.2f}")

    if weight_used == 0:
        total = 0
    else:
        # Renormalize so partial scoring still produces 0-100, not e.g. 0-80.
        total = round(weighted_sum / weight_used)

    dbg("final", f"breakdown: " + " | ".join(f"{k}={v}" for k, v in breakdown.items()))
    dbg("final", "formula: " + " + ".join(formula_parts))
    dbg("final", f"weighted_sum={weighted_sum:.2f} | weight_used={weight_used:.2f} | total={total}")
    if weight_used < 1.0:
        dbg("final", f"(renormalized: {weighted_sum:.2f} / {weight_used:.2f} = {total})")

    # ---- Off-topic penalty: -15 points per off-topic question ----
    # Applied to the final score AFTER renormalization. The penalty is
    # significant enough to push borderline candidates across rating bands
    # (e.g., 79 -> 64, Recommended -> Borderline), which matches how a real
    # assessment system treats refusal to answer / non-answers.
    off_topic_penalty = 15 * len(off_topic_qs)
    if off_topic_penalty > 0:
        pre_penalty = total
        total = max(0, total - off_topic_penalty)
        dbg("final",
            f"off-topic penalty: -{off_topic_penalty} ({len(off_topic_qs)} flagged Qs × 15) "
            f"-> {pre_penalty} - {off_topic_penalty} = {total}")

    feedback_parts = []
    if llm_feedback:
        feedback_parts.append(llm_feedback)
    if failure_notes:
        feedback_parts.append("Notes: " + "; ".join(failure_notes))
    feedback = " ".join(feedback_parts) or "Speaking section evaluated."

    dbg_section("================ END SPEAKING EVAL ================\n")

    return {
        "breakdown": breakdown,
        "total": total,
        "feedback": feedback,
        # 1-indexed question numbers flagged as off-topic. HR uses this to know
        # which audio was off-topic without parsing the feedback paragraph.
        "off_topic_qs": off_topic_qs,
    }