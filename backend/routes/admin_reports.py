"""
Admin-only report download endpoints.

Three endpoints, all gated behind require_admin_strict so an HR-role
session cannot reach them even if they know the URL:

  - GET /api/admin/exports/all-candidates.xlsx
        Bulk Excel of every candidate from every HR. Adds an "HR Admin"
        column so the admin can tell who sent each invitation.

  - GET /api/admin/hrs/{hr_id}/candidates.xlsx
        Bulk Excel of one specific HR's candidates. Same data shape as
        the HR's own /api/hr/exports/candidates.xlsx — admin can run it
        for any HR without impersonating them.

  - GET /api/admin/results/{invitation_id}/report.pdf
        Per-candidate PDF, admin override (no hr_admin_id filter — admin
        can pull any invitation's report regardless of which HR sent it).

Why these live in a separate router file rather than inside admin.py:
admin.py is already 600+ lines and handles auth, user CRUD, and
candidate listing. Reports are a self-contained concern with their own
dependencies (reportlab, openpyxl) — splitting keeps the import surface
of admin.py small and makes it obvious which file to touch when reports
change.

URL structure mirrors the HR-side equivalents under /api/hr/exports/...
and /api/hr/results/.../report.pdf — same shape, different prefix and
different auth dep. That parallel makes the codebase easier to navigate.
"""
import logging
from io import BytesIO

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from auth import require_admin_strict, Principal, require_principal
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


log = logging.getLogger("admin.reports")


router = APIRouter(prefix="/api/admin", tags=["admin-reports"])


# ------------------------------------------------------------------
# Helpers — shared timezone/HR label resolution
# ------------------------------------------------------------------
def _resolve_tz_labels(invitations: list[Invitation], db: Session) -> dict[str, str]:
    """
    Pre-fetch SupportedTimezone short labels for every distinct zone used
    in the given invitations. Single grouped query; avoids the N+1 trap
    when the export contains many invitations across a few zones.
    """
    tz_names = {
        inv.display_timezone for inv in invitations
        if inv.display_timezone and inv.display_timezone != "UTC"
    }
    if not tz_names:
        return {}
    tz_rows = db.query(SupportedTimezone).filter(
        SupportedTimezone.iana_name.in_(tz_names)
    ).all()
    return {row.iana_name: row.short_label for row in tz_rows}


def _resolve_hr_labels(invitations: list[Invitation], db: Session) -> dict[int, str]:
    """
    Build a hr_admin_id → "Name <email>" lookup for the HR Admin column
    in the all-candidates export. One query for every distinct HR id
    referenced by the invitation set.

    Soft-deleted HRs are still surfaced — historical invitations point
    at hr_admin_id values that may no longer be active, but the Excel
    must still attribute them. We don't filter on deleted_at IS NULL
    here for that reason. If the row truly doesn't exist (a bug or
    pre-FK data), the fallback "—" in build_bulk_xlsx kicks in.
    """
    hr_ids = {inv.hr_admin_id for inv in invitations if inv.hr_admin_id}
    if not hr_ids:
        return {}
    hr_rows = db.query(HRAdmin).filter(HRAdmin.id.in_(hr_ids)).all()
    return {hr.id: f"{hr.name} <{hr.email}>" for hr in hr_rows}


