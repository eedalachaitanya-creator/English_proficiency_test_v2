"""PDF report generator for a single candidate's assessment results.

Called from routes/hr_reports.py via build_candidate_pdf(). Returns the PDF
as bytes (caller wraps in a StreamingResponse).

Layout: McKinsey/consulting-deliverable style.
  - Left-aligned title block
  - Navy section bars between sections
  - Full-width tables with navy headers
  - Page header strip + footer on every page
  - Verdict shown only in the Section Summary table (no prose lede)

Palette: navy / white / light gray. No orange, no SaaS-style elements.
ATS-compatible Helvetica throughout.

Function signature is identical to the previous pdf_report.py — drop-in
replacement. routes/hr_reports.py does not need to change.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from io import BytesIO
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether,
    HRFlowable,
)
from reportlab.lib.utils import ImageReader

from models import Invitation, Score, AudioRecording, WritingResponse, WritingTopic, SpeakingTopic


# ============================================================
# LOGO — path to fluentiq_black.png, placed next to this file.
# If the file is missing the page header falls back to a text wordmark
# so the report still generates cleanly.
# ============================================================
LOGO_PATH = os.path.join(os.path.dirname(__file__), "fluentiq_black.png")
LOGO_WIDTH_MM = 32     # rendered width on the page
LOGO_HEIGHT_MM = 6.84  # 32 / 4.677 — preserves aspect ratio of 290x62 source

# ============================================================
# PALETTE — navy / white / light gray. No orange, no decorative colors.
# Status colors are ONLY used on the rating word — nowhere else.
# ============================================================
NAVY        = colors.HexColor("#0b2545")        # primary
NAVY_LIGHT  = colors.HexColor("#1d3a6e")        # softer navy for subheads
INK         = colors.HexColor("#1a1a1a")        # primary text
INK_SOFT    = colors.HexColor("#3a3a3a")        # secondary text
MUTED       = colors.HexColor("#6a7280")        # labels
LIGHT_GRAY  = colors.HexColor("#f5f7fa")        # row fills, banner background
RULE        = colors.HexColor("#d0d7e2")        # table borders, dividers
WHITE       = colors.white

# Status colors — used ONLY for the rating verdict word.
RATING_GREEN = colors.HexColor("#1f7a3a")
RATING_AMBER = colors.HexColor("#a86a00")
RATING_RED   = colors.HexColor("#a02828")


# ============================================================
# Rating bands — mirror scoring.derive_rating()
# ============================================================
RATING_BANDS = [
    ("Recommended",     70, 100),
    ("Borderline",      50, 69),
    ("Not recommended",  0, 49),
]


# ============================================================
# Writing/Speaking dimension definitions
# ============================================================
WRITING_DIMS = [
    ("grammar",                    "Grammar",                    20),
    ("vocabulary",                 "Vocabulary",                 20),
    ("comprehension",              "Comprehension",              20),
    ("writing_quality",            "Writing quality",            20),
    ("professional_communication", "Professional communication", 20),
]

SPEAKING_DIMS = [
    ("pronunciation", "Pronunciation"),
    ("fluency", "Fluency"),
    ("grammar", "Grammar"),
    ("vocabulary", "Vocabulary"),
    ("confidence", "Confidence"),
]


# ============================================================
# Formatters
# ============================================================
def fmt_score(value: Optional[int]) -> str:
    return f"{value}" if value is not None else "—"


def fmt_dt(dt, target_tz_name=None, tz_label=None) -> str:
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if not target_tz_name or target_tz_name == "UTC":
        return dt.strftime("%Y-%m-%d %H:%M") + " (UTC)"
    try:
        target = ZoneInfo(target_tz_name)
        local = dt.astimezone(target)
        label = tz_label or target_tz_name
        return local.strftime("%Y-%m-%d %H:%M") + f" ({label})"
    except ZoneInfoNotFoundError:
        return dt.strftime("%Y-%m-%d %H:%M") + " (UTC)"


def fmt_date(dt, target_tz_name=None) -> str:
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if target_tz_name and target_tz_name != "UTC":
        try:
            dt = dt.astimezone(ZoneInfo(target_tz_name))
        except ZoneInfoNotFoundError:
            pass
    return dt.strftime("%d %B %Y")


def fmt_duration(seconds: Optional[int]) -> str:
    if seconds is None:
        return "—"
    if seconds == 0:
        return "0s"
    m = seconds // 60
    s = seconds % 60
    if m == 0:
        return f"{s}s"
    if s == 0:
        return f"{m} minute{'s' if m != 1 else ''}"
    return f"{m}m {s}s"


def reference_id(inv) -> str:
    token_part = (inv.token or "")[:6].upper() or "------"
    return f"EPT-{inv.id:05d}-{token_part}"


def format_submission_reason(reason: Optional[str]) -> str:
    if not reason:
        return "—"
    mapping = {
        "candidate_finished": "Completed normally",
        "candidate_submit": "Completed normally",
        "tab_switch_termination": "Terminated (tab-switch limit)",
        "tab_switch_limit": "Terminated (tab-switch limit)",
        "reading_timer_expired": "Auto-submitted (reading time expired)",
        "writing_timer_expired": "Auto-submitted (writing time expired)",
        "speaking_timer_expired": "Auto-submitted (speaking time expired)",
        "window_expired": "Auto-submitted (window expired)",
    }
    return mapping.get(reason, reason.replace("_", " ").title())


def rating_label(rating: Optional[str]) -> str:
    return {
        "recommended": "RECOMMENDED",
        "borderline": "BORDERLINE",
        "not_recommended": "NOT RECOMMENDED",
    }.get(rating, "NOT YET SCORED")


def rating_color(rating):
    if rating == "recommended":
        return RATING_GREEN
    if rating == "borderline":
        return RATING_AMBER
    if rating == "not_recommended":
        return RATING_RED
    return MUTED


def cefr_label(total_score: Optional[int]) -> str:
    if total_score is None:
        return "—"
    if total_score >= 90:
        return "C2"
    if total_score >= 75:
        return "C1"
    if total_score >= 60:
        return "B2"
    if total_score >= 40:
        return "B1"
    if total_score >= 20:
        return "A2"
    return "A1"


def cefr_full(total_score: Optional[int]) -> str:
    """CEFR with descriptive name — for headings."""
    code = cefr_label(total_score)
    names = {
        "C2": "C2 — Proficient",
        "C1": "C1 — Advanced",
        "B2": "B2 — Upper Intermediate",
        "B1": "B1 — Intermediate",
        "A2": "A2 — Elementary",
        "A1": "A1 — Beginner",
        "—": "—",
    }
    return names.get(code, code)


def writing_was_skipped(score) -> bool:
    if not score or not score.writing_breakdown:
        return False
    wb = score.writing_breakdown
    if not isinstance(wb, dict):
        return False
    dims = ("grammar", "vocabulary", "comprehension",
            "writing_quality", "professional_communication")
    return all(wb.get(k) is None for k in dims)


def speaking_was_skipped(score) -> bool:
    if not score or not score.speaking_breakdown:
        return False
    sb = score.speaking_breakdown
    if not isinstance(sb, dict):
        return False
    dims = ("pronunciation", "fluency", "grammar", "vocabulary", "confidence")
    return all(sb.get(k) is None for k in dims)


def all_speaking_off_topic(score, num_q: int) -> bool:
    if not score or not score.ai_feedback or num_q == 0:
        return False
    fb = score.ai_feedback
    flagged = sum(1 for i in range(1, num_q + 1) if f"Q{i}" in fb)
    return flagged == num_q and "off-topic" in fb.lower()


def extract_skip_reason(ai_feedback: Optional[str]) -> str:
    if not ai_feedback:
        return "No reason recorded."
    text = ai_feedback.strip()
    if text.lower().startswith("skipped grading"):
        end_period = text.find(".")
        end_para = text.find("\n")
        candidates = [p for p in (end_period, end_para) if p > 0]
        if candidates:
            return text[: min(candidates) + 1]
        return text
    return "See AI Assessor Feedback below for details."


def clean_ai_feedback(ai_feedback: Optional[str]) -> str:
    """Strip internal technical noise from AI feedback so HR sees only
    meaningful content.

    The speaking grading service (services/speaking_eval.py) logs technical
    failures into score.ai_feedback using database row IDs like
    "Question 53: no usable speech detected ...". HR readers see "Question
    53" and think it's question position 53 — but it's actually the
    audio_recordings.id, an internal DB key. Until the upstream service is
    fixed to emit position-based numbering (Question 1/2/3), this function
    strips those technical phrases from the PDF.

    Patterns stripped:
      - "Question N: <any reason text up to next ; or end of string>"
      - "Pronunciation assessment failed for question N"
      - "No usable transcripts for grammar/vocabulary grading"
      - "Notes:" prefix when followed by nothing meaningful
      - Stray leading/trailing semicolons, dangling separators

    If after stripping nothing meaningful remains, returns "" — the caller
    is responsible for hiding the section entirely when this happens.
    """
    if not ai_feedback:
        return ""
    text = ai_feedback

    # Strip technical "Question N: ..." failure messages (with DB IDs)
    text = re.sub(
        r"Question\s+\d+\s*:\s*[^;]*?(?:;|$)",
        "",
        text,
        flags=re.IGNORECASE,
    )
    # Strip the older "Pronunciation assessment failed for question N" pattern
    text = re.sub(
        r"Pronunciation assessment failed for question \d+\s*;?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    # Strip the bulk "No usable transcripts ..." statement
    text = re.sub(
        r"No usable transcripts for grammar/vocabulary grading\s*\.?",
        "",
        text,
        flags=re.IGNORECASE,
    )
    # If "Notes:" is now followed by only whitespace and semicolons, drop it
    text = re.sub(r"Notes:\s*(?:;\s*)*$", "", text, flags=re.IGNORECASE)
    # Normalize "prompt" → "question" so feedback uses the same vocabulary
    # as the rest of the report (section labels, UI, etc.). The GPT-4o
    # writing grader sometimes echoes the word "prompt" from its system
    # message; this rewrites those occurrences to "question" so HR readers
    # see consistent terminology. Preserves capitalization: Prompt → Question,
    # prompt → question, PROMPT → QUESTION.
    text = re.sub(r"\bPrompt\b", "Question", text)
    text = re.sub(r"\bprompt\b", "question", text)
    text = re.sub(r"\bPROMPT\b", "QUESTION", text)
    # Same for "prompts" plural
    text = re.sub(r"\bPrompts\b", "Questions", text)
    text = re.sub(r"\bprompts\b", "questions", text)
    text = re.sub(r"\bPROMPTS\b", "QUESTIONS", text)
    # Collapse stray dangling separators left at the start
    text = re.sub(r"^\s*[;:.\s]+", "", text)
    # Collapse stray dangling separators left at the end
    text = re.sub(r"[;:\s]+$", "", text)
    # Collapse repeated whitespace
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def safe_para_text(text):
    """Return HTML-escaped text safe for ReportLab Paragraph."""
    if not text or not text.strip():
        return "<i>[no text recorded]</i>"
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\n", "<br/>")
    )


_BASE = getSampleStyleSheet()


def _styles():
    return {
        "doc_title": ParagraphStyle(
            "doc_title", parent=_BASE["Title"],
            fontName="Helvetica-Bold", fontSize=20, leading=24,
            textColor=NAVY, alignment=TA_CENTER, spaceAfter=12,
        ),
        "doc_subtitle": ParagraphStyle(
            "doc_subtitle", parent=_BASE["Normal"],
            fontName="Helvetica", fontSize=10, leading=13,
            textColor=INK_SOFT, alignment=TA_LEFT, spaceAfter=12,
        ),
        "section_bar_title": ParagraphStyle(
            "section_bar_title", parent=_BASE["Heading2"],
            fontName="Helvetica-Bold", fontSize=12, leading=15,
            textColor=WHITE, alignment=TA_LEFT,
        ),
        "subsection_h": ParagraphStyle(
            "subsection_h", parent=_BASE["Heading3"],
            fontName="Helvetica-Bold", fontSize=10.5, leading=13,
            textColor=NAVY, spaceBefore=6, spaceAfter=4,
        ),
        "label": ParagraphStyle(
            "label", parent=_BASE["Normal"],
            fontName="Helvetica-Bold", fontSize=7.5, leading=10,
            textColor=MUTED,
        ),
        "value": ParagraphStyle(
            "value", parent=_BASE["Normal"],
            fontName="Helvetica", fontSize=10.5, leading=14,
            textColor=INK,
        ),
        "body": ParagraphStyle(
            "body", parent=_BASE["Normal"],
            fontName="Helvetica", fontSize=10.5, leading=15,
            textColor=INK, alignment=TA_JUSTIFY,
        ),
        "lede": ParagraphStyle(
            "lede", parent=_BASE["Normal"],
            fontName="Helvetica", fontSize=11.5, leading=16,
            textColor=INK, alignment=TA_LEFT, spaceAfter=10,
        ),
        "essay": ParagraphStyle(
            "essay", parent=_BASE["Normal"],
            fontName="Helvetica", fontSize=10.5, leading=15,
            textColor=INK, alignment=TA_JUSTIFY, spaceAfter=6,
        ),
        "transcript": ParagraphStyle(
            "transcript", parent=_BASE["Normal"],
            fontName="Helvetica", fontSize=10, leading=14,
            textColor=INK, alignment=TA_JUSTIFY, spaceAfter=6,
        ),
        "question_text": ParagraphStyle(
            "question_text", parent=_BASE["Normal"],
            fontName="Helvetica-Bold", fontSize=10.5, leading=14,
            textColor=INK, alignment=TA_LEFT, spaceAfter=4,
        ),
        "feedback_box": ParagraphStyle(
            "feedback_box", parent=_BASE["Normal"],
            fontName="Helvetica", fontSize=10.5, leading=15,
            textColor=INK, alignment=TA_JUSTIFY,
            backColor=LIGHT_GRAY,
            borderPadding=(10, 12, 10, 12),
        ),
        "callout_warn": ParagraphStyle(
            "callout_warn", parent=_BASE["Normal"],
            fontName="Helvetica", fontSize=10, leading=14,
            textColor=colors.HexColor("#a02828"),
            backColor=colors.HexColor("#fcecec"),
            borderPadding=(8, 12, 8, 12),
        ),
        "callout_info": ParagraphStyle(
            "callout_info", parent=_BASE["Normal"],
            fontName="Helvetica", fontSize=10, leading=14,
            textColor=colors.HexColor("#a86a00"),
            backColor=colors.HexColor("#fbf6ec"),
            borderPadding=(8, 12, 8, 12),
        ),
    }


def _safe_para(text, style):
    return Paragraph(safe_para_text(text), style)


def _section_bar(title, S):
    """Build the navy 'section bar' heading element.

    Returns a list of [bar, spacer]. The caller is responsible for keeping
    the bar visually attached to its first content — see _section_open()
    below for the orphan-safe version.
    """
    bar = Table([[Paragraph(title, S["section_bar_title"])]],
                colWidths=[174 * mm])
    bar.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), NAVY),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return [bar, Spacer(1, 4 * mm)]


def _section_open(title, S, first_content):
    """Start a section with its heading bar AND first content block kept
    together so the heading never gets orphaned at the bottom of a page.

    'first_content' is whatever comes right after the bar — typically a
    Table or Paragraph. ReportLab treats the whole group as one unit; if
    it can't fit on the current page, the entire group breaks to the next
    page together.

    Use this instead of `_section_bar(...) + first_content` to prevent
    section headings from being separated from their content.
    """
    bar, spacer = _section_bar(title, S)
    # Wrap [bar, spacer, first_content] so they always travel as one unit.
    if isinstance(first_content, list):
        items = [bar, spacer] + first_content
    else:
        items = [bar, spacer, first_content]
    return [KeepTogether(items)]


def _cover(inv, score, tz_name, tz_label, S):
    """Consulting-style opening: title and meta grid."""
    out = []
    out.append(Paragraph("Assessment Report", S["doc_title"]))

    # Meta grid
    rows = [
        [Paragraph("CANDIDATE", S["label"]),
         Paragraph(inv.candidate_name or "—", S["value"]),
         Paragraph("REFERENCE", S["label"]),
         Paragraph(reference_id(inv), S["value"])],
        [Paragraph("EMAIL", S["label"]),
         Paragraph(inv.candidate_email or "—", S["value"]),
         Paragraph("TEST LEVEL", S["label"]),
         Paragraph((inv.difficulty or "—").title(), S["value"])],
        [Paragraph("SUBMITTED", S["label"]),
         Paragraph(fmt_dt(inv.submitted_at, tz_name, tz_label), S["value"]),
         Paragraph("REPORT DATE", S["label"]),
         Paragraph(datetime.now(timezone.utc).strftime("%d %B %Y"),
                   S["value"])],
    ]
    grid = Table(rows, colWidths=[28 * mm, 60 * mm, 28 * mm, 58 * mm])
    grid.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LINEBELOW", (0, 0), (-1, -2), 0.3, RULE),
    ]))
    out.append(grid)
    out.append(Spacer(1, 6 * mm))
    return out


def _summary(inv, score, S):
    if not score:
        return _section_open("Section Summary", S,
                             Paragraph("Test not yet scored.", S["body"]))

    def row(name, included, val):
        if not included:
            return [name, "—", "Not part of this test"]
        if val is None:
            return [name, "—", "Not yet scored"]
        return [name, f"{val} / 100", "Completed"]

    rows = [["Section", "Score", "Outcome"]]
    rows.append(row("Reading", inv.include_reading, score.reading_score))
    rows.append(row("Writing", inv.include_writing, score.writing_score))
    rows.append(row("Speaking", inv.include_speaking, score.speaking_score))
    rows.append(["", "", ""])
    rows.append(["OVERALL",
                 f"{fmt_score(score.total_score)} / 100",
                 rating_label(score.rating)])

    table = Table(rows, colWidths=[60 * mm, 35 * mm, 79 * mm])
    rating_col = rating_color(score.rating)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10.5),
        ("ALIGN", (1, 0), (1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LINEBELOW", (0, 0), (-1, -3), 0.3, RULE),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("BACKGROUND", (0, -1), (-1, -1), LIGHT_GRAY),
        ("TEXTCOLOR", (2, -1), (2, -1), rating_col),
        ("BOX", (0, 0), (-1, -1), 0.5, RULE),
    ]))
    # Open the section with [bar + table] glued together so the heading
    # never gets stranded at the bottom of a page without the table.
    out = _section_open("Section Summary", S, table)
    out.append(Spacer(1, 3 * mm))
    bands_text = " · ".join(f"{name} {lo}–{hi}"
                            for name, lo, hi in RATING_BANDS)
    out.append(Paragraph(
        f"<i>Rating bands: {bands_text}</i>",
        ParagraphStyle("g", parent=S["body"], fontSize=8.5, textColor=MUTED),
    ))
    out.append(Spacer(1, 5 * mm))
    return out


def _reading(inv, score, S):
    if not inv.include_reading:
        return []
    if not score or score.reading_score is None:
        return _section_open(
            "Reading", S,
            Paragraph("Reading section not yet scored.", S["body"]),
        )
    correct = score.reading_correct or 0
    total = score.reading_total or 0
    pct = round((correct / total) * 100) if total > 0 else 0
    body = Paragraph(
        f"The candidate answered <b>{correct} of {total}</b> multiple-choice "
        f"questions correctly, an accuracy of <b>{pct}%</b>. The reading "
        f"score on the 0–100 scale is <b>{score.reading_score}</b>.",
        S["body"],
    )
    out = _section_open("Reading", S, body)
    out.append(Spacer(1, 4 * mm))
    return out


def _writing(inv, score, wr, prompt, S):
    if not inv.include_writing:
        return []
    if not wr:
        return _section_open(
            "Writing", S,
            Paragraph("<i>No essay submitted.</i>", S["body"]),
        )

    # Breakdown table
    wb = score.writing_breakdown if score and score.writing_breakdown else None
    rows = [["Dimension", "Score"]]
    rows.append(["Word count",
                 str(wr.word_count) if wr.word_count is not None else "—"])
    if isinstance(wb, dict):
        for key, label, denom in WRITING_DIMS:
            val = wb.get(key)
            cell = f"{val} / {denom}" if val is not None else "—"
            rows.append([label, cell])
    table = Table(rows, colWidths=[100 * mm, 74 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, LIGHT_GRAY]),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, RULE),
        ("BOX", (0, 0), (-1, -1), 0.5, RULE),
    ]))
    # Section bar + breakdown table travel together.
    out = _section_open("Writing", S, table)
    out.append(Spacer(1, 4 * mm))

    # "Question" subsection: heading + prompt text glued together so the
    # heading doesn't get orphaned at the bottom of a page.
    out.append(KeepTogether([
        Paragraph("Question", S["subsection_h"]),
        _safe_para(prompt or "(question unavailable)", S["body"]),
    ]))
    out.append(Spacer(1, 2 * mm))

    if writing_was_skipped(score):
        skip_reason = extract_skip_reason(score.ai_feedback if score else None)
        out.append(Paragraph(
            f"<b>Reviewer's note:</b> AI grading was skipped. {skip_reason} "
            f"The candidate's submitted answer is shown below as-is for "
            f"manual review.",
            S["callout_warn"],
        ))
        out.append(Spacer(1, 2 * mm))

    # "Candidate's Answer" subsection: heading + first portion of essay
    # glued together. The essay itself may wrap to the next page, but the
    # heading won't get separated from the start of the answer.
    out.append(KeepTogether([
        Paragraph("Candidate's Answer", S["subsection_h"]),
        _safe_para(wr.essay_text, S["essay"]),
    ]))
    out.append(Spacer(1, 3 * mm))
    return out


def _speaking(inv, score, recordings, topic_prompts, S):
    if not inv.include_speaking:
        return []

    num_q = len(recordings) if recordings else 0
    all_off = all_speaking_off_topic(score, num_q)

    # Build the breakdown table first (if there is one), so we know what
    # to glue the section heading to.
    sb = score.speaking_breakdown if score and score.speaking_breakdown else None
    breakdown_table = None
    if isinstance(sb, dict):
        rows = [["Dimension", "Score"]]
        for key, label in SPEAKING_DIMS:
            val = sb.get(key)
            cell = f"{val} / 100" if val is not None else "—"
            rows.append([label, cell])
        breakdown_table = Table(rows, colWidths=[100 * mm, 74 * mm])
        breakdown_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, LIGHT_GRAY]),
            ("LINEBELOW", (0, 0), (-1, -1), 0.25, RULE),
            ("BOX", (0, 0), (-1, -1), 0.5, RULE),
        ]))

    # Section heading travels with whatever its first piece of content is:
    # the breakdown table if present, otherwise the off-topic note if
    # present, otherwise the first question block.
    if breakdown_table is not None:
        out = _section_open("Speaking", S, breakdown_table)
        out.append(Spacer(1, 4 * mm))
    else:
        # No breakdown — section bar alone, content follows below.
        out = _section_bar("Speaking", S)

    if all_off and not speaking_was_skipped(score):
        out.append(Paragraph(
            "<b>Reviewer's note:</b> All speaking responses were flagged "
            "off-topic. The candidate spoke at length but did not address "
            "the questions asked. Per-dimension scores reflect <i>how</i> "
            "the candidate spoke, not <i>what</i> they said — rely on the "
            "transcripts below for the hiring decision.",
            S["callout_info"],
        ))
        out.append(Spacer(1, 3 * mm))

    if recordings:
        # Each question block (heading + prompt + answer-label + transcript)
        # is wrapped in KeepTogether so the question heading never gets
        # stranded at the bottom of a page without its transcript.
        for idx, rec in enumerate(recordings, start=1):
            prompt = topic_prompts.get(rec.topic_id, "(question unavailable)")
            duration_str = fmt_duration(rec.duration_seconds)
            block = [
                Paragraph(f"Question {idx}", S["subsection_h"]),
                _safe_para(prompt, S["question_text"]),
                Paragraph(
                    f"<b>Candidate's Answer</b> "
                    f"<font size='8' color='#6a7280'>"
                    f"(transcript · {duration_str})</font>",
                    S["label"],
                ),
                _safe_para(rec.transcript, S["transcript"]),
            ]
            out.append(KeepTogether(block))
            out.append(Spacer(1, 4 * mm))
    else:
        out.append(Paragraph("<i>No audio recordings submitted.</i>",
                             S["body"]))
    return out


def _feedback(score, S):
    if not score or not score.ai_feedback:
        return []
    cleaned = clean_ai_feedback(score.ai_feedback)
    if not cleaned:
        return []
    # Section bar + feedback box travel together so the heading never gets
    # stranded above a page break.
    return _section_open(
        "Assessor Feedback", S,
        _safe_para(cleaned, S["feedback_box"]),
    )


def _integrity(inv, S):
    terminated = inv.submission_reason and inv.submission_reason not in (
        "candidate_finished", "candidate_submit",
    )
    rows = [
        ["Tab switches", str(inv.tab_switches_count or 0)],
        ["Total time off-tab",
         fmt_duration(inv.tab_switches_total_seconds or 0)],
        ["Submission status",
         format_submission_reason(inv.submission_reason)],
        ["Terminated", "Yes" if terminated else "No"],
    ]
    if inv.started_at and inv.submitted_at:
        elapsed = int((inv.submitted_at - inv.started_at).total_seconds())
        rows.append(["Total test time", fmt_duration(elapsed)])
    sec_allowed = sum(
        getattr(inv, attr, 0) or 0
        for attr, flag in [
            ("reading_seconds", inv.include_reading),
            ("writing_seconds", inv.include_writing),
            ("speaking_seconds", inv.include_speaking),
        ] if flag
    )
    if sec_allowed:
        rows.append(["Time allowed", fmt_duration(sec_allowed)])

    table = Table(rows, colWidths=[60 * mm, 114 * mm])
    table.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 10.5),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TEXTCOLOR", (0, 0), (0, -1), MUTED),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, RULE),
        ("BOX", (0, 0), (-1, -1), 0.5, RULE),
    ]))
    # Section bar + integrity table travel together.
    out = _section_open("Test Integrity", S, table)

    if terminated:
        out.append(Spacer(1, 3 * mm))
        out.append(Paragraph(
            "<b>Reviewer's note:</b> This test was auto-terminated. "
            "Scores reflect only the work the candidate completed before "
            "termination.",
            S["callout_warn"],
        ))
    return out


def _make_page_decorator(inv, generated_at):
    ref = reference_id(inv)
    cand = inv.candidate_name or "(no name)"

    def _on_page(canvas, doc):
        canvas.saveState()
        pw = A4[0]
        ph = A4[1]
        # Header strip — logo top-left only. Right side intentionally empty.
        # Logo bottom sits at ph - 14mm (its height is 6.84mm, so the top is
        # at ph - 7.16mm). Try the image first; fall back to a text wordmark
        # so the report still generates if the logo file goes missing in prod.
        try:
            canvas.drawImage(
                LOGO_PATH,
                18 * mm,                     # x: left margin
                ph - 14 * mm,                # y: bottom edge of logo
                width=LOGO_WIDTH_MM * mm,
                height=LOGO_HEIGHT_MM * mm,
                mask='auto',                 # respect transparency if any
                preserveAspectRatio=True,
            )
        except Exception:
            canvas.setFont("Helvetica-Bold", 9)
            canvas.setFillColor(NAVY)
            canvas.drawString(18 * mm, ph - 12 * mm, "FluentiQ")
        # Navy rule below the header strip
        canvas.setStrokeColor(NAVY)
        canvas.setLineWidth(0.6)
        canvas.line(18 * mm, ph - 16 * mm, pw - 18 * mm, ph - 16 * mm)
        # Footer
        y = 10 * mm
        canvas.setStrokeColor(RULE)
        canvas.setLineWidth(0.3)
        canvas.line(18 * mm, y + 4 * mm, pw - 18 * mm, y + 4 * mm)
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawRightString(pw - 18 * mm, y, f"Page {doc.page}")
        canvas.restoreState()

    return _on_page


def build_candidate_pdf(
    inv, score, writing_response, writing_prompt,
    audio_recordings, speaking_topic_prompts,
    candidate_tz_name=None, candidate_tz_label=None,
):
    S = _styles()
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=22 * mm, bottomMargin=20 * mm,
        title=f"Assessment Report — {inv.candidate_name}",
        author="FluentiQ",
    )
    story = []
    story += _cover(inv, score, candidate_tz_name, candidate_tz_label, S)
    story += _summary(inv, score, S)
    story += _reading(inv, score, S)
    story += _writing(inv, score, writing_response, writing_prompt, S)
    story += _speaking(inv, score, audio_recordings, speaking_topic_prompts, S)
    story += _feedback(score, S)
    story += _integrity(inv, S)

    page_dec = _make_page_decorator(inv, datetime.now(timezone.utc))
    doc.build(story, onFirstPage=page_dec, onLaterPages=page_dec)
    out = buf.getvalue()
    buf.close()
    return out