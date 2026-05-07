"""
PDF report generator for a single candidate's assessment results.

Called from routes/hr_reports.py via build_candidate_pdf(). Returns the PDF
as bytes (caller wraps in a StreamingResponse).

DESIGN DECISIONS

1. ReportLab Platypus (high-level "flowables" API), not the low-level canvas.
   Platypus auto-handles page breaks, table layout, and paragraph wrapping.
   Page breaks happen naturally when content overflows — we don't need to
   manually paginate audio transcripts of unknown length.

2. One PDF per candidate. Generated synchronously on request (200-500ms typical).
   No caching — these get downloaded once per candidate per hiring decision.

3. All sections are conditional on whether they were INCLUDED in the test.
   A reading-only invitation produces a PDF with only the reading section.
   This matches how the HR dashboard already shows "—" for excluded sections.

4. NO embedded audio. PDFs can't play audio reliably, so HR uses the dashboard
   for that. The PDF is for sharing with hiring managers who need numbers
   and transcripts in one document.

5. Color palette is intentionally subdued — this goes to senior managers and
   sometimes to candidates themselves. No "rejected in red" because that's
   hostile. The rating is shown as plain text with a colored bar.
"""
from __future__ import annotations

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
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    PageBreak,
    KeepTogether,
)

from models import Invitation, Score, AudioRecording, WritingResponse, WritingTopic, SpeakingTopic


# ------------------------------------------------------------------
# Styles
# ------------------------------------------------------------------
# These build on ReportLab's getSampleStyleSheet() and override fonts/sizes
# to match a clean professional look. Keep all customizations here so a
# future tweak (e.g., "make the score bigger") happens in one place.
_BASE = getSampleStyleSheet()

STYLES = {
    "title": ParagraphStyle(
        "title",
        parent=_BASE["Title"],
        fontSize=20,
        spaceAfter=4,
        textColor=colors.HexColor("#0b2545"),
        alignment=TA_LEFT,
    ),
    "subtitle": ParagraphStyle(
        "subtitle",
        parent=_BASE["Normal"],
        fontSize=10,
        textColor=colors.HexColor("#5a6478"),
        spaceAfter=12,
    ),
    "section": ParagraphStyle(
        "section",
        parent=_BASE["Heading2"],
        fontSize=13,
        spaceBefore=12,
        spaceAfter=6,
        textColor=colors.HexColor("#0b2545"),
    ),
    "label": ParagraphStyle(
        "label",
        parent=_BASE["Normal"],
        fontSize=9,
        textColor=colors.HexColor("#5a6478"),
    ),
    "value": ParagraphStyle(
        "value",
        parent=_BASE["Normal"],
        fontSize=10,
        textColor=colors.HexColor("#1a1a1a"),
    ),
    "score_big": ParagraphStyle(
        "score_big",
        parent=_BASE["Normal"],
        fontSize=28,
        textColor=colors.HexColor("#0b2545"),
        leading=32,
    ),
    "rating_recommended": ParagraphStyle(
        "rating_recommended",
        parent=_BASE["Normal"],
        fontSize=12,
        textColor=colors.HexColor("#1f7a3a"),
    ),
    "rating_borderline": ParagraphStyle(
        "rating_borderline",
        parent=_BASE["Normal"],
        fontSize=12,
        textColor=colors.HexColor("#a86a00"),
    ),
    "rating_not": ParagraphStyle(
        "rating_not",
        parent=_BASE["Normal"],
        fontSize=12,
        textColor=colors.HexColor("#a02828"),
    ),
    "transcript": ParagraphStyle(
        "transcript",
        parent=_BASE["Normal"],
        fontSize=9.5,
        leading=13,
        textColor=colors.HexColor("#1a1a1a"),
        alignment=TA_JUSTIFY,
        spaceAfter=6,
    ),
    "essay": ParagraphStyle(
        "essay",
        parent=_BASE["Normal"],
        fontSize=10,
        leading=14,
        textColor=colors.HexColor("#1a1a1a"),
        alignment=TA_JUSTIFY,
        spaceAfter=6,
    ),
    "feedback": ParagraphStyle(
        "feedback",
        parent=_BASE["Normal"],
        fontSize=10,
        leading=14,
        textColor=colors.HexColor("#1a1a1a"),
        alignment=TA_JUSTIFY,
        backColor=colors.HexColor("#f5f7fa"),
        borderPadding=(8, 8, 8, 8),
        spaceAfter=6,
    ),
    "warn": ParagraphStyle(
        "warn",
        parent=_BASE["Normal"],
        fontSize=9.5,
        textColor=colors.HexColor("#a02828"),
    ),
}


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------
def _fmt_score(value: Optional[int]) -> str:
    """Render a 0-100 score, or em-dash if missing."""
    return f"{value}" if value is not None else "—"


