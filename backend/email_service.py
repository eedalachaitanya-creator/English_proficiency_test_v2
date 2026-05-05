"""
Email service — sends candidate invitation emails over SMTP.

Why this module exists
----------------------
HR clicks "Invite candidate" → backend creates an invitation row + access code
→ this module emails the candidate with the URL and code.

Best-effort delivery: if SMTP fails (network, credentials, rate limit, etc.),
the invitation still succeeds in the database — HR can copy/paste from the
dashboard popup as before. The error is loud in the server log, never silent.

Office 365 SMTP quirk
---------------------
On many residential ISPs in India (Jio, Airtel), IPv6 routing to
smtp.office365.com hangs during the TLS handshake. Forcing IPv4 fixes it.
We monkey-patch socket.getaddrinfo at module import to filter out IPv6
results — only DNS resolutions inside this process are affected.

Configuration
-------------
Read from .env at startup:
  SMTP_HOST          e.g. smtp.office365.com
  SMTP_PORT          e.g. 587 (STARTTLS)
  SMTP_USER          full email of the authenticated mailbox
  SMTP_PASSWORD      app password (NOT the regular login password)
  SMTP_FROM_EMAIL    sender address — must match SMTP_USER for O365
  SMTP_FROM_NAME     display name candidates see in their inbox
"""
from __future__ import annotations

import os
import smtplib
import socket
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr

# zoneinfo is the stdlib IANA timezone database (Python 3.9+). On Windows
# the IANA database is not bundled with the OS — install tzdata if you see
# ZoneInfoNotFoundError: `pip install tzdata`.
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import certifi


# ---------------------------------------------------------------------------
# Force IPv4 for SMTP — workaround for ISP-level IPv6 routing failures.
# Replaces socket.getaddrinfo so every DNS lookup in this process returns
# IPv4 addresses only. Done at import time (not inside send_*) so we don't
# pay the patch cost on every send.
# ---------------------------------------------------------------------------
_orig_getaddrinfo = socket.getaddrinfo


def _ipv4_only_getaddrinfo(*args, **kwargs):
    return [r for r in _orig_getaddrinfo(*args, **kwargs) if r[0] == socket.AF_INET]


socket.getaddrinfo = _ipv4_only_getaddrinfo


# ---------------------------------------------------------------------------
# Read SMTP config once at import. None means "not configured" — every send
# is short-circuited with a warning instead of trying to connect.
# ---------------------------------------------------------------------------
SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "").strip()
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "").strip()
SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL", "").strip()
SMTP_FROM_NAME = os.getenv("SMTP_FROM_NAME", "English Proficiency Test").strip()

_SMTP_CONFIGURED = bool(SMTP_HOST and SMTP_USER and SMTP_PASSWORD and SMTP_FROM_EMAIL)


def is_configured() -> bool:
    """Cheap check callers can use to decide whether to attempt a send."""
    return _SMTP_CONFIGURED


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def send_invitation_email(
    candidate_email: str,
    candidate_name: str,
    exam_url: str,
    access_code: str,
    valid_from,
    valid_until,
    hr_name: str | None = None,
    display_timezone: str | None = None,
    include_reading: bool = True,
    include_writing: bool = True,
    include_speaking: bool = True,
) -> tuple[bool, str | None]:
    """
    Send an invitation email containing the test URL and 6-digit access code.

    Returns a tuple (success, error_message):
      - (True, None)        if SMTP accepted the message
      - (False, "<reason>") if anything went wrong; <reason> is short and
                            safe to display to HR in the dashboard

    Never raises — the caller (the /invite route) wants the invitation
    creation to succeed even if email delivery doesn't, so HR can fall back
    to copy/paste from the dashboard popup.

    All failures are also printed to the server log with [smtp] prefix.
    """
    if not _SMTP_CONFIGURED:
        err = "SMTP not configured (missing env vars)"
        print(f"[smtp] SKIPPED: {err}. "
              "Set SMTP_HOST/SMTP_USER/SMTP_PASSWORD/SMTP_FROM_EMAIL in .env.")
        return (False, err)

    msg = _build_invitation_message(
        candidate_email=candidate_email,
        candidate_name=candidate_name,
        exam_url=exam_url,
        access_code=access_code,
        valid_from=valid_from,
        valid_until=valid_until,
        hr_name=hr_name,
        display_timezone=display_timezone,
        include_reading=include_reading,
        include_writing=include_writing,
        include_speaking=include_speaking,
    )

    try:
        # 15s timeout: if the server hasn't responded by then, give up — better
        # to fail fast than block the HR's UI for 60+ seconds.
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            # Negotiate TLS. Office 365 won't accept AUTH on a plaintext channel.
            # Pass certifi's CA bundle explicitly — the default macOS+Anaconda
            # Python SSL store often can't verify Microsoft / Google certs,
            # which surfaces as SSLCertVerificationError during STARTTLS.
            context = ssl.create_default_context(cafile=certifi.where())
            server.starttls(context=context)
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
        print(f"[smtp] sent invitation to {candidate_email}")
        return (True, None)
    except smtplib.SMTPAuthenticationError as e:
        # Wrong app password, expired credentials, or IT disabled SMTP AUTH.
        err = "SMTP authentication failed (check app password)"
        print(f"[smtp] AUTH FAILED for {SMTP_USER}: {e}. "
              "Regenerate the app password and update .env.")
        return (False, err)
    except (smtplib.SMTPException, OSError, TimeoutError) as e:
        # Catch-all for network errors, server errors, malformed responses, etc.
        # OSError covers ConnectionRefusedError, socket.timeout, DNS failures.
        # Cap the error message at 150 chars so we don't blow up the DB column.
        err = f"{type(e).__name__}: {str(e)[:150]}"
        print(f"[smtp] FAILED to send to {candidate_email}: {err}")
        return (False, err)


def send_regenerated_code_email(
    candidate_email: str,
    candidate_name: str,
    exam_url: str,
    access_code: str,
    valid_from=None,
    valid_until=None,
    hr_name: str | None = None,
    display_timezone: str | None = None,
    include_reading: bool = True,
    include_writing: bool = True,
    include_speaking: bool = True,
) -> tuple[bool, str | None]:
    """
    Send an email when HR regenerates a candidate's access code (e.g. after
    they got locked out from too many wrong attempts). Same return contract
    as send_invitation_email: (success, error_message).

    valid_from / valid_until are the candidate's scheduled URL window — both
    optional for backward compatibility but should be passed so the candidate
    sees when their (now-renewed) access code is actually valid.
    """
    if not _SMTP_CONFIGURED:
        err = "SMTP not configured (missing env vars)"
        print(f"[smtp] SKIPPED: {err}.")
        return (False, err)

    msg = _build_invitation_message(
        candidate_email=candidate_email,
        candidate_name=candidate_name,
        exam_url=exam_url,
        access_code=access_code,
        valid_from=valid_from,
        valid_until=valid_until,
        hr_name=hr_name,
        regenerated=True,
        display_timezone=display_timezone,
        include_reading=include_reading,
        include_writing=include_writing,
        include_speaking=include_speaking,
    )

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            # Pass certifi's CA bundle explicitly — the default macOS+Anaconda
            # Python SSL store often can't verify Microsoft / Google certs,
            # which surfaces as SSLCertVerificationError during STARTTLS.
            context = ssl.create_default_context(cafile=certifi.where())
            server.starttls(context=context)
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
        print(f"[smtp] sent regenerated-code email to {candidate_email}")
        return (True, None)
    except smtplib.SMTPAuthenticationError as e:
        err = "SMTP authentication failed (check app password)"
        print(f"[smtp] AUTH FAILED for {SMTP_USER}: {e}")
        return (False, err)
    except (smtplib.SMTPException, OSError, TimeoutError) as e:
        err = f"{type(e).__name__}: {str(e)[:150]}"
        print(f"[smtp] FAILED to send to {candidate_email}: {err}")
        return (False, err)


def send_temp_password_email(
    *,
    hr_email: str,
    hr_name: str,
    login_url: str,
    temp_password: str,
) -> tuple[bool, str | None]:
    """
    Send the HR a freshly-generated temporary password after they used
    the "Forgot password?" flow. Same (success, error) contract as the
    other email helpers — never raises. Returns (False, reason) so the
    caller can decide whether to commit the password change atomically
    (we don't want to update password_hash if the email never went out,
    or the user is locked out of their account).

    The temp password is plaintext in this email — same trade-off as
    send_hr_welcome_email. The email subject and body strongly prompt
    the recipient to change the password immediately after logging in.
    """
    if not _SMTP_CONFIGURED:
        err = "SMTP not configured (missing env vars)"
        print(f"[smtp] SKIPPED: {err}.")
        return (False, err)

    msg = EmailMessage()
    msg["Subject"] = "Your English Proficiency Test password has been reset"
    msg["From"] = formataddr((SMTP_FROM_NAME, SMTP_FROM_EMAIL))
    msg["To"] = hr_email
    msg["Reply-To"] = SMTP_FROM_EMAIL

    msg.set_content(
        f"Dear {hr_name},\n"
        f"\n"
        f"You (or someone using your email) requested a password reset for\n"
        f"your English Proficiency Test HR account. Your new temporary\n"
        f"password is below.\n"
        f"\n"
        f"--------------------------------------------\n"
        f"YOUR NEW TEMPORARY PASSWORD\n"
        f"--------------------------------------------\n"
        f"\n"
        f"  Login URL: {login_url}\n"
        f"  Email:     {hr_email}\n"
        f"  Password:  {temp_password}\n"
        f"\n"
        f"--------------------------------------------\n"
        f"IMPORTANT — change this password immediately\n"
        f"--------------------------------------------\n"
        f"\n"
        f"After you log in, click your account avatar in the top-right and\n"
        f"choose 'Change password' to set a new password you'll remember.\n"
        f"\n"
        f"If you did NOT request this reset, your account may have been\n"
        f"targeted. Reply to this email so we can investigate. Your old\n"
        f"password no longer works — anyone with this email can log in,\n"
        f"so treat the contents as sensitive.\n"
        f"\n"
        f"Best regards,\n"
        f"Stixis HR Team\n"
        f"\n"
        f"---\n"
        f"This is an automated email. Do not forward your password to anyone."
    )

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            context = ssl.create_default_context(cafile=certifi.where())
            server.starttls(context=context)
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
        print(f"[smtp] sent forgot-password email to {hr_email}")
        return (True, None)
    except smtplib.SMTPAuthenticationError as e:
        err = "SMTP authentication failed (check app password)"
        print(f"[smtp] AUTH FAILED for {SMTP_USER}: {e}")
        return (False, err)
    except (smtplib.SMTPException, OSError, TimeoutError) as e:
        err = f"{type(e).__name__}: {str(e)[:150]}"
        print(f"[smtp] FAILED to send forgot-password to {hr_email}: {err}")
        return (False, err)


