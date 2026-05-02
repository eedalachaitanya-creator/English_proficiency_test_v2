"""
Writing evaluation pipeline.

Replaces score_writing_stub() in scoring.py once an invitation has a WritingResponse.

Single GPT-4o call per essay. Returns a 5-dimension rubric (Grammar, Vocabulary,
Comprehension, Writing Quality, Professional Communication) — each 0..20,
summed to a 0..100 total — plus a short feedback paragraph for HR.

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

import content_gate
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
Score five dimensions, each on a 0-20 scale. The total (0-100) is the SUM of the five.

GRAMMAR (0-20): sentence-level correctness and clarity.
  18-20: consistent control of tense, agreement, articles, prepositions.
         Errors are rare and never block meaning.
  14-17: occasional mistakes (missing articles, tense slips) but meaning is
         always clear. Indian / non-native phrasing scores in this band when
         meaning is fully understandable.
   9-13: frequent grammar errors that occasionally force the reader to re-read.
   4-8 : errors so dense the reader has to guess intended meaning.
   0-3 : not formed sentences; meaning unrecoverable.

VOCABULARY (0-20): range and accuracy of word choice.
  18-20: precise word choice, varied register, uses topic-appropriate terms
         accurately ("articulate", "advocate", "synthesize", domain terms).
  14-17: solid working vocabulary, occasional repetition. Professional non-native
         writers normally land here.
   9-13: limited range; "good", "very", "thing" repeat; can't reach for precise
         words when needed.
   4-8 : very basic words only; cannot name common concepts.
   0-3 : cannot find words for basic ideas.

COMPREHENSION (0-20): does the essay address the prompt and engage with its substance?
  18-20: fully addresses every part of the prompt with relevant, developed ideas;
         the candidate clearly understood and engaged with the topic.
  14-17: addresses the prompt but leaves one part underdeveloped, OR ideas are
         present but a bit thin.
   9-13: partial response — answers some of the prompt and ignores the rest, OR
         answers it tangentially.
   4-8 : barely addresses the prompt; mostly off-topic or generic.
   0-3 : does not address the prompt (off-topic, refuses, or empty).

WRITING QUALITY (0-20): organization, flow, paragraphing, transitions, overall craft.
  18-20: clear thesis or position; ideas progress in a logical order; transitions
         guide the reader; paragraphs each have a focus; sentences vary in length
         and structure.
  14-17: organized overall but transitions could be smoother, or one idea is
         out of place; sentence rhythm is mostly even.
   9-13: ideas are present but order is hard to follow; weak or no transitions;
         monotonous sentence structure.
   4-8 : largely disorganized; reader has to assemble the meaning.
   0-3 : no discernible structure.

PROFESSIONAL COMMUNICATION (0-20): tone and register appropriate for a workplace
or business setting; would this writing be suitable in an email, report, or
proposal that the candidate sent to a colleague or client?
  18-20: consistently professional register; appropriate formality; respectful
         and clear; the writing could be sent to a colleague or external client
         without edits to tone.
  14-17: mostly professional, with one or two slips into casual or overly informal
         phrasing ("gonna", "stuff", excessive exclamation), but never disrespectful.
   9-13: register drifts noticeably — overly casual, vague hedging, or stiffly
         formal in a way that obscures meaning. Would need an edit pass before
         sending to a client.
   4-8 : inappropriate register for a workplace — text-message style, slang,
         disrespectful tone, or wildly inconsistent formality.
   0-3 : not workplace-appropriate writing at all.
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
        '  "grammar":                   <int 0-20>,\n'
        '  "vocabulary":                <int 0-20>,\n'
        '  "comprehension":             <int 0-20>,\n'
        '  "writing_quality":           <int 0-20>,\n'
        '  "professional_communication":<int 0-20>,\n'
        '  "feedback":                  "<3-4 sentences of feedback for the HR reviewer>"\n'
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


def _clamp_20(v) -> Optional[int]:
    """Coerce to int in [0, 20], or None if not parseable."""
    if v is None:
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return max(0, min(20, n))


# ------------------------------------------------------------------
# Top-level entrypoint — called from scoring._run_writing_eval()
# ------------------------------------------------------------------
def score_writing(invitation: Invitation, db: Session) -> dict:
    """
    Grade the candidate's essay for one invitation.

    Returns the SAME shape score_writing_stub returns:
      {
        "breakdown": {"grammar": int|None, "vocabulary": int|None,
                      "comprehension": int|None, "writing_quality": int|None,
                      "professional_communication": int|None},
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
                "grammar": None,
                "vocabulary": None,
                "comprehension": None,
                "writing_quality": None,
                "professional_communication": None,
            },
            "total": 0,
            "feedback": "No essay was submitted for this candidate.",
        }

    # Pre-LLM content gate. Cheap deterministic checks that short-circuit the
    # GPT-4o call when the essay is profane, gibberish, or non-English. Falls
    # open on its own crashes — see docs/superpowers/specs/2026-05-01-pre-llm-content-gate-design.md.
    try:
        gate = content_gate.check_text(wr.essay_text)
    except Exception:
        log.exception("content_gate crashed; falling through to LLM grading")
        gate = content_gate.GateResult(allowed=True, reason=None, rule=None)

    if not gate.allowed:
        dbg("gate", f"BLOCKED by rule={gate.rule}: {gate.reason}")
        log.info("content_gate blocked invitation %s rule=%s", invitation.id, gate.rule)
        return {
            "breakdown": {
                "grammar": None,
                "vocabulary": None,
                "comprehension": None,
                "writing_quality": None,
                "professional_communication": None,
            },
            "total": 0,
            "feedback": f"Skipped grading: {gate.reason}",
        }

    topic = db.query(WritingTopic).filter(WritingTopic.id == wr.topic_id).first()
    prompt_text = topic.prompt_text if topic else "(prompt unavailable)"
    dbg("essay", f"{wr.word_count} words, topic_id={wr.topic_id}")

    parsed = _grade_essay_with_gpt4o(prompt_text, wr.essay_text, wr.word_count)

    breakdown = {
        "grammar":                    _clamp_20(parsed.get("grammar")),
        "vocabulary":                 _clamp_20(parsed.get("vocabulary")),
        "comprehension":              _clamp_20(parsed.get("comprehension")),
        "writing_quality":            _clamp_20(parsed.get("writing_quality")),
        "professional_communication": _clamp_20(parsed.get("professional_communication")),
    }
    feedback = str(parsed.get("feedback", "")).strip()[:600]

    # Total = sum of the five 0-20 scores. If any dimension is None (rare), it
    # contributes 0 — we'd rather show a conservative score than refuse to score.
    total = sum(v if v is not None else 0 for v in breakdown.values())

    dbg(
        "result",
        f"grammar={breakdown['grammar']} vocab={breakdown['vocabulary']} "
        f"comprehension={breakdown['comprehension']} "
        f"writing_quality={breakdown['writing_quality']} "
        f"prof_comm={breakdown['professional_communication']} "
        f"total={total}",
    )

    return {
        "breakdown": breakdown,
        "total": total,
        "feedback": feedback or "(no feedback returned)",
    }
