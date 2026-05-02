"""
Tests for content_gate.check_text() — the pre-LLM writing gate.

See docs/superpowers/specs/2026-05-01-pre-llm-content-gate-design.md for the full
spec including thresholds and rationale.
"""
from content_gate import check_text, GateResult


# ------------------------------------------------------------------
# Cycle 1 — module exists; normal essay is allowed
# ------------------------------------------------------------------
def test_normal_essay_is_allowed():
    essay = (
        "I once had a disagreement with a colleague about which framework to use for a new project. "
        "Rather than escalating, I scheduled a one-on-one meeting where we walked through the trade-offs "
        "of each option together. By the end we agreed on a hybrid approach that combined the strengths "
        "of both. The experience taught me that calm communication resolves most conflicts."
    )
    result = check_text(essay)
    assert isinstance(result, GateResult)
    assert result.allowed is True
    assert result.reason is None
    assert result.rule is None


# ------------------------------------------------------------------
# Cycle 2 — profanity is blocked
# ------------------------------------------------------------------
def test_blocks_essay_containing_profanity():
    essay = (
        "I think this whole test is fucking pointless and I do not want to do it. "
        "There is no reason for me to write this essay because I do not believe "
        "in workplace evaluations of language skills. Just hire people based on "
        "their actual job performance instead of these contrived assessments."
    )
    result = check_text(essay)
    assert result.allowed is False
    assert result.rule == "profanity"
    assert result.reason and "language" in result.reason.lower()


# ------------------------------------------------------------------
# Cycle 3 — Scunthorpe problem: profanity matching must use word
# boundaries so substring overlap with normal English words does not
# false-positive. Patches the wordlist so the test is hermetic regardless
# of which entries are in production.
# ------------------------------------------------------------------
def test_does_not_flag_normal_word_containing_profanity_substring(monkeypatch):
    import content_gate
    monkeypatch.setattr(content_gate, "_PROFANITY_WORDS", frozenset({"cunt"}))
    essay = (
        "I once worked on a project for a client based in Scunthorpe. "
        "The Scunthorpe office was welcoming and the Scunthorpe team was professional. "
        "Working with the Scunthorpe colleagues taught me a lot about cross-site collaboration."
    )
    result = check_text(essay)
    assert result.allowed is True, (
        "Scunthorpe contains 'cunt' as substring — profanity check must use "
        "word boundaries (\\b) to avoid false-positives."
    )


# ------------------------------------------------------------------
# Cycle 4 — leetspeak normalization. Common digit / symbol substitutions
# (! → i, @ → a, 0 → o, etc.) should be normalized before matching so
# casual evasion ("sh!t", "b1tch") still trips the gate.
# ------------------------------------------------------------------
def test_blocks_leetspeak_substitution(monkeypatch):
    import content_gate
    monkeypatch.setattr(content_gate, "_PROFANITY_WORDS", frozenset({"shit"}))
    essay = (
        "This essay is sh!t and I am not going to write more than the bare minimum. "
        "Padding to keep the word count above the floor that the submit endpoint "
        "enforces upstream so that the gate sees a normal length text and not "
        "an artificially short one that would be rejected at submit time anyway."
    )
    result = check_text(essay)
    assert result.allowed is False
    assert result.rule == "profanity"


# ------------------------------------------------------------------
# Cycle 5 — wrong-script: an essay written entirely in Devanagari
# should be blocked. Threshold per spec: <70% Latin letters → flag.
# ------------------------------------------------------------------
def test_blocks_essay_in_devanagari():
    essay = (
        "मैं एक बार अपने सहकर्मी के साथ एक विवाद में पड़ गया था। "
        "हमने शांति से बात की और समाधान निकाला। यह अनुभव मुझे बहुत कुछ सिखा गया। "
        "अब मैं संघर्ष से नहीं डरता क्योंकि मुझे पता है कि बातचीत से सब कुछ हल हो सकता है।"
    )
    result = check_text(essay)
    assert result.allowed is False
    assert result.rule == "wrong_script"
    assert result.reason and "english" in result.reason.lower()


# ------------------------------------------------------------------
# Cycle 6 — wrong-script false-positive guard: an English essay that
# quotes one short Hindi phrase should still pass (≥70% Latin).
# ------------------------------------------------------------------
def test_allows_english_essay_with_short_hindi_quote():
    essay = (
        "I once had a disagreement with a colleague about which framework to use for a new project. "
        "Rather than escalating, I scheduled a one-on-one meeting where we walked through the trade-offs "
        "of each option together. As my mentor used to say, मेहनत करो — keep working hard. "
        "By the end we agreed on a hybrid approach that combined the strengths of both."
    )
    result = check_text(essay)
    assert result.allowed is True