def send_hr_welcome_email(
    *,
    hr_email: str,
    hr_name: str,
    login_url: str,
    plaintext_password: str,
) -> tuple[bool, str | None]:
    """
    Notify a newly-created HR user that their account exists, with the
    credentials the admin chose. Same (success, error) contract as the
    candidate email helpers — never raises, returns a short reason on
    failure so the admin UI can surface it.

    NOTE: this email contains a plaintext password. That's a deliberate
    v1 trade-off (admin chose to share via email). v2 should switch to a
    one-time setup link or forced reset on first login.
    """
    if not _SMTP_CONFIGURED:
        err = "SMTP not configured (missing env vars)"
        print(f"[smtp] SKIPPED: {err}.")
        return (False, err)

    msg = EmailMessage()
    msg["Subject"] = "Your English Proficiency Test HR account"
    msg["From"] = formataddr((SMTP_FROM_NAME, SMTP_FROM_EMAIL))
    msg["To"] = hr_email
    msg["Reply-To"] = SMTP_FROM_EMAIL

    msg.set_content(
        f"Dear {hr_name},\n"
        f"\n"
        f"An admin has created an HR account for you on the English\n"
        f"Proficiency Test platform.\n"
        f"\n"
        f"--------------------------------------------\n"
        f"YOUR LOGIN CREDENTIALS\n"
        f"--------------------------------------------\n"
        f"\n"
        f"  Login URL: {login_url}\n"
        f"  Email:     {hr_email}\n"
        f"  Password:  {plaintext_password}\n"
        f"\n"
        f"Sign in via the HR card on the login page. Once logged in you\n"
        f"can invite candidates and review their results.\n"
        f"\n"
        f"For security, please change your password after your first login\n"
        f"by asking the admin (self-service password change is on the\n"
        f"roadmap).\n"
        f"\n"
        f"If you weren't expecting this email, please reply to let us know.\n"
        f"\n"
        f"Best regards,\n"
        f"Stixis HR Team\n"
        f"\n"
        f"---\n"
        f"This is an automated email. Do not forward your password to anyone."
    )

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            context = ssl.create_default_context(cafile=certifi.where())
            server.starttls(context=context)
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
        print(f"[smtp] sent HR welcome email to {hr_email}")
        return (True, None)
    except smtplib.SMTPAuthenticationError as e:
        err = "SMTP authentication failed (check app password)"
        print(f"[smtp] AUTH FAILED for {SMTP_USER}: {e}")
        return (False, err)
    except (smtplib.SMTPException, OSError, TimeoutError) as e:
        err = f"{type(e).__name__}: {str(e)[:150]}"
        print(f"[smtp] FAILED to send HR welcome to {hr_email}: {err}")
        return (False, err)


# ---------------------------------------------------------------------------
# Internal — message construction
# ---------------------------------------------------------------------------

# Friendly display labels for each IANA timezone we accept. Keep in sync with
# schemas.ALLOWED_TIMEZONES and the dropdown options in hr-dashboard.html.
# Anything not in this map falls back to the IANA name itself, which is ugly
# but accurate — that's the right tradeoff (loud failure beats wrong label).
_TZ_LABELS = {
    "Asia/Kolkata": "IST",
    "America/New_York": "ET",
    "America/Chicago": "CT",
    "America/Denver": "MT",
    "America/Los_Angeles": "PT",
    "America/Anchorage": "AKT",
    "Pacific/Honolulu": "HT",
    "UTC": "UTC",
}


def _format_window(valid_from: datetime, valid_until: datetime, tz_name: str) -> str:
    """
    Render the [valid_from, valid_until] window as a human-readable string in
    the given IANA timezone. Example output:
        "May 4, 2026 from 4:57 PM to 5:57 PM (IST)"

    Inputs are NAIVE UTC datetimes (the convention used everywhere else in
    this codebase — see _utcnow() in models.py). We attach UTC tzinfo
    explicitly before astimezone() because Python 3.12+ deprecates implicit
    UTC assumption on naive datetimes.

    If tz_name isn't a valid IANA zone (shouldn't happen — schemas.py has
    an allowlist — but defense in depth), fall back to UTC so the email
    still goes out instead of raising mid-send and losing the message.
    """
    try:
        target_tz = ZoneInfo(tz_name)
        label = _TZ_LABELS.get(tz_name, tz_name)
    except ZoneInfoNotFoundError:
        # Bad zone name. Log loudly, don't crash the send.
        print(f"[smtp] WARN: unknown timezone {tz_name!r}, falling back to UTC.")
        target_tz = ZoneInfo("UTC")
        label = "UTC"

    # Attach UTC, then convert. .replace(tzinfo=...) on a naive dt does NOT
    # convert — it just labels. astimezone() is what shifts the wall clock.
    from_local = valid_from.replace(tzinfo=timezone.utc).astimezone(target_tz)
    until_local = valid_until.replace(tzinfo=timezone.utc).astimezone(target_tz)

    date_str = from_local.strftime("%B %d, %Y")
    from_time = from_local.strftime("%I:%M %p").lstrip("0")
    to_time = until_local.strftime("%I:%M %p").lstrip("0")

    # Same-day vs cross-day window. After timezone conversion the dates can
    # diverge even when valid_from == valid_until in UTC (e.g. a window that
    # spans midnight in IST), so this check has to use the converted values.
    if from_local.date() == until_local.date():
        return f"{date_str} from {from_time} to {to_time} ({label})"
    until_date = until_local.strftime("%B %d, %Y")
    return f"{date_str} {from_time} to {until_date} {to_time} ({label})"


