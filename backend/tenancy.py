"""
Tenant scoping — the single source of truth for "what can this caller see?"

This module is the safety perimeter for multi-tenancy. Every route that
queries tenant-scoped data (invitations, content tables, admin user
listings) calls into here instead of writing the filter inline.

Why centralized: pre-multi-tenancy the code had 10+ inline
`hr_admin_id == hr.id` filters spread across hr.py and hr_reports.py.
Migrating to multi-tenant filtering inline would mean repeating the
role-aware logic 10+ times. One missed site = a data leak. One helper
= one place to audit.

Three pairs of helpers, one per tenant-scoped concept:

  INVITATIONS
    tenant_scope_invitations(query, principal)
        List endpoints — returns query with WHERE filter applied.
    assert_can_access_invitation(inv, principal)
        Single-row endpoints — raises 404 on cross-tenant.
    get_invitation_or_404(db, id, principal)
        Convenience wrapper combining fetch + assert.

  CONTENT (passages, questions, writing_topics, speaking_topics)
    tenant_scope_content_read(query, model, principal)
        Returns query with the right "own-org + global" filter applied.
        Used by every list-content endpoint.
    assert_can_edit_content(row, principal)
        Single-row write guard. HR/admin can edit only their own org's
        content; super edits anything. NULL-org (global) content is
        super-only — HR/admin trying to edit returns 403.
    new_content_org_id(principal, requested_org_id=None)
        Computes the organization_id to stamp on a newly-created content
        row. HR/admin always get their own org. Super can choose (None
        for global, or explicit org id).

Multi-tenancy rules (mirrors the design doc):
  super  → sees everything across all orgs; edits anything; can author
           into any org or the global pool (NULL org_id).
  admin  → sees own org + global; edits own org only (never global);
           authors into their own org.
  hr     → invitations: sees only their own (hr_admin_id == self.id).
           content: sees own org + global; edits own org only.

For each role we trust the principal's role and organization_id fields
from auth.Principal. Those are populated from the live DB row by
require_principal — they can't be forged by a JWT replay because the
DB row drives them, not the token claims.
"""
from __future__ import annotations

from typing import Optional, Type

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Query

from auth import Principal
from models import Invitation


# ============================================================
# Generic 404 — used when access is denied OR row is missing.
# Same message for both so cross-tenant access doesn't leak existence.
# ============================================================
_INVITATION_NOT_FOUND = "Invitation not found."
_CONTENT_NOT_FOUND = "Content not found."

# 403 for "you tried to edit global content but you're not super." This
# IS distinguishable from a missing/cross-tenant row because the row
# being visible (global content is visible to everyone) means the
# caller already knows it exists. Hiding the reason would be confusing
# and serves no security purpose.
_GLOBAL_CONTENT_FORBIDDEN = (
    "Global content can only be modified by a Stixis super-admin."
)


# ============================================================
# Invitation scoping (Step B — unchanged)
# ============================================================
def tenant_scope_invitations(query: Query, principal: Principal) -> Query:
    """
    Apply the role-appropriate tenant filter to an Invitation query.

    Usage:
        q = db.query(Invitation).order_by(Invitation.created_at.desc())
        q = tenant_scope_invitations(q, principal)
        results = q.all()

    The function ONLY adds a WHERE clause. It does NOT add ORDER BY,
    LIMIT, OFFSET, or any other clause. The caller controls the rest.
    """
    if principal.role == "super":
        return query

    if principal.role == "admin":
        if principal.organization_id is None:
            raise RuntimeError(
                "Principal has role='admin' but organization_id is None. "
                "DB CHECK constraint should have prevented this. Investigate."
            )
        return query.filter(Invitation.organization_id == principal.organization_id)

    if principal.role == "hr":
        return query.filter(Invitation.hr_admin_id == principal.user.id)

    raise RuntimeError(
        f"tenant_scope_invitations: unknown role {principal.role!r}. "
        f"Update this function when adding new roles."
    )


