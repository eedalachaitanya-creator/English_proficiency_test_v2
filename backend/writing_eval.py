"""
Writing evaluation pipeline.

Replaces score_writing_stub() in scoring.py once an invitation has a WritingResponse.

Single GPT-4o call per essay. Returns a 4-dimension rubric (Task Response,
Grammar, Vocabulary, Coherence) — each 0..25, summed to a 0..100 total — plus
a short feedback paragraph for HR.

Failure handling: any unexpected error bubbles up; scoring._run_writing_eval()
catches it and falls back to the stub so a single bad essay doesn't poison
the whole submission.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Optional

from sqlalchemy.orm import Session

from models import Invitation, WritingResponse, WritingTopic

log = logging.getLogger("writing_eval")


# Toggle stdout debug printing the same way speaking_eval does it.
# Set WRITING_DEBUG=0 to silence.
_DEBUG_ON = os.getenv("WRITING_DEBUG", "1") != "0"


def dbg(prefix: str, message: str = "") -> None:
    if not _DEBUG_ON:
        return
    if message:
        print(f"  [{prefix}] {message}", flush=True)
    else:
        print(f"  [{prefix}]", flush=True)


def dbg_section(title: str) -> None:
    if not _DEBUG_ON:
        return
    print(f"\n{title}", flush=True)


# ------------------------------------------------------------------
# Rubric anchors — same philosophy as speaking_eval: score for clarity
# and communicative effectiveness, not adherence to a single English variant.
# Indian / Singaporean / etc. phrasing is treated as legitimate.
# ------------------------------------------------------------------
_RUBRIC = """
Score four dimensions, each on a 0-25 scale. The total (0-100) is the SUM of the four.

TASK RESPONSE (0-25): does the essay address the prompt?
  22-25: fully addresses every part of the prompt with relevant, developed ideas;
         the candidate clearly engaged with the topic.
  17-21: addresses the prompt but leaves one part underdeveloped, OR ideas are
         present but a bit thin.
  11-16: partial response — answers some of the prompt and ignores the rest, OR
         answers it tangentially.
   5-10: barely addresses the prompt; mostly off-topic or generic.
   0-4 : does not address the prompt (off-topic, refuses, or empty).

GRAMMAR (0-25): sentence-level correctness and clarity.
  22-25: consistent control of tense, agreement, articles, prepositions.
         Errors are rare and never block meaning.
  17-21: occasional mistakes (missing articles, tense slips) but meaning is
         always clear. Indian / non-native phrasing scores in this band when
         meaning is fully understandable.
  11-16: frequent grammar errors that occasionally force the reader to re-read.
   5-10: errors so dense the reader has to guess intended meaning.
   0-4 : not formed sentences; meaning unrecoverable.

VOCABULARY (0-25): range and accuracy of word choice.
  22-25: precise word choice, varied register, uses topic-appropriate terms
         accurately ("articulate", "advocate", "synthesize", domain terms).
  17-21: solid working vocabulary, occasional repetition. Professional non-native
         writers normally land here.
  11-16: limited range; "good", "very", "thing" repeat; can't reach for precise
         words when needed.
   5-10: very basic words only; cannot name common concepts.
   0-4 : cannot find words for basic ideas.

COHERENCE (0-25): organization, flow, paragraphing, transitions.
  22-25: clear thesis or position; ideas progress in a logical order; transitions
         guide the reader; paragraphs each have a focus.
  17-21: organized overall but transitions could be smoother, or one idea is
         out of place.
  11-16: ideas are present but order is hard to follow; weak or no transitions.
   5-10: largely disorganized; reader has to assemble the meaning.
   0-4 : no discernible structure.
