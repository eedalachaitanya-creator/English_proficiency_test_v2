/**
 * Type definitions for HR-side flows.
 *
 * Match the JSON shapes returned by:
 *   /api/hr/login, /api/hr/me, /api/hr/invite,
 *   /api/hr/results, /api/hr/results/:invitation_id
 *
 * Source of truth: backend/schemas.py (HRLoginRequest, HRLoginResponse,
 * InviteCreateRequest, InviteCreateResponse, ScoreRow, ScoreDetail,
 * AudioRecordingPublic).
 */

// ===========================================================================
//  Auth
// ===========================================================================

export interface HRLoginRequest {
  email: string;
  password: string;
}

export interface HRUser {
  id: number;
  name: string;
  email: string;
}

// ===========================================================================
//  Invitations
// ===========================================================================

export interface InviteCreateRequest {
  candidate_name: string;
  candidate_email: string;
  difficulty: 'intermediate' | 'expert';
  // ISO-8601 UTC strings — see hr-dashboard.ts submitInvite() for the
  // wall-clock-to-UTC conversion (interpreted in the selected timezone,
  // NOT browser local). Both required.
  valid_from: string;
  valid_until: string;
  // IANA timezone name (e.g. "Asia/Kolkata", "America/New_York") that HR
  // selected in the invite modal. Backend uses this to render the
  // scheduled window in the candidate's invitation email. Allowed values
  // are gated server-side; see schemas.ALLOWED_TIMEZONES in the backend.
  timezone: string;
}

export interface InviteCreateResponse {
  invitation_id: number;
  token: string;
  candidate_name: string;
  candidate_email: string;
  difficulty: 'intermediate' | 'expert';
  exam_url: string;
  access_code: string;          // 6-digit passcode the candidate enters
  expires_at: string;
  // Email delivery state — drives the dashboard's UX after Generate Link.
  //   "sent"    → frontend shows success toast, closes modal
  //   "failed"  → frontend keeps modal open, shows error + URL/code as fallback
  //   "pending" → SMTP not configured (treat like "failed" in UI)
  email_status: 'sent' | 'failed' | 'pending';
  email_error: string | null;   // short reason if email_status === "failed"
}

/**
 * Returned by GET /api/hr/invitation/:id/details — drives the
 * "INVITATION DETAILS" card on the candidate-detail page for pending
 * (not-yet-submitted) candidates. HR uses this view to recover the URL
 * after the post-invite popup is dismissed, and to resend the email.
 */
export interface InvitationDetails {
  invitation_id: number;
  candidate_name: string;
  candidate_email: string;
  difficulty: string;

  created_at: string;
  valid_from: string;          // window start (ISO UTC) — when URL becomes active
  expires_at: string;          // window end (ISO UTC) — when URL stops working
  started_at: string | null;
  submitted_at: string | null;

  exam_url: string;
  access_code: string;

  email_status: 'sent' | 'failed' | 'pending';
  email_error: string | null;

  code_locked: boolean;
  failed_code_attempts: number;
}

/**
 * Returned by POST /api/hr/invite/:id/resend-email. Just the email outcome
 * — the candidate-detail page uses this to update the badge + show a toast.
 */
export interface ResendEmailResponse {
  email_status: 'sent' | 'failed' | 'pending';
  email_error: string | null;
}

// ===========================================================================
//  Results — table rows + detail panel
// ===========================================================================

/** One row per invitation in the dashboard table. */
export interface ResultRow {
  invitation_id: number;
  candidate_name: string;
  candidate_email: string;
  difficulty: 'intermediate' | 'expert';
  created_at: string;
  submitted_at: string | null;
  reading_score: number | null;
  writing_score: number | null;
  speaking_score: number | null;
  total_score: number | null;
  rating: 'recommended' | 'borderline' | 'not_recommended' | null;
}

/** A single audio recording — info HR needs to play it back. */
export interface AudioRecordingPublic {
  id: number;
  question_index: number;
  topic_prompt: string;
  duration_seconds: number | null;
  transcript: string | null;
}

/**
 * Full breakdown for one candidate. Returned by /api/hr/results/:id.
 *
 * Shape matches backend/schemas.py:ScoreDetail. Note: this is NOT a strict
 * extension of ResultRow — backend's ScoreDetail doesn't include created_at
 * (only submitted_at), and `rating` is a free-form string rather than the
 * union used in ResultRow. We mirror that here.
 */
export interface ResultDetail {
  invitation_id: number;
  candidate_name: string;
  candidate_email: string;
  difficulty: string;
  submitted_at: string | null;

  reading_score: number | null;
  reading_correct: number | null;
  reading_total: number | null;

  writing_topic_text: string | null;
  essay_text: string | null;
  essay_word_count: number | null;
  writing_breakdown: Record<string, number | null> | null;
  writing_score: number | null;

  speaking_breakdown: Record<string, number | null> | null;
  speaking_score: number | null;

  total_score: number | null;
  rating: string | null;
  ai_feedback: string | null;

  // Tab-switching telemetry. count = number of times the candidate switched
  // away (after the 2-second threshold); total_seconds = cumulative time away.
  // Old rows submitted before the columns existed default to 0 server-side.
  tab_switches_count: number | null;
  tab_switches_total_seconds: number | null;

  // Why the test ended. Null for old rows submitted before this column existed.
  // One of: candidate_finished | reading_timer_expired | writing_timer_expired
  // | speaking_timer_expired | tab_switch_termination | window_expired.
  submission_reason: string | null;

  audio_recordings: AudioRecordingPublic[];
}