def assert_can_access_invitation(inv: Invitation, principal: Principal) -> None:
    """
    Raise HTTPException(404) if the principal isn't allowed to see this
    invitation.

    404 (not 403) is intentional: same generic message whether the row
    doesn't exist OR exists-but-isn't-yours. Distinct 403 for cross-tenant
    would let an attacker enumerate invitation IDs.
    """
    if principal.role == "super":
        return

    if principal.role == "admin":
        if principal.organization_id is None:
            raise RuntimeError(
                "Principal has role='admin' but organization_id is None. "
                "DB CHECK constraint should have prevented this. Investigate."
            )
        if inv.organization_id != principal.organization_id:
            raise HTTPException(status_code=404, detail=_INVITATION_NOT_FOUND)
        return

    if principal.role == "hr":
        if inv.hr_admin_id != principal.user.id:
            raise HTTPException(status_code=404, detail=_INVITATION_NOT_FOUND)
        return

    raise RuntimeError(
        f"assert_can_access_invitation: unknown role {principal.role!r}. "
        f"Update this function when adding new roles."
    )


def get_invitation_or_404(db, invitation_id: int, principal: Principal) -> Invitation:
    """Convenience: fetch invitation by id, enforce tenancy, return row.
    Raises 404 on missing OR cross-tenant — same generic message either way."""
    inv = db.query(Invitation).filter(Invitation.id == invitation_id).first()
    if inv is None:
        raise HTTPException(status_code=404, detail=_INVITATION_NOT_FOUND)
    assert_can_access_invitation(inv, principal)
    return inv


# ============================================================
# Content scoping (Step C — passages, questions, writing/speaking topics)
# ============================================================
def tenant_scope_content_read(query: Query, model: Type, principal: Principal) -> Query:
    """
    Apply the read-time filter for content tables (Passage, Question,
    WritingTopic, SpeakingTopic). Read scope is broader than write:

      super  → all content (no filter)
      admin  → own org + global (organization_id IS NULL)
      hr     → own org + global (same as admin)

    The "own org + global" rule is what makes the seed content (NULL
    org_id, populated by Stixis at install) visible to every customer
    org without each org having to duplicate it.

    Why pass `model` explicitly instead of inferring from the query?
    The query might already have JOINs, aliases, or subqueries that
    obscure the underlying table. Explicit model = no ambiguity. Caller
    writes:
        tenant_scope_content_read(db.query(Passage), Passage, principal)

    Read this carefully: there's NO restriction here for HR vs admin
    within an org. The "per-HR" isolation that applies to invitations
    does NOT apply to content. Content authored by HR-A is visible to
    HR-B at the same org. That's intentional — content is an org-level
    asset, not a personal one.
    """
    if principal.role == "super":
        return query

    if principal.role in ("admin", "hr"):
        if principal.organization_id is None:
            raise RuntimeError(
                f"Principal has role={principal.role!r} but organization_id "
                f"is None. DB CHECK constraint should have prevented this. "
                f"Investigate."
            )
        # Own-org OR global (NULL). The IS NULL clause is what makes
        # globally-seeded content visible without each org owning it.
        return query.filter(
            or_(
                model.organization_id == principal.organization_id,
                model.organization_id.is_(None),
            )
        )

    raise RuntimeError(
        f"tenant_scope_content_read: unknown role {principal.role!r}. "
        f"Update this function when adding new roles."
    )


def assert_can_edit_content(row, principal: Principal) -> None:
    """
    Single-row write guard for content tables. Called by update/delete/
    toggle-disable endpoints after fetching the row.

    Rules:
      super       → can edit anything (including global).
      admin/hr    → can edit ONLY rows in their own org.
                    Editing global (org_id IS NULL) is REFUSED with 403.
                    Editing another org's content is REFUSED with 404
                    (cross-tenant; don't leak existence).

    The 403-vs-404 distinction matters:
      - Global content is visible in read endpoints to admin/hr; trying
        to edit it returns 403 with a clear "ask super" message because
        the caller already knows it exists.
      - Cross-org content is NOT visible to admin/hr (filtered out by
        read scope); trying to edit it returns 404, same as a missing
        row, so admin/hr can't probe cross-org content by edit attempts.
    """
    if principal.role == "super":
        return  # super edits anything

    if principal.role in ("admin", "hr"):
        if principal.organization_id is None:
            raise RuntimeError(
                f"Principal has role={principal.role!r} but organization_id "
                f"is None. DB CHECK constraint should have prevented this."
            )
        # Global content: visible to caller, but they can't edit it.
        # 403 with a clear message — they CAN see this row exists,
        # they just can't change it.
        if row.organization_id is None:
            raise HTTPException(
                status_code=403,
                detail=_GLOBAL_CONTENT_FORBIDDEN,
            )
        # Cross-org content: not visible to caller in reads. Pretend it
        # doesn't exist when they try to edit it.
        if row.organization_id != principal.organization_id:
            raise HTTPException(
                status_code=404,
                detail=_CONTENT_NOT_FOUND,
            )
        # Same org — allowed.
        return

    raise RuntimeError(
        f"assert_can_edit_content: unknown role {principal.role!r}. "
        f"Update this function when adding new roles."
    )