def _fmt_dt(
    dt: Optional[datetime],
    target_tz_name: Optional[str] = None,
    tz_label: Optional[str] = None,
) -> str:
    """
    Render a stored UTC timestamp in the candidate's local timezone.

    Args:
        dt: the datetime stored in the DB. Naive in our schema (see models.py
            comment about SQLite/Postgres tz round-trip), but treated as UTC.
        target_tz_name: an IANA zone name like "Asia/Kolkata". When set, the
            timestamp is converted to that zone before formatting. When None
            or "UTC", the timestamp is rendered as UTC.
        tz_label: a short suffix like "IST" or "ET" to append to the formatted
            string. Comes from supported_timezones.short_label. When None,
            falls back to the IANA name or "UTC".

    Failure modes (all safe — never raises):
      - dt is None              → returns "—"
      - target_tz_name is bad   → falls back to UTC display with "(UTC)" label
      - tzdata not installed    → same as above (Windows without `pip install tzdata`)

    Returns strings like:
      "2026-05-06 16:35 (IST)"   ← normal case, candidate's zone
      "2026-05-06 10:35 (UTC)"   ← fallback when zone is unknown
      "—"                         ← when dt is None
    """
    if dt is None:
        return "—"

    # Treat naive datetimes as UTC. Python 3.12+ deprecates the implicit
    # assumption, so be explicit.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    # Default render = UTC if no target zone given
    if not target_tz_name or target_tz_name == "UTC":
        return dt.strftime("%Y-%m-%d %H:%M") + " (UTC)"

    # Convert to candidate's zone. On failure, fall back to UTC so the time
    # AND the label match — better than rendering a non-UTC time with a UTC
    # label, which would silently mislead.
    try:
        target = ZoneInfo(target_tz_name)
        local = dt.astimezone(target)
        label = tz_label or target_tz_name
        return local.strftime("%Y-%m-%d %H:%M") + f" ({label})"
    except ZoneInfoNotFoundError:
        return dt.strftime("%Y-%m-%d %H:%M") + " (UTC)"


def _fmt_duration(seconds: Optional[int]) -> str:
    """Render seconds as 'Xm Ys'. Em-dash if None or 0."""
    if not seconds:
        return "—"
    m = seconds // 60
    s = seconds % 60
    if m == 0:
        return f"{s}s"
    return f"{m}m {s}s"


def _rating_style(rating: Optional[str]) -> ParagraphStyle:
    """Pick the colored Paragraph style based on the rating value."""
    if rating == "recommended":
        return STYLES["rating_recommended"]
    if rating == "borderline":
        return STYLES["rating_borderline"]
    return STYLES["rating_not"]  # default for not_recommended and unknown


def _rating_label(rating: Optional[str]) -> str:
    """Convert internal rating string to a human-readable label."""
    if rating == "recommended":
        return "RECOMMENDED"
    if rating == "borderline":
        return "BORDERLINE"
    if rating == "not_recommended":
        return "NOT RECOMMENDED"
    return "NOT YET SCORED"


def _safe_para(text: Optional[str], style_key: str) -> Paragraph:
    """
    Wrap user-generated text (transcripts, essays, feedback) in a Paragraph
    with HTML-escaping. ReportLab's Paragraph treats <, >, & as markup, so
    a candidate writing "x < y" would crash the renderer without escaping.

    None and empty strings render as a placeholder ("[no text]") so the
    section doesn't silently disappear.
    """
    if not text or not text.strip():
        return Paragraph("<i>[no text recorded]</i>", STYLES[style_key])
    # Escape HTML special chars; preserve newlines as <br/> for readability.
    escaped = (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\n", "<br/>")
    )
    return Paragraph(escaped, STYLES[style_key])


