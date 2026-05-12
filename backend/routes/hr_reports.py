"""
HR-side downloadable reports.

Two endpoints:

  GET /api/hr/results/{invitation_id}/report.pdf
    → PDF of one candidate's full assessment, for sharing with hiring managers.

  GET /api/hr/results/export.xlsx
    → Excel of ALL invitations from this HR, for bulk processing.

AUTH: both endpoints use require_hr_strict — same as the existing /results
and /results/{id} endpoints in routes/hr.py. Tenancy enforcement is
delegated to tenancy.py helpers (tenant_scope_invitations for the bulk
export, assert_can_access_invitation for the per-invitation PDF). For
HR role this resolves to "see only invitations you personally sent",
matching pre-multi-tenancy behavior.

NOTE FOR DEPLOYMENT: this router must be registered in main.py BEFORE the
catch-all api_not_found handler, or every request to these endpoints
returns 404. See main.py instructions in this commit.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from io import BytesIO
from sqlalchemy.orm import Session

from auth import require_hr_strict, Principal
from database import get_db
from models import (
    HRAdmin,
    Invitation,
    SpeakingTopic,
    SupportedTimezone,
    WritingTopic,
)
from tenancy import tenant_scope_invitations, assert_can_access_invitation
from services.pdf_report import build_candidate_pdf
from services.excel_report import build_bulk_xlsx

log = logging.getLogger("hr_reports")

router = APIRouter(prefix="/api/hr", tags=["hr-reports"])


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------
def _safe_filename(name: str, fallback: str = "candidate") -> str:
    """
    Sanitize a candidate name for use in a Content-Disposition filename.

    Browser behavior on weird filenames is inconsistent — strip everything
    that isn't alphanumeric, dash, underscore, or dot. Replace spaces with
    underscores so 'John Smith' becomes 'John_Smith' (readable when saved).

    Empty/missing names fall back to the fallback string. We avoid the
    raw email here because emails contain '@' and '.' which sometimes
    confuse OS file-save dialogs on Windows.
    """
    if not name or not name.strip():
        return fallback
    cleaned = "".join(
        c if (c.isalnum() or c in "-_.") else "_"
        for c in name.strip().replace(" ", "_")
    )
    # Collapse runs of underscores produced by stripping
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned.strip("_") or fallback


def _content_disposition(filename: str) -> str:
    """
    Build a Content-Disposition header that works in all major browsers.

    RFC 5987 lets us send both the ASCII fallback (filename=) AND a
    URL-encoded UTF-8 version (filename*=) for browsers that support
    non-ASCII names. Most modern browsers honor filename*.

    We always send `attachment` so the browser pops a save dialog
    instead of trying to render the PDF inline.
    """
    encoded = quote(filename)
    return (
        f'attachment; filename="{filename}"; '
        f"filename*=UTF-8''{encoded}"
    )


# ------------------------------------------------------------------
# PDF: single candidate
# ------------------------------------------------------------------
@router.get("/results/{invitation_id}/report.pdf")
def download_candidate_pdf(
    invitation_id: int,
    hr: HRAdmin = Depends(require_hr_strict),
    db: Session = Depends(get_db),
):
    """
    Download a PDF report for one candidate.

    Tenancy: 404 (not 403) if the invitation isn't accessible to this
    principal — same approach as result_detail in hr.py, so we don't leak
    existence of cross-tenant data. See tenancy.assert_can_access_invitation.
    """
    inv = db.query(Invitation).filter(Invitation.id == invitation_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invitation not found.")
    assert_can_access_invitation(
        inv,
        Principal(user=hr, role=hr.role, organization_id=hr.organization_id),
    )

    score = inv.score  # None if not yet scored — PDF handles that

    # Writing data — assigned prompt + candidate's essay
    wr = inv.writing_response
    writing_prompt = None
    if wr:
        topic = db.query(WritingTopic).filter(WritingTopic.id == wr.topic_id).first()
        writing_prompt = topic.prompt_text if topic else None

    # Speaking data — recordings sorted by id, plus a topic_id → prompt map
    audio_recordings = sorted(inv.audio_recordings or [], key=lambda r: r.id)
    speaking_topic_prompts = {}
    if audio_recordings:
        topic_ids = list({r.topic_id for r in audio_recordings})
        speaking_topic_prompts = {
            t.id: t.prompt_text
            for t in db.query(SpeakingTopic).filter(SpeakingTopic.id.in_(topic_ids)).all()
        }

    # Look up the candidate's timezone short label so the 'Test taken'
    # timestamp can be rendered in their local time (e.g. "16:35 IST")
    # rather than UTC. Falls back gracefully if the supported_timezones
    # row is missing — the PDF builder handles UTC fallback safely.
    candidate_tz_name = inv.display_timezone or "UTC"
    candidate_tz_label = None
    if candidate_tz_name and candidate_tz_name != "UTC":
        tz_row = db.query(SupportedTimezone).filter(
            SupportedTimezone.iana_name == candidate_tz_name
        ).first()
        candidate_tz_label = tz_row.short_label if tz_row else candidate_tz_name

    # Generate the PDF (sync — typical 200-500ms for a normal candidate)
    try:
        pdf_bytes = build_candidate_pdf(
            inv=inv,
            score=score,
            writing_response=wr,
            writing_prompt=writing_prompt,
            audio_recordings=audio_recordings,
            speaking_topic_prompts=speaking_topic_prompts,
            candidate_tz_name=candidate_tz_name,
            candidate_tz_label=candidate_tz_label,
        )
    except Exception as e:
        log.error(
            "PDF generation failed for invitation %s: %s: %s",
            invitation_id, type(e).__name__, e,
        )
        raise HTTPException(
            status_code=500,
            detail="Could not generate the PDF report. Please try again or contact support.",
        )

    # Filename: "Assessment_<CandidateName>_<YYYYMMDD>.pdf"
    date_str = (inv.submitted_at or datetime.now(timezone.utc)).strftime("%Y%m%d")
    fname = f"Assessment_{_safe_filename(inv.candidate_name)}_{date_str}.pdf"

    return StreamingResponse(
        BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": _content_disposition(fname)},
    )


# ------------------------------------------------------------------
# Excel: all candidates (bulk)
# ------------------------------------------------------------------
# IMPORTANT: this endpoint is registered at /exports/candidates.xlsx, NOT
# at /results/export.xlsx. Reason: FastAPI matches routes by order, and
# routes/hr.py already declares
#     @router.get("/results/{invitation_id}", response_model=ScoreDetail)
# which has higher priority because its router is registered first in
# main.py. A request to /results/export.xlsx would match the {invitation_id}
# pattern, fail integer validation on "export.xlsx", and return 422.
# Putting the bulk export under /exports/ removes the collision entirely.
@router.get("/exports/candidates.xlsx")
def download_bulk_xlsx(
    hr: HRAdmin = Depends(require_hr_strict),
    db: Session = Depends(get_db),
):
    """
    Download an Excel file with ONE row per invitation belonging to this HR.

    Includes ALL invitations regardless of completion status — HR can
    filter inside Excel itself. Newest first, matching the dashboard
    table order so the spreadsheet is intuitive when HR opens it.

    No query-parameter filtering for v1. If HR needs "only this month" or
    "only scored," they can filter inside Excel using the column header
    auto-filter. Add server-side filters later only if real volume makes
    that necessary.
    """
    invitations = (
        tenant_scope_invitations(
            db.query(Invitation),
            Principal(user=hr, role=hr.role, organization_id=hr.organization_id),
        )
        .order_by(Invitation.created_at.desc())
        .all()
    )

    # Pair each invitation with its score (or None). Done in Python rather
    # than via a join-with-LEFT-OUTER because SQLAlchemy's relationship
    # already loads inv.score lazily — and for bulk export the count is
    # bounded (typically <500 candidates per HR).
    rows = [(inv, inv.score) for inv in invitations]

    # Pre-fetch short labels for every distinct timezone present in the
    # invitations. One query for the whole batch instead of N queries
    # (one per row). For 500 invitations across 5 zones, this is the
    # difference between 5 SQL queries and 500.
    tz_names = {
        inv.display_timezone for inv in invitations
        if inv.display_timezone and inv.display_timezone != "UTC"
    }
    tz_label_map: dict[str, str] = {}
    if tz_names:
        tz_rows = db.query(SupportedTimezone).filter(
            SupportedTimezone.iana_name.in_(tz_names)
        ).all()
        tz_label_map = {row.iana_name: row.short_label for row in tz_rows}

    try:
        xlsx_bytes = build_bulk_xlsx(rows, tz_label_map=tz_label_map)
    except Exception as e:
        log.error(
            "Bulk XLSX generation failed for HR %s: %s: %s",
            hr.id, type(e).__name__, e,
        )
        raise HTTPException(
            status_code=500,
            detail="Could not generate the Excel export. Please try again or contact support.",
        )

    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    fname = f"FluentiQ_Candidates_{date_str}.xlsx"

    return StreamingResponse(
        BytesIO(xlsx_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": _content_disposition(fname)},
    )