# ------------------------------------------------------------------
# Excel: all candidates from all HRs (admin-wide)
# ------------------------------------------------------------------
@router.get("/exports/all-candidates.xlsx")
def download_all_candidates_xlsx(
    p: Principal = Depends(require_principal(allow=("super", "admin"), strict=True)),
    db: Session = Depends(get_db),
):
    """
    Returns an XLSX containing every invitation visible to the caller,
    ordered newest-first. An "HR Admin" column is included after
    Invitation ID so each row carries its own HR attribution — without
    this, a 5000-row export couldn't be parsed without joining elsewhere.

    Multi-tenancy:
      super → every invitation across all orgs.
      admin → every invitation in admin's own org (across all HRs in
              that org). Cross-org invitations are silently excluded
              by tenant_scope_invitations.

    No filter on Invitation soft-delete state because Invitation doesn't
    soft-delete in the current schema. Add a deleted_at filter here if
    that ever changes.
    """
    invitations = (
        tenant_scope_invitations(db.query(Invitation), p)
        .order_by(Invitation.created_at.desc())
        .all()
    )

    # Pair each invitation with its score (loaded lazily by relationship,
    # same pattern as the HR side). Done in Python rather than via a
    # join because the row count is bounded — and keeps the formatter
    # callable from tests with a simple list of mocks.
    rows = [(inv, inv.score) for inv in invitations]

    tz_label_map = _resolve_tz_labels(invitations, db)
    hr_label_map = _resolve_hr_labels(invitations, db)

    try:
        xlsx_bytes = build_bulk_xlsx(
            rows,
            tz_label_map=tz_label_map,
            hr_label_map=hr_label_map,
        )
    except Exception:
        log.exception("All-candidates XLSX generation failed")
        raise HTTPException(
            status_code=500,
            detail="Could not generate the export. Please try again or contact support.",
        )

    filename = "FluentiQ_AllCandidates.xlsx"
    return StreamingResponse(
        BytesIO(xlsx_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ------------------------------------------------------------------
# Excel: one specific HR's candidates (admin running it for any HR)
# ------------------------------------------------------------------
@router.get("/hrs/{hr_id}/candidates.xlsx")
def download_hr_candidates_xlsx(
    hr_id: int,
    p: Principal = Depends(require_principal(allow=("super", "admin"), strict=True)),
    db: Session = Depends(get_db),
):
    """
    Returns an XLSX of all candidates belonging to the given HR.

    Multi-tenancy:
      super → can fetch any HR's candidates across any org.
      admin → can fetch HRs in their OWN ORG only. Cross-org → 404.

    404 if hr_id doesn't match a non-soft-deleted HR account, if the
    target is an admin/super (those don't have invitations), or if the
    target is in a different org (for admin callers).

    No HR Admin column — the export is single-HR-scoped, the HR's
    identity is in the filename instead.
    """
    target = (
        db.query(HRAdmin)
        .filter(HRAdmin.id == hr_id, HRAdmin.deleted_at.is_(None))
        .first()
    )
    if target is None or target.role != "hr":
        raise HTTPException(status_code=404, detail="HR not found.")

    # Cross-org check for admin callers (super is unrestricted).
    if p.role == "admin" and target.organization_id != p.organization_id:
        raise HTTPException(status_code=404, detail="HR not found.")

    invitations = (
        db.query(Invitation)
        .filter(Invitation.hr_admin_id == hr_id)
        .order_by(Invitation.created_at.desc())
        .all()
    )

    rows = [(inv, inv.score) for inv in invitations]
    tz_label_map = _resolve_tz_labels(invitations, db)

    try:
        xlsx_bytes = build_bulk_xlsx(rows, tz_label_map=tz_label_map)
    except Exception:
        log.exception("HR-specific XLSX generation failed for hr_id=%s", hr_id)
        raise HTTPException(
            status_code=500,
            detail="Could not generate the export. Please try again or contact support.",
        )

    safe_name = "".join(c if c.isalnum() else "_" for c in target.name).strip("_")
    filename = f"FluentiQ_{safe_name or 'HR'}_Candidates.xlsx"

    return StreamingResponse(
        BytesIO(xlsx_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ------------------------------------------------------------------
# PDF: one candidate (admin override — any HR's invitation)
# ------------------------------------------------------------------
@router.get("/results/{invitation_id}/report.pdf")
def download_candidate_pdf(
    invitation_id: int,
    p: Principal = Depends(require_principal(allow=("super", "admin"), strict=True)),
    db: Session = Depends(get_db),
):
    """
    Returns the per-candidate PDF report.

    Multi-tenancy:
      super → can fetch the PDF for any invitation.
      admin → can fetch PDFs for invitations in their OWN ORG only.
              Cross-org → 404 (don't leak existence).

    All data fetched eagerly here so build_candidate_pdf stays pure
    (no DB access during PDF rendering — easier to test, easier to
    reason about).

    Returns 404 if the invitation doesn't exist OR if the caller can't
    access it. Returns 400 if the candidate hasn't submitted yet —
    generating a PDF for an in-progress test would be misleading.
    """
    inv = (
        db.query(Invitation)
        .filter(Invitation.id == invitation_id)
        .first()
    )
    if inv is None:
        raise HTTPException(status_code=404, detail="Invitation not found.")

    # Tenancy check — raises 404 on cross-tenant. Same generic message
    # as the missing-row 404 above.
    assert_can_access_invitation(inv, p)

    if inv.submitted_at is None:
        raise HTTPException(
            status_code=400,
            detail="This candidate has not submitted the test yet. "
                   "The report will be available after submission.",
        )

    score = inv.score
    wr = inv.writing_response

    # Look up the writing prompt text from the assigned topic id.
    writing_prompt = None
    if wr and wr.topic_id:
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

    # Resolve the candidate's display timezone for the "Test taken" timestamp.
    candidate_tz_name = inv.display_timezone or "UTC"
    candidate_tz_label = None
    if candidate_tz_name and candidate_tz_name != "UTC":
        tz_row = db.query(SupportedTimezone).filter(
            SupportedTimezone.iana_name == candidate_tz_name
        ).first()
        candidate_tz_label = tz_row.short_label if tz_row else candidate_tz_name

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
    except Exception:
        log.exception(
            "Admin PDF generation failed for invitation %s", invitation_id
        )
        raise HTTPException(
            status_code=500,
            detail="Could not generate the PDF report. Please try again or contact support.",
        )

    safe_name = "".join(c if c.isalnum() else "_" for c in (inv.candidate_name or "Candidate")).strip("_")
    filename = f"FluentiQ_Report_{safe_name or 'Candidate'}_{inv.id}.pdf"

    return StreamingResponse(
        BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )