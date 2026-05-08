"""
Scheduler — creates Microsoft Teams online meetings via the Graph API.

Two strategies:
  1. OnlineMeetings API  — supports `recordAutomatically`, but requires a
     Teams Application Access Policy.
  2. Calendar Events API — no policy needed; creates a calendar event with a
     Teams meeting link.  Auto-recording is NOT available via this route
     (must be enabled in the Teams meeting policy or by the organizer).
"""

import logging
import requests
from datetime import datetime, timedelta, timezone
from .auth import auth_provider
from .config import settings


# Module-level logger. Routes to FluentiQ's uvicorn server log so meeting-
# creation success/failure shows up alongside other backend events.
log = logging.getLogger("teams.scheduler")


# ─────────────────────────────────────────────────────────────────────────────
# Strategy 1 — OnlineMeetings API (preferred, needs Application Access Policy)
# ─────────────────────────────────────────────────────────────────────────────

def _schedule_via_online_meetings(
    subject: str,
    participant_name: str,
    participant_email: str,
    start_dt: datetime,
    end_dt: datetime,
    token: str,
    # Optional second attendee. When the candidate is the participant
    # above and HR will observe the meeting, FluentiQ passes HR's name +
    # email here so HR also gets a calendar invite and one-click join.
    # Standalone callers don't pass these — behavior unchanged for them.
    hr_name: str | None = None,
    hr_email: str | None = None,
) -> dict | None:
    """Try creating via /users/{id}/onlineMeetings. Returns None on 403."""

    # Build the attendee list. Always include the primary participant
    # (the candidate). If HR info was passed, include HR too — both will
    # receive calendar invites in their Outlook with one-click join.
    attendees = [
        {
            "upn": participant_email,
            "identity": {
                "user": {"displayName": participant_name}
            },
            "role": "attendee",
        }
    ]
    if hr_email:
        attendees.append({
            "upn": hr_email,
            "identity": {
                "user": {"displayName": hr_name or hr_email}
            },
            "role": "attendee",
        })

    payload = {
        "subject": subject,
        "startDateTime": start_dt.isoformat(),
        "endDateTime": end_dt.isoformat(),
        "recordAutomatically": True,
        "lobbyBypassSettings": {
            "scope": "everyone",
            "isDialInBypassEnabled": True,
        },
        "participants": {
            "attendees": attendees,
        },
    }

    url = f"{settings.GRAPH_API_BASE}/users/{settings.ORGANIZER_USER_ID}/onlineMeetings"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    resp = requests.post(url, json=payload, headers=headers, timeout=30)

    if resp.status_code == 403:
        raise RuntimeError(f"Graph API error 403: {resp.text}")

    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Graph API error {resp.status_code}: {resp.text}")

    data = resp.json()
    return {
        "meeting_id": data.get("id"),
        "join_url": data.get("joinWebUrl"),
        "join_link": data.get("joinUrl"),
        "subject": data.get("subject"),
        "start_time": data.get("startDateTime"),
        "end_time": data.get("endDateTime"),
        "auto_recording": data.get("recordAutomatically"),
        "toll_number": (data.get("audioConferencing") or {}).get("tollNumber"),
        "conference_id": (data.get("audioConferencing") or {}).get("conferenceId"),
        "method": "onlineMeetings",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Strategy 2 — Calendar Events API (fallback, no policy needed)
# ─────────────────────────────────────────────────────────────────────────────

def _schedule_via_calendar_event(
    subject: str,
    participant_name: str,
    participant_email: str,
    start_dt: datetime,
    end_dt: datetime,
    token: str,
    # Optional second attendee. Same pattern as
    # _schedule_via_online_meetings — when FluentiQ calls this with HR
    # info, both the candidate and HR get added to the event's attendees
    # list and Outlook sends both of them calendar invites automatically
    # (this is the API that actually drives the calendar invite emails).
    hr_name: str | None = None,
    hr_email: str | None = None,
) -> dict:
    """Create a calendar event with a Teams meeting link attached."""

    # Build the attendees list. The candidate is always required.
    # If HR info was passed, HR is also added as required — Outlook
    # will then send calendar invite emails to both.
    attendees = [
        {
            "emailAddress": {
                "address": participant_email,
                "name": participant_name,
            },
            "type": "required",
        }
    ]
    if hr_email:
        attendees.append({
            "emailAddress": {
                "address": hr_email,
                "name": hr_name or hr_email,
            },
            "type": "required",
        })

    payload = {
        "subject": subject,
        "start": {
            "dateTime": start_dt.strftime("%Y-%m-%dT%H:%M:%S"),
            "timeZone": "UTC",
        },
        "end": {
            "dateTime": end_dt.strftime("%Y-%m-%dT%H:%M:%S"),
            "timeZone": "UTC",
        },
        "isOnlineMeeting": True,
        "onlineMeetingProvider": "teamsForBusiness",
        "attendees": attendees,
    }

    url = f"{settings.GRAPH_API_BASE}/users/{settings.ORGANIZER_USER_ID}/calendar/events"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    resp = requests.post(url, json=payload, headers=headers, timeout=30)

    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Graph API error {resp.status_code}: {resp.text}")

    data = resp.json()
    online_meeting = data.get("onlineMeeting") or {}

    return {
        "meeting_id": data.get("id"),
        "join_url": online_meeting.get("joinUrl"),
        "join_link": online_meeting.get("joinUrl"),
        "subject": data.get("subject"),
        "start_time": (data.get("start") or {}).get("dateTime"),
        "end_time": (data.get("end") or {}).get("dateTime"),
        "auto_recording": None,  # Not available via Calendar API
        "toll_number": online_meeting.get("tollNumber"),
        "conference_id": online_meeting.get("conferenceId"),
        "method": "calendarEvent",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Strategy 3 — Create a calendar event on HR's mailbox (no email round-trip)
# ─────────────────────────────────────────────────────────────────────────────

def _create_event_on_hr_calendar(
    hr_email: str,
    candidate_name: str,
    candidate_email: str,
    start_dt: datetime,
    end_dt: datetime,
    teams_join_url: str,
    token: str,
) -> dict | None:
    """
    Create a calendar event directly on HR's Outlook calendar so the
    interview shows up at the right time. Embeds the Teams join URL in
    the event body so HR can click Join from their calendar at meeting
    time — no need to copy the URL from FluentiQ.

    Endpoint: POST /users/{hr_email}/calendar/events
    Permission required: Calendars.ReadWrite (Application).

    Until the Stixis Azure admin grants Calendars.ReadWrite + admin
    consent, this function will return None on every call (Graph API
    returns 403 ErrorAccessDenied). The error is logged but does NOT
    raise — the caller treats this as best-effort and the invitation
    still succeeds. HR can copy the Teams URL from the FluentiQ
    dashboard modal in the meantime.

    Once the permission is granted, this function starts succeeding
    with zero code changes — events appear on HR's calendar
    automatically.

    Args:
        hr_email:         The HR's email address; identifies whose
                          calendar to write to.
        candidate_name:   Used in the event subject and body.
        candidate_email:  Shown in the event body for HR's reference.
        start_dt:         Event start (timezone-aware UTC).
        end_dt:           Event end (timezone-aware UTC).
        teams_join_url:   The join URL produced by the OnlineMeetings
                          call. Embedded in the event body so HR can
                          click Join from the calendar event.
        token:            Bearer token for Graph API.

    Returns:
        Dict containing the created event's id, webLink, and
        start/end timestamps. Or None on any failure (logged).
    """
    payload = {
        "subject": f"FluentiQ Interview — {candidate_name}",
        "start": {
            "dateTime": start_dt.strftime("%Y-%m-%dT%H:%M:%S"),
            "timeZone": "UTC",
        },
        "end": {
            "dateTime": end_dt.strftime("%Y-%m-%dT%H:%M:%S"),
            "timeZone": "UTC",
        },
        "body": {
            "contentType": "HTML",
            "content": (
                f"<p><strong>Candidate:</strong> {candidate_name} "
                f"({candidate_email})</p>"
                f"<p><strong>Microsoft Teams join link:</strong></p>"
                f'<p><a href="{teams_join_url}">{teams_join_url}</a></p>'
            ),
        },
        # 15-minute reminder pop-up before the meeting starts. Outlook
        # honors this as the desktop notification HR will see.
        "reminderMinutesBeforeStart": 15,
        "isReminderOn": True,
    }

    url = f"{settings.GRAPH_API_BASE}/users/{hr_email}/calendar/events"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
    except requests.RequestException as exc:
        # Network-level failure — DNS, timeout, connection refused.
        # Distinct from a Graph API error response (which has a body
        # we want to log). Both are non-fatal for the caller.
        log.error(
            f"[teams] HR calendar event request failed (network) for "
            f"{hr_email}: {exc}"
        )
        return None

    if resp.status_code not in (200, 201):
        # Most likely 403 ErrorAccessDenied while Calendars.ReadWrite
        # permission isn't granted. The full response body has the
        # specific error code so admin can confirm what's missing.
        log.error(
            f"[teams] HR calendar event creation failed for {hr_email}: "
            f"{resp.status_code} {resp.text}"
        )
        return None

    data = resp.json()
    log.info(
        f"[teams] event created on {hr_email}'s calendar at "
        f"{start_dt.isoformat()} (event_id={data.get('id', '')[:24]}...)"
    )
    return {
        "event_id": data.get("id"),
        "web_link": data.get("webLink"),
        "subject": data.get("subject"),
        "start_time": (data.get("start") or {}).get("dateTime"),
        "end_time": (data.get("end") or {}).get("dateTime"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Strategy 4 — Cancel an existing Teams meeting (used on resend)
# ─────────────────────────────────────────────────────────────────────────────

def cancel_teams_meeting(meeting_id: str) -> bool:
    """
    Delete a previously-created Teams online meeting via Microsoft Graph.

    Used by FluentiQ when HR resends an invitation with a NEW time window —
    we create a fresh meeting at the new time and cancel the old one so it
    doesn't sit orphaned in Microsoft's system (and so any links the
    candidate received in earlier emails stop working).

    Endpoint: DELETE /users/{ORGANIZER_USER_ID}/onlineMeetings/{meeting_id}
    Permission: same OnlineMeetings.ReadWrite.All scope already granted —
    no new permission grant required.

    Best-effort: this function NEVER raises. Returns True on success,
    False on any failure (logged). The caller (resend route) treats a
    False return as "old meeting still exists, but the new one was
    created successfully so the candidate's invitation works fine" —
    we don't fail the resend over a stale meeting cleanup.

    Args:
        meeting_id: The Microsoft Graph meeting id stored on the
                    Invitation row as `teams_meeting_id`. None or empty
                    string is treated as "nothing to cancel" and returns
                    True (no-op success).

    Returns:
        True if the meeting was deleted (HTTP 204) or didn't exist anymore
        (HTTP 404 — already gone, treat as success).
        False on any other failure (network, 4xx other than 404, 5xx).
    """
    if not meeting_id:
        # Nothing to cancel — older invitations created before the Teams
        # integration shipped won't have a teams_meeting_id. Treat as a
        # successful no-op so the caller's resend logic stays simple.
        return True

    try:
        token = auth_provider.get_access_token()
    except Exception as exc:
        log.error(f"[teams] cancel_teams_meeting: token fetch failed: {exc!r}")
        return False

    url = (
        f"{settings.GRAPH_API_BASE}/users/{settings.ORGANIZER_USER_ID}"
        f"/onlineMeetings/{meeting_id}"
    )
    headers = {"Authorization": f"Bearer {token}"}

    try:
        resp = requests.delete(url, headers=headers, timeout=30)
    except requests.RequestException as exc:
        log.error(
            f"[teams] cancel meeting request failed (network) for "
            f"id={meeting_id[:24]}...: {exc}"
        )
        return False

    # 204 No Content = deleted successfully (Graph's documented success code).
    # 404 Not Found = already gone (deleted manually, or meeting expired and
    # was reaped by Microsoft). Either way it's no longer there, which is
    # what the caller wants — treat both as success.
    if resp.status_code in (204, 404):
        log.info(
            f"[teams] meeting cancelled (status={resp.status_code}) "
            f"id={meeting_id[:24]}..."
        )
        return True

    log.error(
        f"[teams] cancel meeting failed for id={meeting_id[:24]}...: "
        f"{resp.status_code} {resp.text[:200]}"
    )
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Public function — tries Strategy 1, falls back to Strategy 2
# ─────────────────────────────────────────────────────────────────────────────

def schedule_teams_meeting(
    subject: str,
    participant_name: str,
    participant_email: str,
    start_time: str,
    duration_minutes: int = 60,
    # Optional HR observer. When FluentiQ creates an interview meeting,
    # it passes the HR's name + email here so HR is added as a second
    # attendee on the meeting (gets calendar invite + one-click join).
    # Standalone callers can omit these — only the candidate is invited.
    hr_name: str | None = None,
    hr_email: str | None = None,
) -> dict:
    """
    Schedule a Teams meeting via Microsoft Graph.

    Currently uses the /onlineMeetings API:
      ✅ Per-meeting auto-recording (recordAutomatically=true honored).
      ❌ Does NOT send Outlook calendar invites to attendees, even though
         the API accepts a participants.attendees array. The URL exists
         but nothing lands on HR's or the candidate's calendar.

    To make HR see the meeting on their Outlook calendar, FluentiQ
    additionally needs to create a calendar event directly on HR's
    mailbox via POST /users/{hr_email}/calendar/events. That endpoint
    requires the Calendars.ReadWrite Azure AD application permission,
    which the Stixis admin needs to grant + admin-consent before the
    feature can ship. Until then the OnlineMeetings-only path is what
    runs — the candidate still gets the join URL in their FluentiQ
    invite email; HR copies the URL from the dashboard if they need it.

    The _schedule_via_calendar_event helper below was a previous attempt
    at solving this (have the calendar API create the meeting AND send
    invites in one call). It also requires Calendars.ReadWrite, so when
    the permission is granted we have two viable code paths and can
    pick whichever fits the admin's preference.

    Args:
        subject:            Meeting title / subject line.
        participant_name:   Display name of the invited participant (candidate).
        participant_email:  Email address of the invited participant (candidate).
        start_time:         ISO-8601 datetime string (e.g. "2026-05-10T14:00:00").
        duration_minutes:   Length of the meeting in minutes (default 60).
        hr_name:            Optional HR display name — added as a second
                            attendee when provided (FluentiQ use case).
        hr_email:           Optional HR email — added as a second attendee
                            when provided (FluentiQ use case).

    Returns:
        A dict with meeting details including the join URL.

    Raises:
        RuntimeError: if Graph API returns a non-2xx response. The caller
                      (FluentiQ /api/hr/invite) translates this to HTTP 502
                      and aborts the invitation, so HR sees a clear error
                      and no half-created invitation lands in the DB.
    """
    token = auth_provider.get_access_token()

    # Build start / end datetimes from the ISO start_time + duration.
    start_dt = datetime.fromisoformat(start_time)
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)
    end_dt = start_dt + timedelta(minutes=duration_minutes)

    # OnlineMeetings API — supports auto-recording. Does not send invites.
    result = _schedule_via_online_meetings(
        subject, participant_name, participant_email, start_dt, end_dt, token,
        hr_name=hr_name, hr_email=hr_email,
    )

    if result is not None:
        log.info("[teams] meeting created via OnlineMeetings API (auto-recording ON)")

        # Best-effort: drop a calendar event on HR's mailbox so they see
        # the interview on their Outlook calendar at meeting time. Wrapped
        # in try/except because the invitation has already been created at
        # this point — any failure here (403 missing permission, network
        # error, anything) gets logged but MUST NOT propagate up and cause
        # the route to return 502. The candidate already got their email,
        # the meeting URL exists; HR can copy the URL manually if their
        # calendar event creation didn't make it.
        if hr_email and result.get("join_url"):
            try:
                _create_event_on_hr_calendar(
                    hr_email=hr_email,
                    candidate_name=participant_name,
                    candidate_email=participant_email,
                    start_dt=start_dt,
                    end_dt=end_dt,
                    teams_join_url=result["join_url"],
                    token=token,
                )
            except Exception as exc:
                # Belt-and-braces. _create_event_on_hr_calendar already
                # catches the expected failure modes (network, 4xx) and
                # returns None. This catches anything truly unexpected
                # (e.g. KeyError on a malformed Graph response, JSON
                # decode error) so the invitation is never killed by a
                # surprise from this best-effort step.
                log.error(
                    f"[teams] unexpected error in HR calendar event creation "
                    f"for {hr_email}: {exc!r}"
                )

        return result

    # OnlineMeetings returned None — typically a 403 because the Application
    # Access Policy isn't configured for the organizer user. The only
    # documented fix is for a Teams admin to run:
    #   New-CsApplicationAccessPolicy -Identity <id> -AppIds @("<client_id>")
    #   Grant-CsApplicationAccessPolicy -PolicyName <id> -Identity <user>
    # Logging makes the failure visible in the server log so HR / ops can
    # forward the message to admin without having to dig through Graph
    # error JSON. The route caller turns the None into HTTP 502.
    log.error(
        "[teams] OnlineMeetings API returned None — likely 403 from missing "
        "Application Access Policy. Ask Teams admin to run "
        "New-CsApplicationAccessPolicy / Grant-CsApplicationAccessPolicy."
    )
    return result