# ------------------------------------------------------------------
# Section builders — each returns a list of flowables
# ------------------------------------------------------------------
def _build_header(
    inv: Invitation,
    score: Optional[Score],
    target_tz_name: Optional[str],
    tz_label: Optional[str],
) -> list:
    """
    Top-of-page block: candidate name + email + level + date + total + rating.

    target_tz_name + tz_label control how 'Test taken' is rendered. These
    flow from build_candidate_pdf — see the docstring there for resolution
    rules. The other dates in the report (footer's "Generated at") stay in
    UTC because the audience for the footer is internal/auditing, not the
    candidate's local context.

    This is the first thing a hiring manager sees when they open the PDF, so
    it has to convey the headline answer ("recommended? yes/no/maybe") without
    requiring them to read further.
    """
    out: list = []

    # Candidate name + branding/title row
    out.append(Paragraph("FluentiQ — Assessment Report", STYLES["title"]))
    out.append(Paragraph(f"Candidate: {inv.candidate_name}", STYLES["subtitle"]))

    # Two-column meta table: labels on left, values on right.
    # Using a Table because Paragraphs alone can't easily produce side-by-side
    # label/value pairs aligned across rows.
    meta_data = [
        [Paragraph("Email", STYLES["label"]),
         Paragraph(inv.candidate_email or "—", STYLES["value"])],
        [Paragraph("Level", STYLES["label"]),
         Paragraph((inv.difficulty or "—").title(), STYLES["value"])],
        [Paragraph("Test taken", STYLES["label"]),
         Paragraph(_fmt_dt(inv.submitted_at, target_tz_name, tz_label),
                   STYLES["value"])],
        [Paragraph("Invitation ID", STYLES["label"]),
         Paragraph(str(inv.id), STYLES["value"])],
    ]
    meta_table = Table(meta_data, colWidths=[35 * mm, 130 * mm])
    meta_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    out.append(meta_table)
    out.append(Spacer(1, 8 * mm))

    # Big total score + rating banner (or "Not yet scored" if score is None)
    if score is None:
        out.append(Paragraph(
            "<b>Status:</b> Test in progress or not yet scored.",
            STYLES["value"],
        ))
    else:
        # Two-column block: total score on left, rating on right
        rating_para = Paragraph(
            f"<b>{_rating_label(score.rating)}</b>",
            _rating_style(score.rating),
        )
        score_block = [[
            Paragraph(f"<b>{_fmt_score(score.total_score)}</b>", STYLES["score_big"]),
            Paragraph("OVERALL SCORE", STYLES["label"]),
            rating_para,
        ]]
        score_table = Table(
            score_block,
            colWidths=[35 * mm, 60 * mm, 70 * mm],
        )
        score_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (2, 0), (2, 0), "RIGHT"),
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f5f7fa")),
            ("LEFTPADDING", (0, 0), (-1, -1), 12),
            ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ("TOPPADDING", (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ]))
        out.append(score_table)

    out.append(Spacer(1, 6 * mm))
    return out


def _build_section_summary(inv: Invitation, score: Optional[Score]) -> list:
    """
    Per-section summary table: Reading / Writing / Speaking with each section's
    score and inclusion status. Sections excluded from the test show "Not
    included" instead of 0, so HR can tell the difference between "candidate
    failed reading" vs "this test didn't include reading".
    """
    out = []
    out.append(Paragraph("Section Summary", STYLES["section"]))

    rows = [
        ["Section", "Score", "Status"],
    ]

    def _section_row(name: str, included: bool, score_val: Optional[int]) -> list:
        if not included:
            return [name, "—", "Not included"]
        if score_val is None:
            return [name, "—", "Not yet scored"]
        return [name, f"{score_val} / 100", "Scored"]

    s = score
    rows.append(_section_row(
        "Reading",
        inv.include_reading,
        s.reading_score if s else None,
    ))
    rows.append(_section_row(
        "Writing",
        inv.include_writing,
        s.writing_score if s else None,
    ))
    rows.append(_section_row(
        "Speaking",
        inv.include_speaking,
        s.speaking_score if s else None,
    ))

    table = Table(rows, colWidths=[60 * mm, 40 * mm, 65 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0b2545")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("ALIGN", (1, 0), (1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f7fa")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d0d7e2")),
    ]))
    out.append(table)
    out.append(Spacer(1, 4 * mm))
    return out


def _build_reading_section(inv: Invitation, score: Optional[Score]) -> list:
    """Reading details: # correct out of # total."""
    if not inv.include_reading:
        return []
    out = [Paragraph("Reading", STYLES["section"])]
    if not score or score.reading_score is None:
        out.append(Paragraph("Reading section not yet scored.", STYLES["value"]))
        return out

    rows = [
        ["Score", f"{score.reading_score} / 100"],
        ["Correct answers",
         f"{score.reading_correct or 0} of {score.reading_total or 0}"],
    ]
    table = Table(rows, colWidths=[50 * mm, 115 * mm])
    table.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#5a6478")),
    ]))
    out.append(table)
    out.append(Spacer(1, 3 * mm))
    return out