_SECTION_DISPLAY_NAMES = {
    "reading": "Reading Comprehension",
    "writing": "Written Expression",
    "speaking": "Communication",
}


def _format_included_sections(
    include_reading: bool, include_writing: bool, include_speaking: bool
) -> str:
    """
    Render the included sections as a human-readable, oxford-comma-joined
    string for the email body. Examples:
        all 3 → "Reading Comprehension, Written Expression, and Communication"
        2     → "Reading Comprehension and Written Expression"
        1     → "Reading Comprehension"
    """
    parts: list[str] = []
    if include_reading:
        parts.append(_SECTION_DISPLAY_NAMES["reading"])
    if include_writing:
        parts.append(_SECTION_DISPLAY_NAMES["writing"])
    if include_speaking:
        parts.append(_SECTION_DISPLAY_NAMES["speaking"])
    if len(parts) == 0:
        # Defensive — schema validator already rejects this, but if the
        # email path is ever reached with no sections, render something.
        return "no sections selected"
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"
    return f"{parts[0]}, {parts[1]}, and {parts[2]}"


def _build_invitation_message(
    *,
    candidate_email: str,
    candidate_name: str,
    exam_url: str,
    access_code: str,
    valid_from=None,
    valid_until=None,
    hr_name: str | None,
    regenerated: bool = False,
    display_timezone: str | None = None,
    include_reading: bool = True,
    include_writing: bool = True,
    include_speaking: bool = True,
) -> EmailMessage:
    """
    Build a multipart email with both plain-text and HTML parts. Modern email
    clients render HTML; older/CLI clients fall back to plain text. Sending
    both maximises deliverability and accessibility.

    valid_from / valid_until are optional for backward compatibility with the
    regenerate-code path which doesn't (yet) thread the window through. When
    provided, the email shows a "Scheduled for: <window>" line near the top.
    """
    if regenerated:
        subject = "Action required: Your English Proficiency Test access code has been reset"
    else:
        subject = "Action required: Your English Proficiency Test invitation"

    # Format the scheduled window once for use in both the plain text and HTML
    # bodies. Empty string when no window provided (e.g. regenerate-code path).
    if valid_from is not None and valid_until is not None:
        # Convert from naive UTC (how the DB stores it) to the HR's chosen
        # display timezone before formatting. Falls back to "UTC" if the
        # caller didn't pass a timezone — preserves the old behavior for
        # any caller that hasn't been updated yet.
        window_str = _format_window(valid_from, valid_until, display_timezone or "UTC")
    else:
        window_str = ""

    # Pre-render the per-invitation section list and the count, used in the
    # ASSESSMENT DETAILS block of both bodies. Singular vs plural matters
    # for "component" / "components".
    sections_str = _format_included_sections(
        include_reading, include_writing, include_speaking
    )
    sections_count = sum([include_reading, include_writing, include_speaking])

    msg = EmailMessage()
    msg["Subject"] = subject
    # formataddr renders as: "English Proficiency Test <Sinchana.R@stixis.com>"
    msg["From"] = formataddr((SMTP_FROM_NAME, SMTP_FROM_EMAIL))
    msg["To"] = candidate_email
    # Reply-To = the HR who sent the invite, so candidates can ask questions.
    # Falls back to FROM if HR's email isn't passed in.
    msg["Reply-To"] = SMTP_FROM_EMAIL

    msg.set_content(
        _plain_text_body(
            candidate_name=candidate_name,
            exam_url=exam_url,
            access_code=access_code,
            window_str=window_str,
            hr_name=hr_name,
            regenerated=regenerated,
            sections_str=sections_str,
            sections_count=sections_count,
            include_speaking=include_speaking,
        )
    )
    msg.add_alternative(
        _html_body(
            candidate_name=candidate_name,
            exam_url=exam_url,
            access_code=access_code,
            window_str=window_str,
            hr_name=hr_name,
            regenerated=regenerated,
            sections_str=sections_str,
            sections_count=sections_count,
            include_speaking=include_speaking,
        ),
        subtype="html",
    )
    return msg


def _plain_text_body(
    *,
    candidate_name: str,
    exam_url: str,
    access_code: str,
    window_str: str,
    hr_name: str | None,
    regenerated: bool,
    sections_str: str,
    sections_count: int,
    include_speaking: bool,
) -> str:
    """Plain-text fallback. Kept short and scannable on any email client."""
    if regenerated:
        intro = (
            "Your access code has been reset. Kindly use the new code below "
            "to log in and complete your English Proficiency Assessment."
        )
    else:
        intro = (
            "You have been invited to complete an English Proficiency "
            "Assessment as part of the recruitment process."
        )

    # Insert the scheduled window into the IMPORTANT section if provided.
    schedule_line = (
        f"  - This test is scheduled for: {window_str}\n"
        if window_str else ""
    )

    # The signature uses HR's name if known, otherwise just "Stixis HR Team"
    signature = (
        f"Best regards,\n"
        f"{hr_name}\n"
        f"Stixis HR Team"
    ) if hr_name else "Best regards,\nStixis HR Team"

    return (
        f"Dear {candidate_name},\n"
        f"\n"
        f"{intro}\n"
        f"\n"
        f"--------------------------------------------\n"
        f"INSTRUCTIONS TO BEGIN THE ASSESSMENT\n"
        f"--------------------------------------------\n"
        f"\n"
        f"  1. Click the link below to open the assessment:\n"
        f"     {exam_url}\n"
        f"\n"
        f"  2. Enter the following 6-digit access code when prompted:\n"
        f"     {access_code}\n"
        f"\n"
        f"  3. Review the instructions provided, then commence the assessment.\n"
        f"\n"
        f"--------------------------------------------\n"
        f"ASSESSMENT DETAILS\n"
        f"--------------------------------------------\n"
        f"\n"
        f"  - The assessment is structured across "
        f"{'one component' if sections_count == 1 else f'{sections_count} components'}:\n"
        f"    {sections_str}\n"
        + (
            f"  - A quiet environment with a working microphone is required\n"
            if include_speaking else ""
        )
        + f"  - A laptop or desktop computer is required (mobile is not supported)\n"
        f"  - The assessment cannot be paused once it has commenced\n"
        f"\n"
        f"--------------------------------------------\n"
        f"IMPORTANT\n"
        f"--------------------------------------------\n"
        f"\n"
        f"{schedule_line}"
        f"  - The URL is active only during the scheduled window above\n"
        f"  - The assessment may be attempted only once\n"
        f"  - Three incorrect access code entries will lock the assessment\n"
        f"\n"
        f"Should you encounter any technical issues or require assistance, "
        f"please reply to this email and our team will respond promptly.\n"
        f"\n"
        f"{signature}\n"
        f"\n"
        f"---\n"
        f"This is an automated email. Please do not forward the access code."
    )


