/**
 * Type definitions for the candidate-side test flow.
 *
 * These match the JSON shapes returned by the FastAPI backend's
 * /api/test-content endpoint and accepted by /api/submit.
 *
 * Source of truth: backend/schemas.py (TestContentResponse,
 * QuestionPublic, PassagePublic, SpeakingTopicPublic, WritingTopicPublic).
 *
 * If the backend schema changes, update these types too — the TypeScript
 * compiler will then flag every page that needs an update.
 */

/** A single multiple-choice question — answer key is NOT included on the wire. */
export interface QuestionPublic {
  id: number;
  question_type: 'reading_comp' | 'grammar' | 'vocabulary' | 'fill_blank';
  difficulty: 'intermediate' | 'expert';
  stem: string;
  options: string[]; // always exactly 4 options
}

/** The reading passage assigned to this candidate (one per test). */
export interface PassagePublic {
  id: number;
  title: string;
  body: string; // multi-paragraph; split by double newlines on the client
  topic: string | null;
  word_count: number | null;
}

/** A single speaking prompt. The candidate gets a random subset (default: 3). */
export interface SpeakingTopicPublic {
  id: number;
  prompt_text: string;
  category: string | null;
}

/** The writing prompt assigned to this candidate (one per test). */
export interface WritingTopicPublic {
  id: number;
  prompt_text: string;
  min_words: number;
  max_words: number;
  category: string | null;
}

/** Full payload returned by GET /api/test-content. */
export interface TestContent {
  candidate_name: string;
  candidate_email: string;
  difficulty: 'intermediate' | 'expert';

  // Section 1 — Reading
  passage: PassagePublic;
  questions: QuestionPublic[]; // typically 15

  // Section 2 — Writing
  writing_topic: WritingTopicPublic;
  duration_writing_seconds: number;

  // Section 3 — Speaking
  speaking_topics: SpeakingTopicPublic[]; // typically 3
  duration_speaking_seconds: number;

  // Timing
  duration_written_seconds: number;
  expires_at: string; // ISO 8601 timestamp
}

/** Server response from POST /api/submit. */
export interface SubmitResponse {
  ref_id: string; // e.g. "EPT-00001-5YICMW"
  message: string;
}

/** Map of question_id → selected option index (0-3). */
export type ReadingAnswers = Record<number, number>;