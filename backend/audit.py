"""
Audit logging helper — single source of truth for writing audit_log rows.

Every privileged action that mutates another tenant's data (or any global
state) should call `record_audit(...)`. Centralizing the write keeps the
denormalized actor/target fields consistent and makes it impossible to
forget a field at one call site.

Key contract: record_audit() stages the row via db.add() but does NOT
commit. The caller commits the business operation AND the audit row in
the same transaction — so either both succeed or both roll back.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import Request
from sqlalchemy.orm import Session

from auth import Principal
from models import AuditLog


# Canonical action labels. Keep as module constants so typos are
# caught by lint/grep rather than landing in production.
ACTION_SUPER_LOGIN = "SUPER_LOGIN"
ACTION_SUPER_LOGOUT = "SUPER_LOGOUT"
ACTION_ORG_CREATE = "ORG_CREATE"
ACTION_ORG_RENAME = "ORG_RENAME"
ACTION_ORG_DISABLE = "ORG_DISABLE"
ACTION_ORG_ENABLE = "ORG_ENABLE"
ACTION_ORG_DELETE = "ORG_DELETE"

# target_type values — singular lowercase noun matching the model name.
TARGET_ORGANIZATION = "organization"
TARGET_HR_ADMIN = "hr_admin"


def _client_ip(request: Optional[Request]) -> Optional[str]:
    """Pull the client IP, honoring X-Forwarded-For if set."""
    if request is None:
        return None
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()[:45]
    if request.client is None:
        return None
    return request.client.host[:45]


def _user_agent(request: Optional[Request]) -> Optional[str]:
    if request is None:
        return None
    ua = request.headers.get("user-agent")
    if not ua:
        return None
    return ua[:500]


def record_audit(
    db: Session,
    *,
    principal: Optional[Principal],
    action: str,
    target_type: Optional[str] = None,
    target_id: Optional[int] = None,
    target_organization_id: Optional[int] = None,
    payload: Optional[dict[str, Any]] = None,
    request: Optional[Request] = None,
    # Overrides used when no Principal exists yet (e.g. SUPER_LOGIN
    # records the user that JUST authenticated, before require_principal
    # would have built a Principal object).
    actor_id: Optional[int] = None,
    actor_role: Optional[str] = None,
    actor_email: Optional[str] = None,
    actor_organization_id: Optional[int] = None,
) -> AuditLog:
    """
    Stage an audit_log row on the caller's DB session.

    Either pass `principal` (preferred) or the four `actor_*` overrides.

    Raises:
      ValueError if `action` is empty.
      ValueError if neither principal nor actor_id is provided.
    """
    if not action:
        raise ValueError("audit action label is required")

    if principal is not None:
        actor_id = principal.user.id
        actor_role = principal.role
        actor_email = principal.user.email
        actor_organization_id = principal.organization_id

    if actor_id is None:
        raise ValueError(
            "record_audit requires either `principal` or `actor_id`; both were None"
        )

    row = AuditLog(
        actor_id=actor_id,
        actor_role=actor_role,
        actor_email=actor_email,
        actor_organization_id=actor_organization_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        target_organization_id=target_organization_id,
        payload=payload,
        ip_address=_client_ip(request),
        user_agent=_user_agent(request),
    )
    db.add(row)
    return row