def _build_writing_section(
    inv: Invitation,
    score: Optional[Score],
    wr: Optional[WritingResponse],
    writing_prompt: Optional[str],
) -> list:
    """Writing section: prompt + essay + score breakdown + AI feedback."""
    if not inv.include_writing:
        return []
    out = [Paragraph("Writing", STYLES["section"])]

    if not wr:
        out.append(Paragraph("No essay submitted.", STYLES["warn"]))
        return out

    # Score breakdown (if scored). writing_breakdown is a JSON dict per scoring.py.
    breakdown_rows = [
        ["Score", _fmt_score(score.writing_score) if score else "—"],
        ["Word count", str(wr.word_count) if wr.word_count is not None else "—"],
    ]
    if score and score.writing_breakdown:
        wb = score.writing_breakdown
        if isinstance(wb, dict):
            for key in ("task_response", "grammar", "vocabulary", "coherence"):
                if key in wb:
                    breakdown_rows.append([
                        key.replace("_", " ").title(),
                        f"{wb[key]} / 25",
                    ])
    breakdown_table = Table(breakdown_rows, colWidths=[50 * mm, 115 * mm])
    breakdown_table.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#5a6478")),
    ]))
    out.append(breakdown_table)
    out.append(Spacer(1, 3 * mm))

    # Prompt
    out.append(Paragraph("<b>Prompt</b>", STYLES["label"]))
    out.append(_safe_para(writing_prompt or "(prompt unavailable)", "value"))
    out.append(Spacer(1, 3 * mm))

    # Essay
    out.append(Paragraph("<b>Candidate's Essay</b>", STYLES["label"]))
    out.append(_safe_para(wr.essay_text, "essay"))
    out.append(Spacer(1, 3 * mm))

    return out


def _build_speaking_section(
    inv: Invitation,
    score: Optional[Score],
    audio_recordings: list,
    topic_prompts: dict,
) -> list:
    """Speaking section: breakdown of 5 dimensions + per-question transcripts."""
    if not inv.include_speaking:
        return []
    out = [Paragraph("Speaking", STYLES["section"])]

    # Dimension breakdown (if scored)
    breakdown_rows = [
        ["Score", _fmt_score(score.speaking_score) if score else "—"],
    ]
    if score and score.speaking_breakdown:
        sb = score.speaking_breakdown
        if isinstance(sb, dict):
            for key in ("pronunciation", "fluency", "grammar", "vocabulary", "confidence"):
                if key in sb:
                    val = sb[key]
                    breakdown_rows.append([
                        key.title(),
                        f"{val} / 100" if val is not None else "—",
                    ])
    breakdown_table = Table(breakdown_rows, colWidths=[50 * mm, 115 * mm])
    breakdown_table.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#5a6478")),
    ]))
    out.append(breakdown_table)
    out.append(Spacer(1, 3 * mm))

    # Per-question transcripts. Each Q is wrapped in KeepTogether so the
    # prompt + transcript don't get split across a page break (ugly).
    if audio_recordings:
        for idx, rec in enumerate(audio_recordings, start=1):
            prompt = topic_prompts.get(rec.topic_id, "(prompt unavailable)")
            block = [
                Paragraph(f"<b>Question {idx}</b>", STYLES["label"]),
                _safe_para(prompt, "value"),
                Spacer(1, 1.5 * mm),
                Paragraph(
                    f"<b>Transcript</b> "
                    f"<font color='#5a6478'>"
                    f"({_fmt_duration(rec.duration_seconds)})"
                    f"</font>",
                    STYLES["label"],
                ),
                _safe_para(rec.transcript, "transcript"),
                Spacer(1, 3 * mm),
            ]
            out.append(KeepTogether(block))
    else:
        out.append(Paragraph("No audio recordings submitted.", STYLES["warn"]))

    return out


def _build_feedback_section(score: Optional[Score]) -> list:
    """AI feedback paragraph (writing + speaking combined)."""
    if not score or not score.ai_feedback:
        return []
    out = [Paragraph("AI Assessor Feedback", STYLES["section"])]
    out.append(_safe_para(score.ai_feedback, "feedback"))
    out.append(Spacer(1, 3 * mm))
    return out


