"""
Centralized engineering configuration.

This file is the single source of truth for tunable constants that engineers
or product owners may need to change as client requirements evolve. The aim:
edit here, restart the backend, done — no hunting through the codebase.

Three layers of configuration in this project — pick the right one:

  1. backend/.env (environment / secrets)
     For: per-environment URLs, DB credentials, API keys, SMTP settings,
          things that differ between dev / staging / production.
     Edit:  edit the file → restart backend.

  2. backend/config.py (THIS FILE — engineering defaults)
     For: thresholds, weights, tunable algorithm parameters, application
          behavior knobs that are the same across environments but vary by
          client requirement (e.g., "raise the essay minimum to 75 words").
     Edit:  edit the file → restart backend.

  3. system_settings DB table (operational runtime config)
     For: per-invitation values HR controls. Snapshotted onto each Invitation
          at creation, so changes only affect future invitations:
            * max_starts (allowed URL opens)
            * reading_seconds, writing_seconds, speaking_seconds
     Edit:  UPDATE system_settings SET … WHERE id = 1;  (no restart)

If you add a new knob: ask whether it varies by environment (→ .env), by
client requirement (→ here), or by HR's day-to-day operations (→ DB).
"""

# ============================================================
# Authentication / lockout
# ============================================================

# How many wrong access-code submissions a candidate is allowed before the
# invitation auto-locks. The lockout is per-invitation; HR has to issue a
# fresh invitation (or reset code_locked manually) to recover.
MAX_CODE_ATTEMPTS = 3

# Seconds of clock-skew tolerance allowed when validating that an HR-chosen
# window's start time is "not in the past." Without this, a "right now" picked
# in the browser would routinely fail validation if the backend clock is even
# slightly ahead.
PAST_GRACE_SECONDS = 60

# Smallest scheduled-window length HR is allowed to pick. Must be at least the
# total test budget (reading + writing + speaking) so the window can actually
# fit the test the candidate is being asked to take.
MIN_WINDOW_SECONDS = 60 * 60   # 60 minutes


# ============================================================
# Test content
# ============================================================

# How many MCQ questions get assigned to each candidate's reading section
# from the question bank. Increase if the bank gets larger and you want
# longer assessments.
WRITTEN_QUESTIONS_PER_TEST = 15

# How many speaking topics get assigned to each candidate.
SPEAKING_QUESTIONS_PER_TEST = 3


# ============================================================
# Submission
# ============================================================

# Hard floor on essay length — submissions under this many words are rejected
# at submit time AND at the writing-page Continue button (frontend mirrors
# this value). 50 is small enough to permit a 3-4 sentence response but
# large enough to filter out empty / token submissions.
HARD_FLOOR_WORDS = 50


# ============================================================
# Scoring — total-score weights
# ============================================================
# Section weights (must sum to 1.0). Equal weighting across the three sections.
# The compute_total redistribution code path is mathematically equivalent to a
# simple mean under equal weights — kept in case the weights change again.

W_READING = 1 / 3
W_WRITING = 1 / 3
W_SPEAKING = 1 / 3


# ============================================================
# Speaking evaluation
# ============================================================

# Audio recordings shorter than this (in seconds) are scored 0 across all
# dimensions and excluded from averaging — treated as "not a real attempt."
# Catches accidental clicks and silent recordings.
MIN_AUDIO_SECONDS = 5.0

# Per-dimension weights for the speaking rubric. Must sum to 1.0 — asserted
# at speaking_eval module load time. Adjusting these changes the relative
# importance of pronunciation vs fluency vs grammar etc. for the speaking
# total score.
SPEAKING_RUBRIC_WEIGHTS = {
    "pronunciation": 0.20,
    "fluency":       0.25,
    "grammar":       0.20,
    "vocabulary":    0.15,
    "confidence":    0.20,
}


# ============================================================
# Pre-LLM content gate (writing essay sanity checks)
# ============================================================
# Thresholds for the cheap heuristic checks that run before the GPT-4o
# call — see docs and content_gate.py for full rationale of each value.

# Wrong-script: essays with fewer than this fraction of Latin letters are
# blocked from grading. 0.70 leaves headroom for legitimate quoting of
# non-Latin phrases ("मेहनत करो") inside an English essay.
LATIN_RATIO_FLOOR = 0.70

# Keyboard-mash: essay flagged as gibberish if more than this fraction of
# its tokens look like keyboard-row mashing.
MASH_TOKEN_RATIO = 0.30

# Pure-repetition: essay flagged if any single token (length ≥ 3) makes up
# more than this fraction of all tokens. Length-3 cutoff exempts natural
# function words like "the" / "and".
TOKEN_DOMINANCE_RATIO = 0.25
DOMINANCE_MIN_TOKEN_LEN = 3

# Bigram repetition: essay flagged if the same 2-token sequence appears
# more than this many times across the essay (sliding window).
BIGRAM_REPEAT_LIMIT = 5


# ============================================================
# system_settings fallback (when the DB row is missing)
# ============================================================
# Used by routes.hr._settings_to_dict when the DB row is absent (fresh DB,
# missed migration, or row deleted). Should mirror the migration's seed
# defaults exactly — drift here means a fresh-DB invitation gets different
# behavior than a migrated-DB one.

FALLBACK_MAX_STARTS = 1
FALLBACK_READING_SECONDS = 30 * 60
FALLBACK_WRITING_SECONDS = 20 * 60
FALLBACK_SPEAKING_SECONDS = 10 * 60
