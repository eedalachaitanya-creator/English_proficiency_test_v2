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
}

export interface InviteCreateResponse {
  invitation_id: number;
  candidate_email: string;
  candidate_name: string;
  difficulty: 'intermediate' | 'expert';
  exam_url: string;
  access_code: string;          // NEW — 6-digit passcode the candidate enters
  expires_at: string;
  email_sent?: boolean;
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

  // Why the test ended. Null for old rows submitted before this column existed.
  // One of: candidate_finished | reading_timer_expired | writing_timer_expired
  // | speaking_timer_expired | tab_switch_termination.
  submission_reason: string | null;

  audio_recordings: AudioRecordingPublic[];
}