# ------------------------------------------------------------------
# Cycle 7 — keyboard mashing: an essay where most tokens are
# vowel-less or keyboard-row patterns should be blocked.
# ------------------------------------------------------------------
def test_blocks_keyboard_mashing():
    # 60+ tokens, almost all of which are vowel-less or keyboard-row patterns.
    mash_tokens = ["asdfg", "qwerty", "hjklhjkl", "qwertyuiop", "asdfasdf",
                   "zxcvbnm", "ghjkl", "qwerasdf", "rtyuhjkl", "zxcvbn",
                   "fghjkl", "wertyuio", "sdfghjk", "vbnmzxcv"]
    essay = " ".join(mash_tokens * 6)  # 84 tokens, all mashed
    result = check_text(essay)
    assert result.allowed is False
    assert result.rule == "keyboard_mash"
    assert result.reason and "gibberish" in result.reason.lower()


# ------------------------------------------------------------------
# Cycle 8 — keyboard-mash false-positive guard: legitimate technical
# terms (javascript, typescript, etc.) must not trip the mash check.
# ------------------------------------------------------------------
def test_allows_essay_with_technical_terms():
    essay = (
        "I work as a software engineer using javascript and typescript daily. "
        "I have debugged complex backend issues in python flask applications and "
        "deployed services to kubernetes clusters in production environments. "
        "My experience with postgresql, redis, and rabbitmq has been invaluable "
        "for designing distributed systems that scale reliably under load."
    )
    result = check_text(essay)
    assert result.allowed is True


# ------------------------------------------------------------------
# Cycle 9 — token dominance: any single len≥3 token making up >25%
# of all tokens flags the essay. Common short words (the/and/is) are
# exempt due to len≥3 actually applying — wait, the/and ARE len≥3.
# Spec exempts via len≥3 cutoff. So the exemption is only for 1-2 char
# words: "is", "a", etc. "the" CAN trigger if it dominates.
# ------------------------------------------------------------------
def test_blocks_token_dominance():
    # 80 tokens total, "table" appears 30 times (37.5%) — over the 25% threshold.
    essay = ("table " * 30 + "chair desk lamp window door floor ceiling wall office room "
             "computer keyboard mouse monitor screen camera microphone speaker tablet phone "
             "pen paper notebook folder binder stapler scissors ruler eraser pencil "
             "calendar clock calculator printer scanner ").strip()
    result = check_text(essay)
    assert result.allowed is False
    assert result.rule == "repetition"
    assert result.reason and "gibberish" in result.reason.lower()


# ------------------------------------------------------------------
# Cycle 10 — bigram repetition: the same 2-token sequence appearing
# >5 times in total flags the essay even when no single token dominates.
# ------------------------------------------------------------------
def test_blocks_bigram_repetition():
    # "table chair" appears 8 times — over the >5 threshold; no single
    # token dominates because table and chair each ~33%.
    essay = ("table chair " * 8 + "desk lamp window door floor ceiling wall office room "
             "computer keyboard mouse monitor screen camera microphone speaker tablet").strip()
    result = check_text(essay)
    assert result.allowed is False
    assert result.rule == "repetition"


# ------------------------------------------------------------------
# Edge case — empty / whitespace input must not crash. The HARD_FLOOR_WORDS
# check at submit time already guarantees ≥50 words by the time the gate
# runs; this is defensive only.
# ------------------------------------------------------------------
def test_empty_text_does_not_crash():
    assert check_text("").allowed is True
    assert check_text("   \n\n  ").allowed is True


# ------------------------------------------------------------------
# Integration — when content_gate blocks an essay, score_writing()
# must (a) return total=0 with the gate's reason in feedback, and
# (b) NOT call the GPT-4o grader (this is the cost-saving point).
# ------------------------------------------------------------------
def test_score_writing_skips_openai_when_gate_blocks(monkeypatch):
    from unittest.mock import MagicMock
    import writing_eval

    # Track whether the GPT-4o grader is invoked. If it is, the gate failed
    # to short-circuit and we are wasting API spend.
    grader_called = []

    def fake_grade(*args, **kwargs):
        grader_called.append(True)
        return {
            "grammar": 10, "vocabulary": 10, "comprehension": 10,
            "writing_quality": 10, "professional_communication": 10,
            "feedback": "should not be returned",
        }

    monkeypatch.setattr(writing_eval, "_grade_essay_with_gpt4o", fake_grade)

    invitation = MagicMock()
    invitation.id = 999
    invitation.writing_response.essay_text = (
        "I think this whole test is fucking pointless and I do not want to do it. "
        "Padding to keep the word count above the floor that the submit endpoint "
        "enforces upstream so that the gate sees a normal length text."
    )
    invitation.writing_response.word_count = 35
    invitation.writing_response.topic_id = 1

    result = writing_eval.score_writing(invitation, MagicMock())

    assert grader_called == [], (
        "GPT-4o was called even though the gate should have blocked the essay"
    )
    assert result["total"] == 0
    assert result["breakdown"] == {
        "grammar": None, "vocabulary": None, "comprehension": None,
        "writing_quality": None, "professional_communication": None,
    }
    assert "Skipped grading" in result["feedback"]
    assert "language" in result["feedback"].lower()
