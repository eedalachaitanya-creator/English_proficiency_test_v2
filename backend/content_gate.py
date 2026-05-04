"""
Pre-LLM content gate for the Writing section.

See docs/superpowers/specs/2026-05-01-pre-llm-content-gate-design.md.

Pure module — no I/O, no API calls, no DB. One public function: check_text().
Reused for speaking transcripts in a later phase.
"""
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass


@dataclass
class GateResult:
    """
    Outcome of a single content_gate check.

    - allowed=True, reason=None, rule=None  → text passes; safe to send to LLM.
    - allowed=False, reason=str, rule=str   → text rejected; skip LLM. The
      reason is shown verbatim to HR; the rule is the machine tag for logs.
    """
    allowed: bool
    reason: str | None
    rule: str | None


# ------------------------------------------------------------------
# Profanity wordlist — English strong profanity + clear-cut slurs.
# Grown over time; deliberately not enumerated in the design doc.
# ------------------------------------------------------------------
_PROFANITY_WORDS: frozenset[str] = frozenset({
    "fucking",
})

# Leetspeak substitutions applied before re-matching. Catches casual evasion
# ("sh!t", "b1tch") without trying to win an arms race against Cyrillic
# look-alikes, spaced-out letters, or zero-width characters.
_LEET_TABLE = str.maketrans({
    "@": "a", "!": "i", "0": "o", "1": "i",
    "3": "e", "4": "a", "5": "s", "$": "s",
})


def _has_profanity(text: str) -> bool:
    if not _PROFANITY_WORDS:
        return False
    pattern = r"\b(?:" + "|".join(re.escape(w) for w in _PROFANITY_WORDS) + r")\b"
    lowered = text.lower()
    if re.search(pattern, lowered):
        return True
    normalized = lowered.translate(_LEET_TABLE)
    return bool(re.search(pattern, normalized))


# ------------------------------------------------------------------
# Wrong-script: ≥30% non-Latin letter codepoints → flag.
# ------------------------------------------------------------------
from config import LATIN_RATIO_FLOOR as _LATIN_RATIO_FLOOR  # essays with <70% Latin letters are blocked


def _is_wrong_script(text: str) -> bool:
    latin = 0
    non_latin = 0
    for ch in text:
        if not ch.isalpha():
            continue  # skip whitespace, digits, punctuation
        # unicodedata.name returns e.g. "LATIN SMALL LETTER A" or
        # "DEVANAGARI LETTER MA". Treat absence of a name as non-Latin.
        try:
            name = unicodedata.name(ch)
        except ValueError:
            non_latin += 1
            continue
        if name.startswith("LATIN"):
            latin += 1
        else:
            non_latin += 1
    total = latin + non_latin
    if total == 0:
        return False  # no letters at all — not the gate's job to judge
    return (latin / total) < _LATIN_RATIO_FLOOR


# ------------------------------------------------------------------
# Keyboard-mashing: a token is "mashed" if length ≥ 4 AND (no vowels
# OR contains a 4-char window matching a keyboard row).
# Essay flagged if >30% of tokens are mashed.
#
# Note: the design spec used 3-char windows, but that false-positives on
# common workplace words (alert/expert/insert all contain 'ert' — a 3-char
# substring of the qwerty row). Tightened to 4 chars during implementation.
# ------------------------------------------------------------------
_VOWELS = frozenset("aeiouy")
_KEYBOARD_ROWS = ("qwertyuiop", "asdfghjkl", "zxcvbnm")
from config import MASH_TOKEN_RATIO as _MASH_TOKEN_RATIO


def _build_keyboard_windows() -> frozenset[str]:
    out: set[str] = set()
    for row in _KEYBOARD_ROWS:
        for sequence in (row, row[::-1]):
            for i in range(len(sequence) - 3):
                out.add(sequence[i:i + 4])
    return frozenset(out)


_KEYBOARD_WINDOWS = _build_keyboard_windows()


def _is_mashed_token(token: str) -> bool:
    if len(token) < 4:
        return False
    if not any(ch in _VOWELS for ch in token):
        return True
    for i in range(len(token) - 3):
        if token[i:i + 4] in _KEYBOARD_WINDOWS:
            return True
    return False


def _is_keyboard_mash(text: str) -> bool:
    tokens = re.findall(r"[a-z]+", text.lower())
    if not tokens:
        return False
    mashed = sum(1 for t in tokens if _is_mashed_token(t))
    return (mashed / len(tokens)) > _MASH_TOKEN_RATIO


# ------------------------------------------------------------------
# Pure-repetition: token dominance OR bigram repetition.
#   - Any single token of len ≥ 3 making up >25% of tokens → flag.
#   - Same 2-token sequence appearing >5 times in total → flag.
# Common short function words ("a", "is") are exempt via the len ≥ 3 cutoff.
# ------------------------------------------------------------------
from config import (
    TOKEN_DOMINANCE_RATIO as _TOKEN_DOMINANCE_RATIO,
    DOMINANCE_MIN_TOKEN_LEN as _DOMINANCE_MIN_TOKEN_LEN,
    BIGRAM_REPEAT_LIMIT as _BIGRAM_REPEAT_LIMIT,
)


def _is_pure_repetition(text: str) -> bool:
    tokens = re.findall(r"[a-z]+", text.lower())
    if not tokens:
        return False

    counts = Counter(t for t in tokens if len(t) >= _DOMINANCE_MIN_TOKEN_LEN)
    if counts:
        most_common_count = counts.most_common(1)[0][1]
        if most_common_count / len(tokens) > _TOKEN_DOMINANCE_RATIO:
            return True

    if len(tokens) >= 2:
        bigrams = Counter(zip(tokens, tokens[1:]))
        if bigrams.most_common(1)[0][1] > _BIGRAM_REPEAT_LIMIT:
            return True

    return False


def check_text(text: str) -> GateResult:
    """
    Run all content checks against an essay. Short-circuits on first failure.
    See spec for the four detection rules and their thresholds.
    """
    if _has_profanity(text):
        return GateResult(
            allowed=False,
            reason="Inappropriate language detected.",
            rule="profanity",
        )
    if _is_wrong_script(text):
        return GateResult(
            allowed=False,
            reason="Essay is not in English.",
            rule="wrong_script",
        )
    if _is_keyboard_mash(text):
        return GateResult(
            allowed=False,
            reason="Submission appears to be gibberish (keyboard mashing).",
            rule="keyboard_mash",
        )
    if _is_pure_repetition(text):
        return GateResult(
            allowed=False,
            reason="Submission appears to be gibberish (excessive repetition).",
            rule="repetition",
        )
    return GateResult(allowed=True, reason=None, rule=None)