def new_content_org_id(
    principal: Principal,
    requested_org_id: Optional[int] = None,
) -> Optional[int]:
    """
    Compute the organization_id to stamp on a newly-created content row.

    Rules:
      super  → uses requested_org_id as-is (None = global, int = that org).
               This is how Stixis seeds the global pool: super POSTs with
               no org_id and the row goes into the global pool.
      admin  → always own org. requested_org_id is IGNORED (silently — we
               don't want a curious admin trying to plant content in another
               org and getting a useful error message; just normalize
               whatever they sent).
      hr     → same as admin.

    Returns the int org_id or None for global.
    """
    if principal.role == "super":
        return requested_org_id  # None means global

    if principal.role in ("admin", "hr"):
        # HR/admin cannot author into global pool or another org. Always
        # own-org regardless of what was requested. We don't error if they
        # passed a different value — silently normalize. (The frontend
        # never sends organization_id for these roles anyway; this is
        # defense against direct API calls.)
        if principal.organization_id is None:
            raise RuntimeError(
                f"Principal has role={principal.role!r} but organization_id "
                f"is None. DB CHECK constraint should have prevented this."
            )
        return principal.organization_id

    raise RuntimeError(
        f"new_content_org_id: unknown role {principal.role!r}. "
        f"Update this function when adding new roles."
    )


# ============================================================
# Candidate content filtering (used at exam-assignment time)
# ============================================================
# Map of SQLAlchemy content model → string label stored in
# organization_content_disable.content_type. Used by both the per-org
# disable endpoints and the candidate-side filter so the same content
# type vocabulary applies on both sides.
_CANDIDATE_CONTENT_TYPES: dict[type, str] = {}


def _register_candidate_content_types() -> None:
    """Populated lazily on first call to avoid import cycles with models.py."""
    if _CANDIDATE_CONTENT_TYPES:
        return
    from models import Passage, Question, SpeakingTopic, WritingTopic
    _CANDIDATE_CONTENT_TYPES.update({
        Passage: "passage",
        Question: "question",
        SpeakingTopic: "speaking_topic",
        WritingTopic: "writing_topic",
    })


def tenant_scope_candidate_content(model, *, organization_id: int):
    """
    WHERE clause for a candidate loading content during their exam.

    Includes:
      - Org-private content for the invitation's org
      - Global content (organization_id IS NULL)

    Excludes:
      - Global content the invitation's org has explicitly disabled via
        organization_content_disable.

    Usage:
        rows = db.query(Question).filter(
            tenant_scope_candidate_content(Question, organization_id=inv.organization_id)
        ).all()

    Raises ValueError if `model` is not one of the four candidate-facing
    content tables — guard against silently filtering by the wrong type
    (which would always return zero rows because the content_type label
    wouldn't match anything in organization_content_disable).
    """
    from sqlalchemy import and_, or_, not_, exists
    from models import OrganizationContentDisable

    _register_candidate_content_types()
    content_type = _CANDIDATE_CONTENT_TYPES.get(model)
    if content_type is None:
        raise ValueError(
            f"tenant_scope_candidate_content: model {model!r} is not a "
            f"candidate-facing content table. Expected one of "
            f"{[m.__name__ for m in _CANDIDATE_CONTENT_TYPES]}."
        )

    return and_(
        or_(
            model.organization_id == organization_id,
            model.organization_id.is_(None),
        ),
        not_(
            exists().where(and_(
                OrganizationContentDisable.organization_id == organization_id,
                OrganizationContentDisable.content_type == content_type,
                OrganizationContentDisable.content_id == model.id,
            ))
        ),
    )