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
SMTP_FROM_NAME = os.getenv("SMTP_FROM_NAME", "FluentiQ").strip()

_SMTP_CONFIGURED = bool(SMTP_HOST and SMTP_USER and SMTP_PASSWORD and SMTP_FROM_EMAIL)


def is_configured() -> bool:
    """Cheap check callers can use to decide whether to attempt a send."""
    return _SMTP_CONFIGURED


def _friendly_smtp_error(exc: BaseException) -> str:
    """Translate a low-level SMTP/network exception into a message HR can
    actually act on. The full exception class + message are still printed to
    the server log with the [smtp] prefix for debugging.

    Without this, the catch-all branch surfaced things like
        gaierror: [Errno -3] Temporary failure in name resolution
    directly into the candidate-detail UI — accurate but useless to a non-
    engineer (and immediately raised "is the app broken?" tickets).
    """
    # DNS resolution failure — the box running the backend cannot resolve
    # SMTP_HOST. Almost always a server/network issue (no internet egress,
    # broken /etc/resolv.conf, captive portal), not anything a code change
    # would fix. Tell HR to check with their IT, not the app developer.
    if isinstance(exc, socket.gaierror):
        return (
            "Could not reach the email server (DNS lookup failed). "
            "The server hosting this app may not have internet access — "
            "check with your IT/network admin."
        )
    if isinstance(exc, ConnectionRefusedError):
        return (
            "Email server refused the connection. "
            "Check that SMTP_HOST and SMTP_PORT are correct."
        )
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "Email server did not respond within 15 seconds."
    if isinstance(exc, ssl.SSLError):
        return "Could not establish a secure connection to the email server."
    if isinstance(exc, smtplib.SMTPServerDisconnected):
        return "Email server disconnected unexpectedly. Try again."
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return "The recipient email address was rejected by the server."
    if isinstance(exc, smtplib.SMTPSenderRefused):
        return "The sender address was rejected by the server."
    if isinstance(exc, smtplib.SMTPDataError):
        return "Email server rejected the message contents."
    # Catch-all for SMTPException / other OSErrors. Stays generic on purpose
    # — the server log has the precise type + message for debugging.
    return "Could not send email (network or server error)."


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
    timezone_short_label: str | None = None,
    include_reading: bool = True,
    include_writing: bool = True,
    include_speaking: bool = True,
    teams_join_url: str | None = None,
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

    `timezone_short_label` is the friendly abbreviation ("IST", "PT") looked
    up by the route handler from the supported_timezones table. Optional —
    when None, the email falls back to showing the raw IANA name.

    `teams_join_url` is the Microsoft Teams meeting URL. When provided,
    the email gets two visual treatments: (1) a "Join Teams Meeting" CTA
    block above the existing Begin Assessment button, and (2) the URL is
    referenced in the INSTRUCTIONS list as step 1. When None (e.g. the
    regenerate-code path), the email looks exactly as it did before.
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
        timezone_short_label=timezone_short_label,
        include_reading=include_reading,
        include_writing=include_writing,
        include_speaking=include_speaking,
        teams_join_url=teams_join_url,
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
        err = _friendly_smtp_error(e)
        # Log the raw class + message for debugging — only the friendly
        # version is surfaced via the API to the HR dashboard.
        print(f"[smtp] raw error: {type(e).__name__}: {str(e)[:200]}")
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
    timezone_short_label: str | None = None,
    include_reading: bool = True,
    include_writing: bool = True,
    include_speaking: bool = True,
    teams_join_url: str | None = None,
) -> tuple[bool, str | None]:
    """
    Send an email when HR regenerates a candidate's access code (e.g. after
    they got locked out from too many wrong attempts). Same return contract
    as send_invitation_email: (success, error_message).

    valid_from / valid_until are the candidate's scheduled URL window — both
    optional for backward compatibility but should be passed so the candidate
    sees when their (now-renewed) access code is actually valid.

    `timezone_short_label` is the friendly abbreviation ("IST", "PT") from
    the supported_timezones table. The route handler looks it up.

    `teams_join_url` is the Microsoft Teams meeting URL — same one stored
    on the Invitation row at create time. Regenerate-code does NOT create
    a new Teams meeting (the interview is the same, only the access code
    rotated), so we just include the existing URL in the email so the
    candidate sees it again alongside their new code.
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
        timezone_short_label=timezone_short_label,
        include_reading=include_reading,
        include_writing=include_writing,
        include_speaking=include_speaking,
        teams_join_url=teams_join_url,
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
        err = _friendly_smtp_error(e)
        # Log the raw class + message for debugging — only the friendly
        # version is surfaced via the API to the HR dashboard.
        print(f"[smtp] raw error: {type(e).__name__}: {str(e)[:200]}")
        print(f"[smtp] FAILED to send to {candidate_email}: {err}")
        return (False, err)


def send_hr_interview_confirmation_email(
    *,
    hr_email: str,
    hr_name: str,
    candidate_name: str,
    candidate_email: str,
    valid_from,
    valid_until,
    teams_join_url: str,
    display_timezone: str | None = None,
    timezone_short_label: str | None = None,
) -> tuple[bool, str | None]:
    """
    Send HR a confirmation email when an interview is scheduled. Triggered
    from /api/hr/invite right after the candidate email goes out.

    Why this email exists: even though the calendar event is created on
    HR's Outlook calendar via Graph API (so it's visible in their
    calendar app), Microsoft does NOT email the calendar owner about
    events they themselves create. To get an entry in HR's INBOX (which
    is searchable, forwardable, and visible from any mail client) we
    have to send a real email.

    What this email is NOT: this is not a copy of the candidate's invite
    email. HR doesn't need:
      - The 6-digit access code (only the candidate enters it)
      - The exam URL (HR doesn't take the test)
      - The "begin assessment" CTA
    It just needs the Teams URL, the candidate's name, and the time.

    Same return contract as the other email helpers — (success, error)
    tuple, never raises. Caller (the /invite route) treats this as
    best-effort: invitation has already succeeded by the time this is
    called, so a failure here just means HR has to find the meeting on
    their calendar instead of in their inbox. Logged with [smtp] prefix.

    Args:
        hr_email:            Recipient — the HR who created the invite.
        hr_name:             Display name used in the greeting.
        candidate_name:      Shown in subject + body so HR can ID the interview.
        candidate_email:     Shown in body for HR's reference.
        valid_from:          Naive UTC datetime — meeting start.
        valid_until:         Naive UTC datetime — meeting end.
        teams_join_url:      The join URL produced by Graph's OnlineMeetings
                             call. Embedded as the primary CTA button.
        display_timezone:    IANA name (e.g. "Asia/Kolkata") for time rendering.
        timezone_short_label: Friendly abbreviation ("IST", "PT") shown in
                             parens after the time. Both optional — fall
                             back to UTC if not provided.
    """
    if not _SMTP_CONFIGURED:
        err = "SMTP not configured (missing env vars)"
        print(f"[smtp] SKIPPED HR confirmation: {err}.")
        return (False, err)

    # Render the scheduled window using the same helper the candidate
    # email uses — so HR sees the time formatted identically to what the
    # candidate sees ("May 10, 2026 from 9:00 AM to 10:00 AM (IST)").
    window_str = _format_window(
        valid_from,
        valid_until,
        display_timezone or "UTC",
        short_label=timezone_short_label,
    )

    msg = EmailMessage()
    msg["Subject"] = f"Interview scheduled with {candidate_name}"
    msg["From"] = formataddr((SMTP_FROM_NAME, SMTP_FROM_EMAIL))
    msg["To"] = hr_email
    msg["Reply-To"] = SMTP_FROM_EMAIL

    msg.set_content(
        _hr_confirmation_plain_text_body(
            hr_name=hr_name,
            candidate_name=candidate_name,
            candidate_email=candidate_email,
            window_str=window_str,
            teams_join_url=teams_join_url,
        )
    )
    msg.add_alternative(
        _hr_confirmation_html_body(
            hr_name=hr_name,
            candidate_name=candidate_name,
            candidate_email=candidate_email,
            window_str=window_str,
            teams_join_url=teams_join_url,
        ),
        subtype="html",
    )

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            context = ssl.create_default_context(cafile=certifi.where())
            server.starttls(context=context)
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
        print(f"[smtp] sent HR confirmation to {hr_email} (interview with {candidate_name})")
        return (True, None)
    except smtplib.SMTPAuthenticationError as e:
        err = "SMTP authentication failed (check app password)"
        print(f"[smtp] AUTH FAILED for {SMTP_USER}: {e}")
        return (False, err)
    except (smtplib.SMTPException, OSError, TimeoutError) as e:
        err = _friendly_smtp_error(e)
        # Log the raw class + message for debugging — only the friendly
        # version is surfaced via the API to the HR dashboard.
        print(f"[smtp] raw error: {type(e).__name__}: {str(e)[:200]}")
        print(f"[smtp] FAILED to send HR confirmation to {hr_email}: {err}")
        return (False, err)


def _hr_confirmation_plain_text_body(
    *,
    hr_name: str,
    candidate_name: str,
    candidate_email: str,
    window_str: str,
    teams_join_url: str,
) -> str:
    """
    Plain-text fallback body for the HR confirmation email. Short and
    scannable — HR opens this on mobile too. No access code or exam URL,
    just the join link, candidate context, and time.
    """
    return (
        f"Hi {hr_name},\n"
        f"\n"
        f"You have scheduled an interview through FluentiQ. The Microsoft\n"
        f"Teams meeting has been created and added to your Outlook calendar.\n"
        f"\n"
        f"--------------------------------------------\n"
        f"INTERVIEW DETAILS\n"
        f"--------------------------------------------\n"
        f"\n"
        f"  Candidate: {candidate_name} ({candidate_email})\n"
        f"  Scheduled: {window_str}\n"
        f"\n"
        f"--------------------------------------------\n"
        f"JOIN THE TEAMS MEETING\n"
        f"--------------------------------------------\n"
        f"\n"
        f"  {teams_join_url}\n"
        f"\n"
        f"You can also join from your Outlook calendar — the event has\n"
        f"already been added there at the scheduled time.\n"
        f"\n"
        f"Best regards,\n"
        f"FluentiQ\n"
        f"\n"
        f"---\n"
        f"This is an automated confirmation. The candidate received a\n"
        f"separate email with the test link and access code."
    )


def _hr_confirmation_html_body(
    *,
    hr_name: str,
    candidate_name: str,
    candidate_email: str,
    window_str: str,
    teams_join_url: str,
) -> str:
    """
    HTML body for the HR confirmation email. Same visual language as
    the other automated emails — navy header, white card, orange Teams
    button. Distinct from the candidate email by NOT having a test URL,
    access code, or instructions block.
    """
    return f"""\
<!DOCTYPE html>
<html>
  <body style="margin:0;padding:24px 16px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#111827;background:#f3f4f6;line-height:1.6;">
    <div style="max-width:600px;margin:0 auto;background:#ffffff;border-radius:8px;overflow:hidden;border:1px solid #e5e7eb;">

      <!-- Header band -->
      <div style="background:#1e3a8a;padding:24px 32px;color:#ffffff;">
        <h1 style="margin:0;font-size:20px;font-weight:600;letter-spacing:-0.2px;">Interview Scheduled</h1>
        <p style="margin:4px 0 0 0;font-size:13px;color:#bfdbfe;">FluentiQ &middot; Stixis HR</p>
      </div>

      <!-- Body card -->
      <div style="padding:32px;">

        <p style="margin:0 0 16px 0;font-size:16px;color:#111827;">Hi {hr_name},</p>
        <p style="margin:0 0 28px 0;font-size:15px;color:#374151;">
          You have scheduled an interview through FluentiQ. The Microsoft
          Teams meeting has been created and added to your Outlook calendar.
        </p>

        <!-- Interview details block -->
        <div style="margin:0 0 28px 0;background:#f9fafb;border:1px solid #e5e7eb;border-radius:6px;padding:18px 20px;">
          <p style="margin:0 0 12px 0;font-size:12px;color:#6b7280;text-transform:uppercase;letter-spacing:1px;font-weight:600;">Interview Details</p>
          <table cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;width:100%;">
            <tr>
              <td style="padding:6px 0;font-size:13px;color:#6b7280;width:100px;vertical-align:top;">Candidate</td>
              <td style="padding:6px 0;font-size:14px;color:#111827;font-weight:600;">{candidate_name}</td>
            </tr>
            <tr>
              <td style="padding:6px 0;font-size:13px;color:#6b7280;vertical-align:top;">Email</td>
              <td style="padding:6px 0;font-size:14px;color:#111827;word-break:break-all;">{candidate_email}</td>
            </tr>
            <tr>
              <td style="padding:6px 0;font-size:13px;color:#6b7280;vertical-align:top;">Scheduled</td>
              <td style="padding:6px 0;font-size:14px;color:#111827;font-weight:600;">{window_str}</td>
            </tr>
          </table>
        </div>

        <!-- Teams join CTA -->
        <div style="margin:0 0 16px 0;text-align:center;">
          <a href="{teams_join_url}"
             style="display:inline-block;background:#FF6B35;color:#ffffff;text-decoration:none;padding:14px 36px;border-radius:6px;font-size:16px;font-weight:600;letter-spacing:0.2px;">
            Join Teams Meeting
          </a>
        </div>

        <!-- Fallback link in case the button is stripped -->
        <p style="margin:0 0 28px 0;font-size:12px;color:#6b7280;text-align:center;">
          Button not working? Copy this link:<br>
          <a href="{teams_join_url}" style="color:#1e3a8a;word-break:break-all;">{teams_join_url}</a>
        </p>

        <!-- Calendar reference -->
        <!-- Calendar reference -->
        <div style="margin:0 0 24px 0;background:#eff6ff;border:1px solid #bfdbfe;border-radius:6px;padding:14px 18px;">
          <p style="margin:0;font-size:13px;color:#1e40af;line-height:1.55;">
            <strong>Already on your calendar:</strong> this meeting has been
            added to your Outlook calendar at the scheduled time. You can
            join from there too.
          </p>
        </div>

        <!-- Signature -->
        <p style="margin:0 0 4px 0;font-size:14px;color:#374151;line-height:1.5;">Best regards,</p>
        <p style="margin:0;font-size:14px;color:#111827;font-weight:600;line-height:1.5;">FluentiQ</p>

      </div>

      <!-- Footer disclaimer -->
      <div style="background:#f9fafb;padding:16px 32px;border-top:1px solid #e5e7eb;">
        <p style="margin:0;font-size:11px;color:#9ca3af;text-align:center;line-height:1.5;">
          This is an automated confirmation. The candidate received a separate
          email with the test link and access code.
        </p>
      </div>
    </div>
  </body>
</html>"""


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
    send_user_welcome_email. The email subject and body strongly prompt
    the recipient to change the password immediately after logging in.
    """
    if not _SMTP_CONFIGURED:
        err = "SMTP not configured (missing env vars)"
        print(f"[smtp] SKIPPED: {err}.")
        return (False, err)

    msg = EmailMessage()
    msg["Subject"] = "Your FluentiQ password has been reset"
    msg["From"] = formataddr((SMTP_FROM_NAME, SMTP_FROM_EMAIL))
    msg["To"] = hr_email
    msg["Reply-To"] = SMTP_FROM_EMAIL

    msg.set_content(
        f"Dear {hr_name},\n"
        f"\n"
        f"You (or someone using your email) requested a password reset for\n"
        f"your FluentiQ HR account. Your new temporary password is below.\n"
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
        f"HR Team\n"
        f"\n"
        f"---\n"
        f"This is an automated email. Do not forward your password to anyone."
    )

    # HTML alternative — rendered by Gmail web, Outlook desktop, Apple
    # Mail, etc. Plain-text version above is the fallback for clients
    # that don't render HTML or for users who prefer it. Same content
    # both ways; only the visual treatment differs.
    msg.add_alternative(
        _temp_password_html_body(
            hr_name=hr_name,
            hr_email=hr_email,
            login_url=login_url,
            temp_password=temp_password,
        ),
        subtype="html",
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
        err = _friendly_smtp_error(e)
        # Log the raw class + message for debugging — only the friendly
        # version is surfaced via the API to the HR dashboard.
        print(f"[smtp] raw error: {type(e).__name__}: {str(e)[:200]}")
        print(f"[smtp] FAILED to send forgot-password to {hr_email}: {err}")
        return (False, err)


def _temp_password_html_body(
    *,
    hr_name: str,
    hr_email: str,
    login_url: str,
    temp_password: str,
) -> str:
    """
    HTML body for the forgot-password email. Mirrors the visual
    language of the invitation email's _html_body — same navy header
    band, same white card layout, same CTA button shape — so a user
    who has seen one email instantly recognizes the other as part of
    the same product.

    Email-client-safe: inline styles only, no external stylesheets,
    no JS, fixed max-width 600px. Verified by the existing
    _html_body to render in Gmail web, Outlook desktop, Apple Mail,
    and Outlook mobile.
    """
    return f"""\
<!DOCTYPE html>
<html>
  <body style="margin:0;padding:24px 16px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#111827;background:#f3f4f6;line-height:1.6;">
    <div style="max-width:600px;margin:0 auto;background:#ffffff;border-radius:8px;overflow:hidden;border:1px solid #e5e7eb;">

      <!-- Header band -->
      <div style="background:#1e3a8a;padding:24px 32px;color:#ffffff;">
        <h1 style="margin:0;font-size:20px;font-weight:600;letter-spacing:-0.2px;">Password Reset</h1>
        <p style="margin:4px 0 0 0;font-size:13px;color:#bfdbfe;">FluentiQ &middot; Stixis HR</p>
      </div>

      <!-- Body card -->
      <div style="padding:32px;">

        <p style="margin:0 0 16px 0;font-size:16px;color:#111827;">Dear {hr_name},</p>
        <p style="margin:0 0 28px 0;font-size:15px;color:#374151;">
          You (or someone using your email) requested a password reset for your
          FluentiQ HR account. Use the temporary password below to log in, then
          change it from your account menu right away.
        </p>

        <!-- Credentials block -->
        <div style="margin:0 0 28px 0;background:#f9fafb;border:1px solid #e5e7eb;border-left:4px solid #1e3a8a;border-radius:6px;padding:18px 20px;">
          <p style="margin:0 0 10px 0;font-size:12px;color:#6b7280;text-transform:uppercase;letter-spacing:1px;font-weight:600;">Your temporary credentials</p>
          <table cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;width:100%;">
            <tr>
              <td style="padding:6px 0;font-size:13px;color:#6b7280;width:90px;vertical-align:top;">Email</td>
              <td style="padding:6px 0;font-size:14px;color:#111827;font-weight:600;word-break:break-all;">{hr_email}</td>
            </tr>
            <tr>
              <td style="padding:6px 0;font-size:13px;color:#6b7280;vertical-align:top;">Password</td>
              <td style="padding:6px 0;">
                <span style="display:inline-block;background:#ffffff;border:1px solid #d1d5db;border-radius:4px;padding:6px 10px;font-family:'SFMono-Regular',Consolas,'Liberation Mono',Menlo,monospace;font-size:14px;color:#111827;letter-spacing:0.5px;">{temp_password}</span>
              </td>
            </tr>
          </table>
        </div>

        <!-- CTA button -->
        <div style="margin:0 0 16px 0;text-align:center;">
          <a href="{login_url}"
             style="display:inline-block;background:#1e3a8a;color:#ffffff;text-decoration:none;padding:14px 36px;border-radius:6px;font-size:16px;font-weight:600;letter-spacing:0.2px;">
            Log In &rarr;
          </a>
        </div>

        <!-- Fallback link in case the button is stripped -->
        <p style="margin:0 0 28px 0;font-size:12px;color:#6b7280;text-align:center;">
          Button not working? Copy this link:<br>
          <a href="{login_url}" style="color:#1e3a8a;word-break:break-all;">{login_url}</a>
        </p>

        <!-- After-login instruction -->
        <div style="margin:0 0 24px 0;background:#eff6ff;border:1px solid #bfdbfe;border-radius:6px;padding:14px 18px;">
          <p style="margin:0;font-size:13px;color:#1e40af;line-height:1.55;">
            <strong>Once you log in:</strong> click your account avatar in the top-right
            corner and choose <strong>Change password</strong> to set a new password
            you'll remember.
          </p>
        </div>

        <!-- Security warning -->
        <div style="margin:0 0 24px 0;background:#fef2f2;border:1px solid #fecaca;border-radius:6px;padding:14px 18px;">
          <p style="margin:0 0 6px 0;font-size:13px;color:#991b1b;font-weight:600;">If you did NOT request this reset</p>
          <p style="margin:0;font-size:13px;color:#7f1d1d;line-height:1.55;">
            Your account may have been targeted. Reply to this email so we can
            investigate. Your old password no longer works &mdash; anyone with this
            email can now log in, so treat the contents as sensitive.
          </p>
        </div>

        <!-- Signature -->
        <p style="margin:0 0 4px 0;font-size:14px;color:#374151;line-height:1.5;">Best regards,</p>
        <p style="margin:0;font-size:14px;color:#111827;font-weight:600;line-height:1.5;">HR Team</p>

      </div>

      <!-- Footer disclaimer -->
      <div style="background:#f9fafb;padding:16px 32px;border-top:1px solid #e5e7eb;">
        <p style="margin:0;font-size:11px;color:#9ca3af;text-align:center;line-height:1.5;">
          This is an automated email. Do not forward your password to anyone.
        </p>
      </div>
    </div>
  </body>
</html>"""


def send_user_welcome_email(
    *,
    user_email: str,
    user_name: str,
    role: str,
    login_url: str,
    plaintext_password: str,
) -> tuple[bool, str | None]:
    """
    Notify a newly-created user (HR or admin) that their account exists,
    with the credentials the admin chose. Same (success, error) contract
    as the candidate email helpers — never raises, returns a short reason
    on failure so the admin UI can surface it.

    `role` is "hr" or "admin" — only the subject line, the account
    description ("HR account" vs "admin account"), and the post-login
    capability sentence change. Everything else is shared.

    NOTE: this email contains a plaintext password. That's a deliberate
    v1 trade-off (admin chose to share via email). v2 should switch to a
    one-time setup link or forced reset on first login.
    """
    if not _SMTP_CONFIGURED:
        err = "SMTP not configured (missing env vars)"
        print(f"[smtp] SKIPPED: {err}.")
        return (False, err)

    is_admin = role == "admin"
    role_label = "admin" if is_admin else "HR"
    # Single line that swaps based on the new account's privileges. HR
    # invites candidates; admin manages users (HRs + other admins) and
    # has access to everything HR can do too.
    capabilities = (
        "Once logged in you can manage users (HR + admin accounts) and\n"
        "review every candidate result on the platform."
        if is_admin
        else
        "Sign in via the HR card on the login page. Once logged in you\n"
        "can invite candidates and review their results."
    )

    msg = EmailMessage()
    msg["Subject"] = f"Your FluentiQ {role_label} account"
    msg["From"] = formataddr((SMTP_FROM_NAME, SMTP_FROM_EMAIL))
    msg["To"] = user_email
    msg["Reply-To"] = SMTP_FROM_EMAIL

    msg.set_content(
        f"Dear {user_name},\n"
        f"\n"
        f"An admin has created an {role_label} account for you on FluentiQ.\n"
        f"\n"
        f"--------------------------------------------\n"
        f"YOUR LOGIN CREDENTIALS\n"
        f"--------------------------------------------\n"
        f"\n"
        f"  Login URL: {login_url}\n"
        f"  Email:     {user_email}\n"
        f"  Password:  {plaintext_password}\n"
        f"\n"
        f"{capabilities}\n"
        f"\n"
        f"For security, please change your password after your first login\n"
        f"from the account menu.\n"
        f"\n"
        f"If you weren't expecting this email, please reply to let us know.\n"
        f"\n"
        f"Best regards,\n"
        f"HR Team\n"
        f"\n"
        f"---\n"
        f"This is an automated email. Do not forward your password to anyone."
    )

    # HTML alternative — same content, friendlier visual treatment.
    # Mirrors the temp-password email's styling so the two automated
    # account-state emails look like siblings in the user's inbox.
    msg.add_alternative(
        _user_welcome_html_body(
            user_name=user_name,
            user_email=user_email,
            role=role,
            login_url=login_url,
            plaintext_password=plaintext_password,
        ),
        subtype="html",
    )

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            context = ssl.create_default_context(cafile=certifi.where())
            server.starttls(context=context)
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
        print(f"[smtp] sent {role_label} welcome email to {user_email}")
        return (True, None)
    except smtplib.SMTPAuthenticationError as e:
        err = "SMTP authentication failed (check app password)"
        print(f"[smtp] AUTH FAILED for {SMTP_USER}: {e}")
        return (False, err)
    except (smtplib.SMTPException, OSError, TimeoutError) as e:
        err = _friendly_smtp_error(e)
        # Log the raw class + message for debugging — only the friendly
        # version is surfaced via the API to the HR dashboard.
        print(f"[smtp] raw error: {type(e).__name__}: {str(e)[:200]}")
        print(f"[smtp] FAILED to send {role_label} welcome to {user_email}: {err}")
        return (False, err)


def _user_welcome_html_body(
    *,
    user_name: str,
    user_email: str,
    role: str,
    login_url: str,
    plaintext_password: str,
) -> str:
    """
    HTML body for the welcome email sent when an admin creates a new
    user (HR or admin). Mirrors the visual language of the temp-password
    email — same navy header band, same white card layout, same CTA
    button shape — so the two account-state emails feel like siblings.

    Email-client-safe: inline styles only, no external stylesheets,
    no JS, fixed max-width 600px.
    """
    is_admin = role == "admin"
    role_label = "admin" if is_admin else "HR"
    headline = "Your admin account is ready" if is_admin else "Your HR account is ready"
    capabilities_html = (
        "Once logged in you can manage users (HR + admin accounts) and "
        "review every candidate result on the platform."
        if is_admin
        else
        "Sign in via the HR card on the login page. Once logged in you "
        "can invite candidates and review their results."
    )

    return f"""\
<!DOCTYPE html>
<html>
  <body style="margin:0;padding:24px 16px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#111827;background:#f3f4f6;line-height:1.6;">
    <div style="max-width:600px;margin:0 auto;background:#ffffff;border-radius:8px;overflow:hidden;border:1px solid #e5e7eb;">

      <!-- Header band -->
      <div style="background:#1e3a8a;padding:24px 32px;color:#ffffff;">
        <h1 style="margin:0;font-size:20px;font-weight:600;letter-spacing:-0.2px;">{headline}</h1>
        <p style="margin:4px 0 0 0;font-size:13px;color:#bfdbfe;">FluentiQ &middot; Stixis HR</p>
      </div>

      <!-- Body card -->
      <div style="padding:32px;">

        <p style="margin:0 0 16px 0;font-size:16px;color:#111827;">Dear {user_name},</p>
        <p style="margin:0 0 28px 0;font-size:15px;color:#374151;">
          An admin has created an {role_label} account for you on FluentiQ.
          Use the credentials below to sign in.
        </p>

        <!-- Credentials block -->
        <div style="margin:0 0 28px 0;background:#f9fafb;border:1px solid #e5e7eb;border-left:4px solid #1e3a8a;border-radius:6px;padding:18px 20px;">
          <p style="margin:0 0 10px 0;font-size:12px;color:#6b7280;text-transform:uppercase;letter-spacing:1px;font-weight:600;">Your login credentials</p>
          <table cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;width:100%;">
            <tr>
              <td style="padding:6px 0;font-size:13px;color:#6b7280;width:90px;vertical-align:top;">Email</td>
              <td style="padding:6px 0;font-size:14px;color:#111827;font-weight:600;word-break:break-all;">{user_email}</td>
            </tr>
            <tr>
              <td style="padding:6px 0;font-size:13px;color:#6b7280;vertical-align:top;">Password</td>
              <td style="padding:6px 0;">
                <span style="display:inline-block;background:#ffffff;border:1px solid #d1d5db;border-radius:4px;padding:6px 10px;font-family:'SFMono-Regular',Consolas,'Liberation Mono',Menlo,monospace;font-size:14px;color:#111827;letter-spacing:0.5px;">{plaintext_password}</span>
              </td>
            </tr>
          </table>
        </div>

        <!-- CTA button -->
        <div style="margin:0 0 16px 0;text-align:center;">
          <a href="{login_url}"
             style="display:inline-block;background:#1e3a8a;color:#ffffff;text-decoration:none;padding:14px 36px;border-radius:6px;font-size:16px;font-weight:600;letter-spacing:0.2px;">
            Log In &rarr;
          </a>
        </div>

        <!-- Fallback link in case the button is stripped -->
        <p style="margin:0 0 28px 0;font-size:12px;color:#6b7280;text-align:center;">
          Button not working? Copy this link:<br>
          <a href="{login_url}" style="color:#1e3a8a;word-break:break-all;">{login_url}</a>
        </p>

        <!-- What you can do once logged in -->
        <div style="margin:0 0 24px 0;background:#eff6ff;border:1px solid #bfdbfe;border-radius:6px;padding:14px 18px;">
          <p style="margin:0;font-size:13px;color:#1e40af;line-height:1.55;">
            <strong>Once you log in:</strong> {capabilities_html}
            For security, please change your password from the account menu after your first login.
          </p>
        </div>

        <!-- Unexpected-email notice -->
        <div style="margin:0 0 24px 0;background:#fef2f2;border:1px solid #fecaca;border-radius:6px;padding:14px 18px;">
          <p style="margin:0;font-size:13px;color:#7f1d1d;line-height:1.55;">
            If you weren&rsquo;t expecting this email, please reply so we can investigate.
            Your password is sensitive &mdash; do not forward it to anyone.
          </p>
        </div>

        <!-- Signature -->
        <p style="margin:0 0 4px 0;font-size:14px;color:#374151;line-height:1.5;">Best regards,</p>
        <p style="margin:0;font-size:14px;color:#111827;font-weight:600;line-height:1.5;">HR Team</p>

      </div>

      <!-- Footer disclaimer -->
      <div style="background:#f9fafb;padding:16px 32px;border-top:1px solid #e5e7eb;">
        <p style="margin:0;font-size:11px;color:#9ca3af;text-align:center;line-height:1.5;">
          This is an automated email from FluentiQ. Do not forward your password to anyone.
        </p>
      </div>
    </div>
  </body>
</html>"""


# ---------------------------------------------------------------------------
# Internal — message construction
# ---------------------------------------------------------------------------

# NOTE: The previous _TZ_LABELS dict was removed when timezones moved to
# the supported_timezones DB table (see backend/models.py:SupportedTimezone).
# The route handler now looks up the short label by iana_name and passes
# it into _format_window as the `short_label` parameter. Keeping email_service
# free of DB dependency keeps the module testable in isolation.


def _format_window(
    valid_from: datetime,
    valid_until: datetime,
    tz_name: str,
    short_label: str | None = None,
) -> str:
    """
    Render the [valid_from, valid_until] window as a human-readable string in
    the given IANA timezone. Example output:
        "May 4, 2026 from 4:57 PM to 5:57 PM (IST)"

    Inputs are NAIVE UTC datetimes (the convention used everywhere else in
    this codebase — see _utcnow() in models.py). We attach UTC tzinfo
    explicitly before astimezone() because Python 3.12+ deprecates implicit
    UTC assumption on naive datetimes.

    `short_label` is the friendly abbreviation to show in parentheses (e.g.
    "IST", "PT"). It's looked up by the caller from the supported_timezones
    table. If None, falls back to the IANA name — ugly but accurate, and
    surfaces the missing-label problem in the email so it's obvious at
    review time rather than silently using a wrong label.

    Failure modes (both safe — email still goes out, never raises):
      1. tz_name isn't a valid IANA zone — fall back to UTC display.
      2. The IANA database isn't installed at all (e.g. Windows without the
         `tzdata` pip package) — fall back to formatting the naive UTC
         values directly, with a "(UTC)" label. Loud warning is logged so
         the operator knows to `pip install tzdata`.
    """
    target_tz = None
    label = short_label if short_label else tz_name
    try:
        target_tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        # Either the zone name is wrong, OR (on Windows) the tzdata package
        # is missing. Try plain UTC as a fallback — if THAT also fails, we
        # know the IANA DB itself is unavailable and we have to skip the
        # conversion entirely.
        print(
            f"[smtp] WARN: timezone {tz_name!r} unavailable. "
            f"On Windows install the IANA DB with: pip install tzdata"
        )
        try:
            target_tz = ZoneInfo("UTC")
            label = "UTC"
        except ZoneInfoNotFoundError:
            target_tz = None  # No tzdata at all — render UTC naively below.
            label = "UTC"

    if target_tz is not None:
        # Attach UTC, then convert. .replace(tzinfo=...) on a naive dt does
        # NOT convert — it just labels. astimezone() is what shifts the wall
        # clock to the target zone.
        from_local = valid_from.replace(tzinfo=timezone.utc).astimezone(target_tz)
        until_local = valid_until.replace(tzinfo=timezone.utc).astimezone(target_tz)
    else:
        # Last-ditch fallback: no IANA DB installed. The DB stores naive UTC,
        # so just format the values as-is and call them UTC.
        from_local = valid_from
        until_local = valid_until

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
    timezone_short_label: str | None = None,
    include_reading: bool = True,
    include_writing: bool = True,
    include_speaking: bool = True,
    teams_join_url: str | None = None,
) -> EmailMessage:
    """
    Build a multipart email with both plain-text and HTML parts. Modern email
    clients render HTML; older/CLI clients fall back to plain text. Sending
    both maximises deliverability and accessibility.

    valid_from / valid_until are optional for backward compatibility with the
    regenerate-code path which doesn't (yet) thread the window through. When
    provided, the email shows a "Scheduled for: <window>" line near the top.

    `timezone_short_label` is the friendly abbreviation ("IST", "PT") from
    the supported_timezones table. The route handler looks it up. If not
    provided, the email falls back to showing the raw IANA name.

    `teams_join_url` adds a Teams CTA block above the existing Begin
    Assessment button AND turns the INSTRUCTIONS list into a 4-step
    sequence (with "Join the call" as step 1). When None, the email
    renders exactly as it did before this feature shipped.
    """
    if regenerated:
        subject = "Action required: Your FluentiQ access code has been reset"
    else:
        subject = "Action required: Your FluentiQ invitation"

    # Format the scheduled window once for use in both the plain text and HTML
    # bodies. Empty string when no window provided (e.g. regenerate-code path).
    if valid_from is not None and valid_until is not None:
        # Convert from naive UTC (how the DB stores it) to the HR's chosen
        # display timezone before formatting. Falls back to "UTC" if the
        # caller didn't pass a timezone — preserves the old behavior for
        # any caller that hasn't been updated yet.
        window_str = _format_window(
            valid_from,
            valid_until,
            display_timezone or "UTC",
            short_label=timezone_short_label,
        )
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
    # formataddr renders as: "FluentiQ <Sinchana.R@stixis.com>"
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
            teams_join_url=teams_join_url,
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
            teams_join_url=teams_join_url,
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
    teams_join_url: str | None = None,
) -> str:
    """Plain-text fallback. Kept short and scannable on any email client."""
    if regenerated:
        intro = (
            "Your access code has been reset. Kindly use the new code below "
            "to log in and complete your FluentiQ English Proficiency Assessment."
        )
    else:
        intro = (
            "You have been invited to complete the FluentiQ English Proficiency "
            "Assessment as part of the recruitment process."
        )

    # Insert the scheduled window into the IMPORTANT section if provided.
    schedule_line = (
        f"  - This test is scheduled for: {window_str}\n"
        if window_str else ""
    )

    # The signature uses HR's name if known, otherwise just "HR Team"
    signature = (
        f"Best regards,\n"
        f"{hr_name}\n"
        f"HR Team"
    ) if hr_name else "Best regards,\nHR Team"

    # Build the INSTRUCTIONS block. Two variants:
    #   - With Teams URL (interview flow): 4 steps, "Join Teams call" first,
    #     then test link, access code, begin.
    #   - Without Teams URL (regenerate-code path or pre-feature behaviour):
    #     original 3 steps unchanged.
    if teams_join_url:
        instructions_block = (
            f"  1. Join the Microsoft Teams interview call at the scheduled time:\n"
            f"     {teams_join_url}\n"
            f"     The call will be recorded for review purposes.\n"
            f"\n"
            f"  2. Once on the call, click the link below to open the assessment:\n"
            f"     {exam_url}\n"
            f"\n"
            f"  3. Enter the following 6-digit access code when prompted:\n"
            f"     {access_code}\n"
            f"\n"
            f"  4. Review the instructions provided, then commence the assessment.\n"
        )
    else:
        instructions_block = (
            f"  1. Click the link below to open the assessment:\n"
            f"     {exam_url}\n"
            f"\n"
            f"  2. Enter the following 6-digit access code when prompted:\n"
            f"     {access_code}\n"
            f"\n"
            f"  3. Review the instructions provided, then commence the assessment.\n"
        )

    return (
        f"Dear {candidate_name},\n"
        f"\n"
        f"{intro}\n"
        f"\n"
        f"--------------------------------------------\n"
        f"INSTRUCTIONS TO BEGIN THE ASSESSMENT\n"
        f"--------------------------------------------\n"
        f"\n"
        f"{instructions_block}"
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
    teams_join_url: str | None = None,
) -> str:
    """
    HTML version — uses inline styles only (most email clients strip <style>
    tags). Conservative styling: works in Gmail web, Outlook desktop, Apple
    Mail, and the Outlook mobile app without surprises.

    Structure (top to bottom):
      1. Header with title
      2. Greeting + intro paragraph
      3. Teams meeting CTA block (only when teams_join_url is provided)
      4. CTA button — primary action ("Begin Assessment")
      5. Access code box — secondary info needed at step 2
      6. "How to start" — three numbered steps (or four if Teams URL provided)
      7. "What to expect" — bullet list of test details
      8. "Important" — expiry, single-use, lockout warning
      9. Reply prompt + signature
     10. Footer disclaimer
    """
    if regenerated:
        intro = (
            "Your access code has been reset. Kindly use the new code below "
            "to log in and complete your FluentiQ English Proficiency Assessment."
        )
        cta_label = "Resume Assessment"
    else:
        intro = (
            "You have been invited to complete the FluentiQ English Proficiency "
            "Assessment as part of the recruitment process."
        )
        cta_label = "Begin Assessment"

    # Schedule line — appended to the IMPORTANT section if a window is provided.
    schedule_html = (
        f'<li style="margin:0 0 6px 0;">This test is scheduled for: '
        f'<strong>{window_str}</strong></li>'
        if window_str else ""
    )

    # Teams CTA block — rendered only when teams_join_url is provided.
    # Sits above the Begin Assessment button so candidates see "Join Teams"
    # as the FIRST action, then "Begin Assessment" as the second. Orange
    # button stands out from the navy Begin Assessment button so the two
    # CTAs are visually distinct.
    teams_cta_html = ""
    if teams_join_url:
        teams_cta_html = f"""
        <div style="margin:0 0 28px 0;background:#f9fafb;border:1px solid #e5e7eb;border-left:4px solid #1e3a8a;border-radius:6px;padding:18px 20px;">
          
          <p style="margin:0 0 14px 0;font-size:14px;color:#374151;">
            Join the Microsoft Teams call at the scheduled time. The call will be recorded for review purposes.
          </p>
          <div style="text-align:center;margin:0 0 12px 0;">
            <a href="{teams_join_url}"
               style="display:inline-block;background:#FF6B35;color:#ffffff;text-decoration:none;padding:12px 28px;border-radius:6px;font-size:15px;font-weight:600;">
              Join Teams Meeting
            </a>
          </div>
          <p style="margin:0;font-size:12px;color:#6b7280;text-align:center;">
            Button not working? Copy this link:<br>
            <a href="{teams_join_url}" style="color:#1e3a8a;word-break:break-all;">{teams_join_url}</a>
          </p>
        </div>
        """

    # INSTRUCTIONS ordered list. Two variants depending on Teams URL.
    # With Teams URL: 4 steps with "Join the call" as step 1, the existing
    # button/code/begin steps shifted to 2/3/4. The Teams URL is also
    # surfaced as a clickable link inside the <li> so the candidate has
    # the link in two places (the CTA block above AND inside the steps).
    if teams_join_url:
        instructions_ol = f"""
        <ol style="margin:0 0 28px 20px;padding:0;font-size:14px;color:#374151;">
          <li style="margin:0 0 10px 0;">
            Join the Microsoft Teams interview call at the scheduled time:<br>
            <a href="{teams_join_url}" style="color:#1e3a8a;word-break:break-all;">{teams_join_url}</a><br>
            <span style="font-size:12px;color:#6b7280;">The call will be recorded for review purposes.</span>
          </li>
          <li style="margin:0 0 6px 0;">Once on the call, click the <strong>{cta_label}</strong> button above</li>
          <li style="margin:0 0 6px 0;">Enter the 6-digit access code shown above</li>
          <li style="margin:0;">Review the instructions provided, then commence the assessment</li>
        </ol>
        """
    else:
        instructions_ol = f"""
        <ol style="margin:0 0 28px 20px;padding:0;font-size:14px;color:#374151;">
          <li style="margin:0 0 6px 0;">Click the <strong>{cta_label}</strong> button above</li>
          <li style="margin:0 0 6px 0;">Enter the 6-digit access code shown above</li>
          <li style="margin:0;">Review the instructions provided, then commence the assessment</li>
        </ol>
        """

    # Signature — HR's name if known, otherwise just team name
    if hr_name:
        signature_html = (
            f'<p style="margin:0 0 4px 0;font-size:14px;color:#374151;line-height:1.5;">Best regards,</p>'
            f'<p style="margin:0;font-size:14px;color:#111827;font-weight:600;line-height:1.5;">{hr_name}</p>'
            f'<p style="margin:0;font-size:13px;color:#6b7280;line-height:1.5;">HR Team</p>'
        )
    else:
        signature_html = (
            '<p style="margin:0 0 4px 0;font-size:14px;color:#374151;line-height:1.5;">Best regards,</p>'
            '<p style="margin:0;font-size:14px;color:#111827;font-weight:600;line-height:1.5;">HR Team</p>'
        )

    return f"""\
<!DOCTYPE html>
<html>
  <body style="margin:0;padding:24px 16px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#111827;background:#f3f4f6;line-height:1.6;">
    <div style="max-width:600px;margin:0 auto;background:#ffffff;border-radius:8px;overflow:hidden;border:1px solid #e5e7eb;">

      <!-- Header -->
      <div style="background:#1e3a8a;padding:24px 32px;color:#ffffff;">
        <h1 style="margin:0;font-size:20px;font-weight:600;letter-spacing:-0.2px;">FluentiQ &middot; English Proficiency Assessment</h1>
        <p style="margin:4px 0 0 0;font-size:13px;color:#bfdbfe;">Recruitment Assessment</p>
      </div>

      <!-- Body -->
      <div style="padding:32px;">

        <!-- Greeting + Intro -->
        <p style="margin:0 0 16px 0;font-size:16px;color:#111827;">Dear {candidate_name},</p>
        <p style="margin:0 0 28px 0;font-size:15px;color:#374151;">{intro}</p>

        {teams_cta_html}

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
        {instructions_ol}

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

        <!-- Signature -->
        <div style="margin:0 0 0 0;">
          {signature_html}
        </div>

      </div>

      <!-- Footer -->
      <div style="background:#f9fafb;border-top:1px solid #e5e7eb;padding:18px 32px;">
        <p style="margin:0;font-size:11px;color:#9ca3af;line-height:1.5;text-align:center;">
          This is an automated email from the Recruitment Assessment platform.<br>
          Please do not forward the access code to anyone else.
        </p>
      </div>

    </div>
  </body>
</html>
"""