def _build_integrity_section(inv: Invitation) -> list:
    """
    Test integrity / anti-cheating signals. HR uses this to decide whether
    to trust the score or follow up with a manual interview.
    """
    out = [Paragraph("Test Integrity", STYLES["section"])]

    # "Terminated" is derived from submission_reason rather than a stored
    # boolean column. The taxonomy is documented in models.py: any of
    # tab_switch_termination, reading_timer_expired, writing_timer_expired,
    # speaking_timer_expired means the test was forcibly ended; everything
    # else (candidate_finished, None for in-progress) is "normal".
    terminated = inv.submission_reason and inv.submission_reason not in (
        "candidate_finished", "candidate_submit",
    )

    rows = [
        ["Tab switches", str(inv.tab_switches_count or 0)],
        ["Total time off-tab", _fmt_duration(inv.tab_switches_total_seconds or 0)],
        ["Submission status", _format_submission_reason(inv.submission_reason)],
        ["Terminated", "Yes" if terminated else "No"],
    ]

    if inv.started_at and inv.submitted_at:
        elapsed = int((inv.submitted_at - inv.started_at).total_seconds())
        rows.append(["Total test time", _fmt_duration(elapsed)])

    # Total seconds allowed = sum of per-section seconds. Each section's
    # cap is stored separately on the invitation so HR can include only
    # the sections that were actually part of the test.
    seconds_allowed = 0
    if inv.include_reading:
        seconds_allowed += inv.reading_seconds or 0
    if inv.include_writing:
        seconds_allowed += inv.writing_seconds or 0
    if inv.include_speaking:
        seconds_allowed += inv.speaking_seconds or 0
    if seconds_allowed > 0:
        rows.append(["Time allowed", _fmt_duration(seconds_allowed)])

    table = Table(rows, colWidths=[50 * mm, 115 * mm])
    table.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#5a6478")),
    ]))
    out.append(table)

    # If terminated, show a warning highlighting the reason
    if terminated:
        out.append(Spacer(1, 2 * mm))
        out.append(Paragraph(
            "<b>Note:</b> This test was auto-terminated. "
            "Scores reflect only the work the candidate completed before termination.",
            STYLES["warn"],
        ))

    out.append(Spacer(1, 3 * mm))
    return out


def _format_submission_reason(reason: Optional[str]) -> str:
    """Map internal submission_reason strings to human-readable labels."""
    if not reason:
        return "—"
    mapping = {
        "candidate_finished": "Completed normally",
        "tab_switch_termination": "Terminated (tab-switch limit)",
        "tab_switch_limit": "Terminated (tab-switch limit)",
        "speaking_timer_expired": "Auto-submitted (time expired)",
        "window_expired": "Auto-submitted (window expired)",
    }
    return mapping.get(reason, reason.replace("_", " ").title())




# ------------------------------------------------------------------
# Main entry point
# ------------------------------------------------------------------
def build_candidate_pdf(
    inv: Invitation,
    score: Optional[Score],
    writing_response: Optional[WritingResponse],
    writing_prompt: Optional[str],
    audio_recordings: list,
    speaking_topic_prompts: dict,
    candidate_tz_name: Optional[str] = None,
    candidate_tz_label: Optional[str] = None,
) -> bytes:
    """
    Generate a complete assessment PDF for one candidate.

    Args:
        inv: the Invitation row
        score: the Score row, or None if not yet scored
        writing_response: the WritingResponse row, or None
        writing_prompt: the assigned writing prompt text, or None
        audio_recordings: list of AudioRecording rows (already sorted)
        speaking_topic_prompts: dict mapping topic_id → prompt_text
        candidate_tz_name: IANA timezone name (e.g. "Asia/Kolkata") for
            rendering the candidate-facing 'Test taken' timestamp. Comes
            from inv.display_timezone — the same zone HR picked when
            sending the invitation. When None or "UTC", the timestamp is
            shown in UTC.
        candidate_tz_label: short label like "IST" or "ET" appended to the
            formatted time. Comes from supported_timezones.short_label.

    Why two timezone params instead of just looking up the row inside this
    function: keeps this function dependency-free of the DB. The caller
    (routes/hr_reports.py) already has a Session — it does the lookup once
    and passes the strings down. This keeps the PDF builder pure and
    unit-testable without a DB.

    The caller is responsible for fetching all of these upfront so this
    function makes zero DB queries.

    Returns the PDF as bytes. Caller wraps in StreamingResponse.
    """
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=f"Assessment Report — {inv.candidate_name}",
        author="FluentiQ",
    )

    story: list = []
    story += _build_header(inv, score, candidate_tz_name, candidate_tz_label)
    story += _build_section_summary(inv, score)
    story += _build_reading_section(inv, score)
    story += _build_writing_section(inv, score, writing_response, writing_prompt)
    story += _build_speaking_section(inv, score, audio_recordings, speaking_topic_prompts)
    story += _build_feedback_section(score)
    story += _build_integrity_section(inv)
    story += _build_footer(inv)

    doc.build(story)
    pdf_bytes = buf.getvalue()
    buf.close()
    return pdf_bytes