def _html_body(
    *,
    candidate_name: str,
    exam_url: str,
    access_code: str,
    window_str: str,
    hr_name: str | None,
    regenerated: bool,
    sections_str: str,
    sections_count: int,
    include_speaking: bool,
) -> str:
    """
    HTML version — uses inline styles only (most email clients strip <style>
    tags). Conservative styling: works in Gmail web, Outlook desktop, Apple
    Mail, and the Outlook mobile app without surprises.

    Structure (top to bottom):
      1. Header with title
      2. Greeting + intro paragraph
      3. CTA button — primary action ("Start Test")
      4. Access code box — secondary info needed at step 2
      5. "How to start" — three numbered steps
      6. "What to expect" — bullet list of test details
      7. "Important" — expiry, single-use, lockout warning
      8. Reply prompt + signature
      9. Footer disclaimer
    """
    if regenerated:
        intro = (
            "Your access code has been reset. Kindly use the new code below "
            "to log in and complete your English Proficiency Assessment."
        )
        cta_label = "Resume Assessment"
    else:
        intro = (
            "You have been invited to complete an English Proficiency "
            "Assessment as part of the recruitment process."
        )
        cta_label = "Begin Assessment"

    # Schedule line — appended to the IMPORTANT section if a window is provided.
    schedule_html = (
        f'<li style="margin:0 0 6px 0;">This test is scheduled for: '
        f'<strong>{window_str}</strong></li>'
        if window_str else ""
    )

    # Signature — HR's name if known, otherwise just team name
    if hr_name:
        signature_html = (
            f'<p style="margin:0 0 4px 0;font-size:14px;color:#374151;line-height:1.5;">Best regards,</p>'
            f'<p style="margin:0;font-size:14px;color:#111827;font-weight:600;line-height:1.5;">{hr_name}</p>'
            f'<p style="margin:0;font-size:13px;color:#6b7280;line-height:1.5;">Stixis HR Team</p>'
        )
    else:
        signature_html = (
            '<p style="margin:0 0 4px 0;font-size:14px;color:#374151;line-height:1.5;">Best regards,</p>'
            '<p style="margin:0;font-size:14px;color:#111827;font-weight:600;line-height:1.5;">Stixis HR Team</p>'
        )

    return f"""\
<!DOCTYPE html>
<html>
  <body style="margin:0;padding:24px 16px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#111827;background:#f3f4f6;line-height:1.6;">
    <div style="max-width:600px;margin:0 auto;background:#ffffff;border-radius:8px;overflow:hidden;border:1px solid #e5e7eb;">

      <!-- Header -->
      <div style="background:#1e3a8a;padding:24px 32px;color:#ffffff;">
        <h1 style="margin:0;font-size:20px;font-weight:600;letter-spacing:-0.2px;">English Proficiency Test</h1>
        <p style="margin:4px 0 0 0;font-size:13px;color:#bfdbfe;">Stixis Recruitment Assessment</p>
      </div>

      <!-- Body -->
      <div style="padding:32px;">

        <!-- Greeting + Intro -->
        <p style="margin:0 0 16px 0;font-size:16px;color:#111827;">Dear {candidate_name},</p>
        <p style="margin:0 0 28px 0;font-size:15px;color:#374151;">{intro}</p>

        <!-- CTA Button -->
        <div style="margin:0 0 28px 0;text-align:center;">
          <a href="{exam_url}"
             style="display:inline-block;background:#1e3a8a;color:#ffffff;text-decoration:none;padding:14px 36px;border-radius:6px;font-size:16px;font-weight:600;letter-spacing:0.2px;">
            {cta_label} &rarr;
          </a>
        </div>

        <!-- Fallback link below CTA, in case button is broken or stripped -->
        <p style="margin:0 0 28px 0;font-size:12px;color:#6b7280;text-align:center;">
          Button not working? Copy and paste this link:<br>
          <a href="{exam_url}" style="color:#1e3a8a;word-break:break-all;">{exam_url}</a>
        </p>

        <!-- Access Code Box -->
        <div style="margin:0 0 28px 0;background:#f9fafb;border:1px solid #e5e7eb;border-radius:6px;padding:18px 20px;text-align:center;">
          <p style="margin:0 0 6px 0;font-size:12px;color:#6b7280;text-transform:uppercase;letter-spacing:1px;font-weight:600;">Your Access Code</p>
          <p style="margin:0;font-family:'SF Mono',Menlo,Consolas,Courier,monospace;font-size:28px;font-weight:700;letter-spacing:6px;color:#111827;">
            {access_code}
          </p>
          <p style="margin:8px 0 0 0;font-size:12px;color:#6b7280;">Enter this 6-digit code on the page that loads after clicking the button</p>
        </div>

        <!-- Instructions to begin -->
        <h2 style="margin:32px 0 12px 0;font-size:14px;font-weight:700;color:#111827;text-transform:uppercase;letter-spacing:0.5px;">Instructions to begin the assessment</h2>
        <ol style="margin:0 0 28px 20px;padding:0;font-size:14px;color:#374151;">
          <li style="margin:0 0 6px 0;">Click the <strong>{cta_label}</strong> button above</li>
          <li style="margin:0 0 6px 0;">Enter the 6-digit access code shown above</li>
          <li style="margin:0;">Review the instructions provided, then commence the assessment</li>
        </ol>

        <!-- Assessment details -->
        <h2 style="margin:0 0 12px 0;font-size:14px;font-weight:700;color:#111827;text-transform:uppercase;letter-spacing:0.5px;">Assessment details</h2>
        <ul style="margin:0 0 28px 20px;padding:0;font-size:14px;color:#374151;list-style:disc;">
          <li style="margin:0 0 6px 0;">The assessment is structured across {'one component' if sections_count == 1 else str(sections_count) + ' components'}: <strong>{sections_str}</strong></li>
          {'<li style="margin:0 0 6px 0;">A quiet environment with a working microphone is required</li>' if include_speaking else ''}
          <li style="margin:0 0 6px 0;">A laptop or desktop computer is required (mobile is not supported)</li>
          <li style="margin:0;">The assessment cannot be paused once it has commenced</li>
        </ul>

        <!-- Important -->
        <div style="margin:0 0 28px 0;background:#fef3c7;border-left:3px solid #f59e0b;border-radius:4px;padding:14px 18px;">
          <p style="margin:0 0 6px 0;font-size:13px;font-weight:700;color:#92400e;text-transform:uppercase;letter-spacing:0.5px;">Important</p>
          <ul style="margin:0 0 0 16px;padding:0;font-size:13px;color:#78350f;list-style:disc;">
            {schedule_html}
            <li style="margin:0 0 4px 0;">The URL is active <strong>only during the scheduled window above</strong></li>
            <li style="margin:0 0 4px 0;">The assessment may be attempted <strong>only once</strong></li>
            <li style="margin:0;">Three incorrect access code entries will <strong>lock the assessment</strong></li>
          </ul>
        </div>

        <!-- Help / Reply prompt -->
        <p style="margin:0 0 24px 0;font-size:14px;color:#374151;">
          Should you encounter any technical issues or require assistance, please reply to this email and our team will respond promptly.
        </p>

        <!-- Signature -->
        <div style="margin:0 0 0 0;">
          {signature_html}
        </div>

      </div>

      <!-- Footer -->
      <div style="background:#f9fafb;border-top:1px solid #e5e7eb;padding:18px 32px;">
        <p style="margin:0;font-size:11px;color:#9ca3af;line-height:1.5;text-align:center;">
          This is an automated email from the Stixis Recruitment Assessment platform.<br>
          Please do not forward the access code to anyone else.
        </p>
      </div>

    </div>
  </body>
</html>
"""