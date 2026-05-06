"""
Speaking evaluation pipeline.

Called from scoring.py via _run_speaking_eval() once an invitation has audio
recordings on disk. Top-level entry point is score_speaking().

OVERVIEW

Each invitation has up to 3 audio recordings (one per assigned speaking topic).
For EACH recording, we run all 4 stages independently:

    1. Whisper API   → transcript + word-level timestamps
    2. Azure Speech  → pronunciation accuracy + fluency scores
    3. Python        → confidence score from filler words, pauses, restarts
    4. GPT-4o        → grammar + vocabulary scores (graded per-question)

This gives us 5 dimension scores PER QUESTION:

    Q1: pron=88, fluency=90, grammar=85, vocab=80, confidence=82
    Q2: pron=65, fluency=60, grammar=70, vocab=65, confidence=60
    Q3: pron=40, fluency=30, grammar=35, vocab=30, confidence=25

We then weight each question's 5 dimensions using the rubric, getting a
PER-QUESTION total (0-100). The final speaking_score is the average of
those question totals:

    Q1 total = 88×0.20 + 90×0.25 + 85×0.20 + 80×0.15 + 82×0.20 = 86
    Q2 total = ... = 64
    Q3 total = ... = 33
    speaking_score = mean(86, 64, 33) = 61

RUBRIC WEIGHTS (must sum to 1.0; defined in config.py)

    Pronunciation  20%   (Azure AccuracyScore)
    Fluency        25%   (Azure FluencyScore)
    Grammar        20%   (GPT-4o, per-question)
    Vocabulary     15%   (GPT-4o, per-question)
    Confidence     20%   (Python signals from transcript timing)

EMPTY / BAD AUDIO HANDLING (multiple guards, fail loud not silently)

    1. File missing on disk          → Q gets all-None dimensions, total = 0
    2. File < MIN_AUDIO_BYTES (2KB)  → SHORT-CIRCUIT before Whisper, save the API call
    3. File < MIN_AUDIO_SECONDS      → zero the Q after Whisper sees it's too short
    4. Whisper hallucination on silence → guard via _is_whisper_hallucination()
    5. Non-English audio              → zero the Q

In all these cases, the question total comes out as 0 and gets averaged in,
so a candidate who didn't speak gets penalized rather than excluded.

API CALL FAILURES (different from empty audio)

If Whisper/Azure/GPT-4o ITSELF fails (API down, key missing, etc.) for one
dimension on one question, that dimension is recorded as None instead of 0.
The per-question total then renormalizes over the dimensions that DID
succeed, so a single API outage doesn't tank the score. None is also what
score_speaking_stub() returns when the entire evaluator can't import.

OFF-TOPIC PENALTY

GPT-4o flags answers that don't address the question (e.g. candidate reads
the question back, refuses to answer, talks about something unrelated).
Each off-topic question costs -15 points off the final speaking_score.
This kicks in AFTER renormalization, so it works the same regardless of
whether some dimensions failed.
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
# Rubric weights + minimum audio threshold are sourced from config.py
# so the client can tune them without editing scoring code.
# ------------------------------------------------------------------
from config import SPEAKING_RUBRIC_WEIGHTS as RUBRIC_WEIGHTS, MIN_AUDIO_SECONDS

assert abs(sum(RUBRIC_WEIGHTS.values()) - 1.0) < 1e-9, "Rubric weights must sum to 1.0"

# Known Whisper hallucination strings.
#
# Whisper-1 has a documented tendency to "hallucinate" generic text when given
# silent audio, background noise, or audio with no clear speech. The model
# was trained on millions of hours of transcribed video including subtitles
# and end-credit text, so on silence it tends to produce strings that look
# like subtitle artefacts.
#
# When we see one of these as the ENTIRE transcript (or substantially the
# whole transcript), it means the candidate didn't actually speak — they
# recorded silence or background noise. We should treat that the same as
# the short-audio path: zero the dimensions, exclude from GPT-4o grading.
#
# This list is conservative: we only match when the WHOLE transcript is one
# of these phrases, not when the phrase appears within a longer real
# transcript. A real candidate saying "Thanks for watching" mid-answer
# would still be graded normally.
WHISPER_HALLUCINATIONS = {
    "transcribed by https://otter.ai",
    "transcribed by otter.ai",
    "subtitles by the amara.org community",
    "thanks for watching",
    "thanks for watching!",
    "thank you for watching",
    "thank you for watching.",
    "thank you.",
    "thank you",
    "subtitles by",
    "subscribe to my channel",
    "[music]",
    "[applause]",
    "[no audio]",
    ".",
    "you",
    "bye",
    "bye.",
    "okay",
    "okay.",
}

# Substring patterns from Whisper's prompt-echo hallucination.
#
# When Whisper is given silent audio along with the disfluency context prompt
# we send (the "this is a spontaneous spoken response..." string near the top
# of transcribe_with_whisper), it sometimes just transcribes the PROMPT BACK
# as the audio's content. Real example we caught in production:
#   "This is a spontaneous spoken response from an English assessment test.
#    It may contain hesitation sounds like um, uh, umm, ahh, hmm, erm, mhm,
#    and self-corrections. This is a spontaneous spoken response from an
#    English assessment test."
#
# These phrases come straight from our prompt and a real candidate would
# never use them in an answer. Substring match (not exact) because Whisper
# sometimes echoes a fragment, not the whole prompt verbatim.
WHISPER_PROMPT_ECHO_FRAGMENTS = (
    "spontaneous spoken response",
    "may contain hesitation sounds",
    "english assessment test",
)


def _is_whisper_hallucination(text: str) -> bool:
    """
    Return True if `text` looks like a Whisper hallucination on silence.

    Three layers of detection:

      1. Empty or near-empty (<= 3 chars after stripping). Catches "you",
         ".", "" — even a one-word real answer would produce more text.

      2. Exact match against WHISPER_HALLUCINATIONS. Catches the common
         subtitle-style artefacts like "Transcribed by https://otter.ai".

      3. Substring match against WHISPER_PROMPT_ECHO_FRAGMENTS. Catches
         the case where Whisper echoes our own prompt back (real captured
         example was 234 chars long, all prompt-echo, scored 0/0/0/0/13
         before this guard caught it).

    A real candidate's answer that *contains* one of the EXACT-MATCH
    phrases as a substring is NOT flagged. But the prompt-echo fragments
    are distinctive enough ("spontaneous spoken response", "hesitation
    sounds") that any transcript containing them is almost certainly
    a hallucination — a real candidate answering the speaking questions
    wouldn't use this internal-instruction phrasing.
    """
    if not text:
        return True
    normalized = text.strip().lower()
    if len(normalized) <= 3:
        return True
    if normalized in WHISPER_HALLUCINATIONS:
        return True
    # Prompt-echo: even a single fragment match is enough. The fragments
    # are unique to our prompt — no real candidate would use them.
    for fragment in WHISPER_PROMPT_ECHO_FRAGMENTS:
        if fragment in normalized:
            return True
    return False


# Whisper expects max 25MB. We don't expect anywhere near that for 60-90s of speech,
# but cap defensively.
MAX_AUDIO_BYTES = 25 * 1024 * 1024

# Empty/near-empty container threshold. A WebM container with no real audio is
# typically 200-800 bytes. Anything under 2KB is definitely empty (browser created
# the container header but the candidate didn't actually speak). We skip Whisper
# entirely for these to avoid wasted API calls AND to avoid Whisper hallucinating
# subtitle artefacts on silence (caught downstream by _is_whisper_hallucination,
# but cheaper to short-circuit before the call).
#
# Threshold chosen at 2KB (not 5KB) because a real 1-second compressed recording
# can be ~3KB. Safer to occasionally let an empty recording through to the
# downstream guards than to false-reject a valid short answer.
MIN_AUDIO_BYTES = 2_000

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
    Stage 1 of the pipeline. Transcribe an audio file using OpenAI's
    Whisper API, including word-level timestamps used downstream by the
    confidence calculator.

    Returns:
      {
        "text": str,                       # full transcript
        "words": [{"word": str, "start": float, "end": float}, ...],
        "duration": float,                 # seconds
        "language": str,                   # full name, e.g. "english", "hindi" (NOT ISO code in verbose_json)
      }

    Raises RuntimeError on API failure so the caller can mark this question failed.
    Also raises RuntimeError if audio exceeds MAX_AUDIO_BYTES (defensive cap;
    we never actually expect 60-90s audio to come close).
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
        "This is a spontaneous spoken response from an English-proficiency "
        "assessment. Transcribe verbatim. Preserve hesitation sounds (um, uh, "
        "umm, ahh, hmm, er, erm, mhm) and self-corrections — do not clean "
        "them up. The transcript is used for downstream fluency analysis."
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
    Stage 2 of the pipeline. Send the audio file to Azure Speech and get
    back per-phoneme pronunciation accuracy + fluency scores.

    Mode is "unscripted, continuous" — unscripted because our speaking
    prompts are open-ended (we don't know what the candidate will say in
    advance), continuous because audio can be longer than 30s.

    reference_text is the Whisper transcript. Azure uses it to align phonemes
    against what the candidate actually said.

    Internally converts the WebM/Opus audio to WAV PCM 16kHz mono first,
    because the Azure SDK only accepts that format. ffmpeg must be in PATH.

    Returns:
      {
        "accuracy": float,        # 0-100, phoneme accuracy
        "fluency":  float,        # 0-100, pace + pause structure
        "completeness": float,    # 0-100, did they speak vs. silence
      }

    Raises RuntimeError on failure (no SDK, missing keys, ffmpeg missing,
    Azure returned no segments). Caller catches this and records None for
    pronunciation+fluency for that one question.
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
    Stage 3 of the pipeline. Compute a confidence score for one recording
    using purely Python — no API calls, runs in milliseconds.

    Confidence is a derived metric from observable nervousness markers:

    Word-based signals (from transcript):
      - filler words per minute (more = less confident)
      - long pauses (>1.5s gaps between consecutive words)
      - self-corrections / restarts (repeated words)

    Time-based signals (from word timestamps + total audio duration —
    these catch hesitation that Whisper dropped from the transcript):
      - speech ratio (spoken time / total time; low = lots of dead air)
      - leading silence (delay before first word; high = stalling)

    Each signal contributes a penalty (e.g. 5 fillers/min = -10 points).
    Final score is 100 minus all penalties, clamped 0-100.

    Returns:
      {
        "score":           int 0-100,    # the confidence score
        "filler_per_min":  float,        # raw signals so HR can see WHY
        "long_pauses":     int,
        "restarts":        int,
        "speech_ratio":    float 0-1,
        "leading_silence": float (seconds),
      }
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
        "You are a certified English-proficiency assessor evaluating a "
        "candidate's spoken response for corporate recruitment. The transcript "
        "was produced by Whisper from the candidate's audio. Apply the rubric "
        "below consistently across all candidates.\n\n"
        "CORE PRINCIPLE: score for CLARITY AND COMMUNICATIVE EFFECTIVENESS, "
        "not adherence to native phrasing. Indian, Singaporean, and other "
        "regional English variants are legitimate English. Do not penalize "
        "accent-driven word order, article usage, or pluralization "
        "differences when meaning is clear.\n\n"
        "SCORE STRICTLY ON OBSERVABLE EVIDENCE in the transcript. Do not "
        "infer what the candidate \"probably meant\" or give credit for "
        "unwritten ideas.\n\n"
        "Return ONLY a JSON object — no preamble, no markdown, no commentary:\n"
        '{"grammar": <int 0-100>, "vocabulary": <int 0-100>, '
        '"on_topic": <true|false>, "observation": "<one sentence about quality>"}\n\n'
        f"{_GRAMMAR_ANCHORS}\n\n"
        f"{_VOCAB_ANCHORS}\n\n"
        "ON_TOPIC GUIDANCE:\n"
        "  true  = the answer addresses the question, even imperfectly or partially.\n"
        "  false = the answer evades the question, refuses ('I can't say that'),\n"
        "          reads the question back without answering, talks about\n"
        "          something unrelated, or contains no usable answer.\n\n"
        "  Note: an answer can be on-topic AND poorly executed. on_topic is\n"
        "  about whether the candidate engaged with the question, not whether\n"
        "  they did it well.\n\n"
        "OBSERVATION RULES:\n"
        "  - Exactly ONE sentence, max 25 words.\n"
        "  - Specific to THIS response (cite phrasing or pattern if relevant).\n"
        "  - No score recap. No generic praise. No vague hedges.\n\n"
        "Return only the JSON object. Nothing else."
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
    Stage 4 of the pipeline. Grade all responses for grammar + vocabulary
    using GPT-4o, one question at a time, then synthesize a single feedback
    paragraph for HR.

    Per-question grading (calling _grade_one_response once per Q) gives us
    per-question scores. We return BOTH the per-question lists (used by
    score_speaking to compute per-question totals) AND the dimension averages
    (used by the dashboard's bar chart).

    Returns:
      {
        "grammar":          int 0-100 or None,    # average across Qs
        "vocabulary":       int 0-100 or None,    # average across Qs
        "per_q_grammar":    list[int],            # per-Q grammar scores
        "per_q_vocabulary": list[int],            # per-Q vocab scores
        "feedback":         str,                  # single HR-ready paragraph
        "off_topic_qs":     list[int],            # 1-indexed Q numbers flagged
      }

    A single Q failure (API error mid-batch) → that Q's grammar/vocab become
    None in the per_q_results list. The averages skip Nones. The per_q
    lists returned at the end convert None → 0 (so per-question totals
    treat the Q as a 0, penalizing the candidate).
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

    # Expose per-question grammar/vocabulary so score_speaking() can compute
    # per-question totals. We use 0 instead of None for failed Qs because
    # the per-question total formula treats a failed Q as a 0-point Q
    # (candidate gets penalized for not speaking, not excluded from average).
    per_q_grammar_list = [
        r["grammar"] if r["grammar"] is not None else 0
        for r in per_q_results
    ]
    per_q_vocabulary_list = [
        r["vocabulary"] if r["vocabulary"] is not None else 0
        for r in per_q_results
    ]

    return {
        "grammar": grammar_avg,
        "vocabulary": vocab_avg,
        "per_q_grammar": per_q_grammar_list,
        "per_q_vocabulary": per_q_vocabulary_list,
        "feedback": feedback,
        "off_topic_qs": off_topic_qs,
    }


# ==================================================================
# Top-level orchestrator
# ==================================================================
def score_speaking(invitation: Invitation, db: Session) -> dict:
    """
    Top-level entry point for the speaking pipeline. Called from
    scoring._run_speaking_eval() once per invitation.

    Returns this dict (matches score_speaking_stub's shape so the pipeline
    is consistent regardless of whether the real eval ran):

        {
          "breakdown":     {"pronunciation": int|None, "fluency": int|None,
                            "grammar": int|None, "vocabulary": int|None,
                            "confidence": int|None},
          "total":         int,        # 0-100, the speaking_score
          "feedback":      str,        # one-paragraph HR-readable summary
          "off_topic_qs":  list[int],  # 1-indexed Q numbers that were off-topic
        }

    HOW THE FINAL TOTAL IS COMPUTED

    For each recording (Q1, Q2, Q3) we run all 4 stages and end up with 5
    dimension scores per question. We then:

        1. Apply rubric weights to each question's 5 dimensions → per-Q total
        2. Average those per-Q totals → speaking_score
        3. Subtract 15 points per off-topic question

    Edge cases:
        - No audio at all          → total=0, "no recordings submitted" feedback
        - Audio present, all stages failed → dimensions all None, total=0
        - Empty/silent audio (< 2KB)       → that Q's dims = 0, drags average down
        - Single dimension API failure    → that dim is None, renormalized over
                                              the dimensions that DID succeed

    See the module docstring for the full pipeline overview.
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

        # ---- Empty-audio short circuit ----
        # Skip Whisper/Azure/GPT entirely if the file is too small to contain real
        # audio. WebM container alone is ~200-800 bytes; a real recording is
        # 30KB+. The 2KB threshold catches "candidate clicked stop without
        # speaking" without false-rejecting valid short answers.
        file_size = audio_path.stat().st_size
        if file_size < MIN_AUDIO_BYTES:
            dbg("guard", f"audio file too small ({file_size} bytes < {MIN_AUDIO_BYTES}) — skipping all stages for this Q")
            failure_notes.append(f"Question {rec.id}: audio file too small ({file_size} bytes), candidate likely did not speak")
            transcripts.append("")
            rec.transcript = ""
            per_q_pron.append(0)
            per_q_fluency.append(0)
            per_q_confidence.append(0)
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
            # Blank the transcript at the LAST index so GPT-4o doesn't grade
            # whatever Whisper hallucinated on the silence (e.g.
            # "Transcribed by https://otter.ai"). Before this fix the per-Q
            # dimensions were zeroed but the hallucinated transcript still
            # got passed to GPT-4o for grammar/vocab grading.
            transcripts[-1] = ""
            rec.transcript = ""
            per_q_pron.append(0)
            per_q_fluency.append(0)
            per_q_confidence.append(0)
            continue

        # Whisper hallucination guard — catches silent-but-long recordings.
        #
        # The MIN_AUDIO_SECONDS check above only catches recordings under 5s.
        # A candidate who records 30 seconds of silence passes that gate, but
        # Whisper transcribes the silence as something like
        # "Transcribed by https://otter.ai" — a known training-data artefact.
        # Without this guard, that hallucination gets sent to GPT-4o, which
        # cheerfully scores it 67 grammar / 65 vocabulary because the string
        # IS grammatically valid. The candidate ends up with a non-zero
        # speaking score for having recorded background noise.
        #
        # We blank the transcript and zero the per-Q dimensions, same shape
        # as the short-audio path.
        if _is_whisper_hallucination(text):
            dbg("guard", f"Whisper hallucination detected for Q{q_idx}: {text!r} — zeroing all dimensions for this Q")
            failure_notes.append(f"Question {rec.id}: no usable speech detected (transcript was a Whisper hallucination)")
            transcripts[-1] = ""
            rec.transcript = ""
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
            # Same fix as above — exclude the (foreign-language) transcript
            # from GPT-4o grammar/vocab grading.
            transcripts[-1] = ""
            rec.transcript = ""
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
    per_q_grammar: list[int] = []
    per_q_vocab: list[int] = []
    llm_feedback = ""
    off_topic_qs: list[int] = []
    if any(t.strip() for t in transcripts):
        try:
            grading = grade_responses_with_gpt4o(transcripts, prompts_in_order)
            grammar_score = grading["grammar"]
            vocabulary_score = grading["vocabulary"]
            per_q_grammar = grading["per_q_grammar"]
            per_q_vocab = grading["per_q_vocabulary"]
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
            # Failed grading: zero out per-Q grammar/vocab so per-question totals
            # treat them as 0. Length matches the recordings count.
            per_q_grammar = [0] * len(recordings)
            per_q_vocab = [0] * len(recordings)
    else:
        dbg("gpt4o", "skipped (no usable transcripts)")
        failure_notes.append("No usable transcripts for grammar/vocabulary grading")
        per_q_grammar = [0] * len(recordings)
        per_q_vocab = [0] * len(recordings)

    # ---- Per-question totals (NEW APPROACH) ----
    # We previously averaged each dimension across questions, then applied rubric
    # weights once. Now we apply rubric weights per question, then average those
    # question totals. The math is the same when all dimensions succeed for all
    # questions, but per-question totals are what HR actually wants to see
    # (Q1=85, Q2=64, Q3=33) and they make the <50 floor rule semantically clear.
    #
    # Rules for a single failed dimension within a question:
    #   - None means the API call failed (Azure timeout, etc.). Skip from rubric,
    #     renormalize over the dimensions that succeeded.
    #   - 0 means the file was empty/hallucinated/non-English. Include as 0 so
    #     the candidate is penalized for not delivering a usable answer.
    def _question_total(pron, fluency, grammar, vocab, confidence) -> int:
        """
        Apply rubric weights to one question's 5 dimensions, returning a
        single 0-100 question total.

        None vs 0 handling:
            None = API call failed (Azure timeout, GPT-4o error, etc.).
                   That dimension is EXCLUDED from this question's total
                   and the remaining weights renormalize.
            0    = file was empty/hallucinated/non-English (caught by guards
                   in the per-Q loop). Included as 0 so the candidate is
                   penalized for not delivering a usable answer.

        Example: pron=88, fluency=90, grammar=85, vocab=80, confidence=82
            All dimensions succeeded:
                88×0.20 + 90×0.25 + 85×0.20 + 80×0.15 + 82×0.20 = 85.5 → 86

        Example: pron=88, fluency=None (Azure failed), rest the same
            Excludes fluency, renormalizes over remaining dimensions:
                (88×0.20 + 85×0.20 + 80×0.15 + 82×0.20) / 0.75 = 84
        """
        pairs = [
            (pron,       RUBRIC_WEIGHTS["pronunciation"]),
            (fluency,    RUBRIC_WEIGHTS["fluency"]),
            (grammar,    RUBRIC_WEIGHTS["grammar"]),
            (vocab,      RUBRIC_WEIGHTS["vocabulary"]),
            (confidence, RUBRIC_WEIGHTS["confidence"]),
        ]
        successful = [(v, w) for v, w in pairs if v is not None]
        if not successful:
            return 0
        weighted_sum = sum(v * w for v, w in successful)
        weight_used = sum(w for _, w in successful)
        return round(weighted_sum / weight_used)

    # Compute one total per question. The lists are guaranteed to be the same
    # length as `recordings` because every per-Q stage appends exactly once
    # (success path appends a value, failure paths append None or 0).
    per_question_totals: list[int] = []
    for i in range(len(recordings)):
        q_total = _question_total(
            per_q_pron[i],
            per_q_fluency[i],
            per_q_grammar[i] if i < len(per_q_grammar) else 0,
            per_q_vocab[i] if i < len(per_q_vocab) else 0,
            per_q_confidence[i],
        )
        per_question_totals.append(q_total)
        dbg("per-Q total", f"Q{i+1}: pron={per_q_pron[i]} fluency={per_q_fluency[i]} "
                          f"grammar={per_q_grammar[i] if i < len(per_q_grammar) else 0} "
                          f"vocab={per_q_vocab[i] if i < len(per_q_vocab) else 0} "
                          f"confidence={per_q_confidence[i]} → total={q_total}")

    # Final speaking score = average of question totals.
    # A failed question contributes 0, dragging the average down (intentional).
    if per_question_totals:
        total = round(mean(per_question_totals))
    else:
        total = 0

    # Breakdown for HR dashboard. Same structure as before so existing UI works.
    # Dimension values are averages across questions (None values excluded);
    # this is what the dashboard shows in the per-dimension bar chart.
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

    dbg("per-Q pron", str(per_q_pron))
    dbg("per-Q fluency", str(per_q_fluency))
    dbg("per-Q grammar", str(per_q_grammar))
    dbg("per-Q vocab", str(per_q_vocab))
    dbg("per-Q confidence", str(per_q_confidence))
    dbg("final", f"per-question totals: {per_question_totals}")
    dbg("final", f"speaking_score = mean({per_question_totals}) = {total}")

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