""".strip()


# ------------------------------------------------------------------
# Single GPT-4o grading call
# ------------------------------------------------------------------
def _grade_essay_with_gpt4o(prompt_text: str, essay_text: str, word_count: int) -> dict:
    """
    Send one (prompt, essay) pair to GPT-4o. Returns the parsed JSON with
    breakdown + feedback. Raises RuntimeError on bad output / no API key.
    """
    from openai import OpenAI

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    client = OpenAI(api_key=api_key)

    system_msg = (
        "You are an English proficiency assessor for job recruitment. You grade "
        "ONE written essay. The candidate may be a native or non-native English "
        "speaker; you score for CLARITY AND COMMUNICATIVE EFFECTIVENESS, not for "
        "adherence to native phrasing. Indian, Singaporean, and other regional "
        "English variants are treated as legitimate English; do not penalize for "
        "non-native phrasing as long as meaning is clear.\n\n"
        + _RUBRIC + "\n\n"
        "Return ONLY a JSON object with this exact shape:\n"
        "{\n"
        '  "task_response": <int 0-25>,\n'
        '  "grammar":       <int 0-25>,\n'
        '  "vocabulary":    <int 0-25>,\n'
        '  "coherence":     <int 0-25>,\n'
        '  "feedback":      "<3-4 sentences of feedback for the HR reviewer>"\n'
        "}\n\n"
        "feedback guidance: 3-4 sentences, ≤ 80 words. Mention the strongest and "
        "weakest dimension, with one specific example from the essay. Address it "
        "to the HR reviewer (third person about the candidate). No score recap. "
        "No generic praise. Return only the JSON, nothing else."
    )

    user_msg = (
        f"PROMPT:\n{prompt_text}\n\n"
        f"ESSAY ({word_count} words):\n{essay_text}\n\n"
        "Grade this essay."
    )

    t0 = time.time()
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
        max_tokens=400,
    )
    elapsed = time.time() - t0

    raw = response.choices[0].message.content or "{}"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        raise RuntimeError(f"GPT-4o returned non-JSON for writing: {raw[:200]}")

    dbg("gpt4o", f"essay graded in {elapsed:.1f}s")
    return parsed


def _clamp_25(v) -> Optional[int]:
    """Coerce to int in [0, 25], or None if not parseable."""
    if v is None:
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return max(0, min(25, n))


# ------------------------------------------------------------------
# Top-level entrypoint — called from scoring._run_writing_eval()
# ------------------------------------------------------------------
def score_writing(invitation: Invitation, db: Session) -> dict:
    """
    Grade the candidate's essay for one invitation.

    Returns the SAME shape score_writing_stub returns:
      {
        "breakdown": {"task_response": int|None, "grammar": int|None,
                      "vocabulary": int|None, "coherence": int|None},
        "total": int|None,    # sum of breakdown, 0..100
        "feedback": str,
      }

    Cases:
      - No essay submitted → total=0, feedback explains.
      - Essay present but topic missing (data corruption) → returns None scores.
      - GPT-4o call fails / returns garbage → raises; caller's try/except
        falls back to score_writing_stub.
    """
    dbg_section(f"================ WRITING EVAL: invitation {invitation.id} ================")

    wr: Optional[WritingResponse] = invitation.writing_response
    if wr is None or not wr.essay_text:
        dbg("end", "no essay on file — returning total=0")
        return {
            "breakdown": {
                "task_response": None,
                "grammar": None,
                "vocabulary": None,
                "coherence": None,
            },
            "total": 0,
            "feedback": "No essay was submitted for this candidate.",
        }

    topic = db.query(WritingTopic).filter(WritingTopic.id == wr.topic_id).first()
    prompt_text = topic.prompt_text if topic else "(prompt unavailable)"
    dbg("essay", f"{wr.word_count} words, topic_id={wr.topic_id}")

    parsed = _grade_essay_with_gpt4o(prompt_text, wr.essay_text, wr.word_count)

    breakdown = {
        "task_response": _clamp_25(parsed.get("task_response")),
        "grammar":       _clamp_25(parsed.get("grammar")),
        "vocabulary":    _clamp_25(parsed.get("vocabulary")),
        "coherence":     _clamp_25(parsed.get("coherence")),
    }
    feedback = str(parsed.get("feedback", "")).strip()[:600]

    # Total = sum of the four 0-25 scores. If any dimension is None (rare), it
    # contributes 0 — we'd rather show a conservative score than refuse to score.
    total = sum(v if v is not None else 0 for v in breakdown.values())

    dbg(
        "result",
        f"task={breakdown['task_response']} grammar={breakdown['grammar']} "
        f"vocab={breakdown['vocabulary']} coherence={breakdown['coherence']} "
        f"total={total}",
    )

    return {
        "breakdown": breakdown,
        "total": total,
        "feedback": feedback or "(no feedback returned)",
    }
