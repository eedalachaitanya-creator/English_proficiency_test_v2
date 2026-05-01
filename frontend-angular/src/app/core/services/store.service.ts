import { Injectable } from '@angular/core';
import { TestContent, ReadingAnswers } from '../models/test.models';

@Injectable({ providedIn: 'root' })
export class StoreService {
  // ------------------------------------------------------------------
  //  Generic primitives — same shape as the old common.js Store object.
  // ------------------------------------------------------------------

  /** Read and JSON-parse a key. Returns fallback if missing or unparseable. */
  get<T>(key: string, fallback: T | null = null): T | null {
    const raw = sessionStorage.getItem(key);
    if (raw === null) return fallback;
    try {
      return JSON.parse(raw) as T;
    } catch {
      return fallback;
    }
  }

  /** JSON-stringify and store a value. Throws if value contains a circular ref. */
  set(key: string, value: unknown): void {
    sessionStorage.setItem(key, JSON.stringify(value));
  }

  /** Delete a single key. No-op if it doesn't exist. */
  remove(key: string): void {
    sessionStorage.removeItem(key);
  }

  /** Wipe everything in sessionStorage for this tab. Use with care. */
  clear(): void {
    sessionStorage.clear();
  }

  // ------------------------------------------------------------------
  //  Typed accessors for the keys we actually use.
  //
  //  These are convenience wrappers that give callers proper TypeScript
  //  types instead of `unknown` from the generic get<T>(). Components
  //  should prefer these over the raw get/set whenever possible.
  // ------------------------------------------------------------------

  /** The cached /api/test-content payload. Null until the first fetch. */
  getTestContent(): TestContent | null {
    return this.get<TestContent>('testContent');
  }
  setTestContent(value: TestContent): void {
    this.set('testContent', value);
  }
  clearTestContent(): void {
    this.remove('testContent');
  }

  /** Map of question_id → selected option index. Empty object if untouched. */
  getReadingAnswers(): ReadingAnswers {
    return this.get<ReadingAnswers>('readingAnswers', {}) ?? {};
  }
  setReadingAnswers(value: ReadingAnswers): void {
    this.set('readingAnswers', value);
  }

  /** Absolute deadline (ms since epoch) for the reading timer. */
  getReadingDeadline(): number | null {
    const v = this.get<number>('readingDeadline');
    return typeof v === 'number' ? v : null;
  }
  setReadingDeadline(value: number): void {
    this.set('readingDeadline', value);
  }

  /** Reading-time-up flag — set by the timer's onExpire callback. */
  getReadingTimeUp(): boolean {
    return this.get<boolean>('readingTimeUp') === true;
  }
  setReadingTimeUp(value: boolean): void {
    this.set('readingTimeUp', value);
  }

  /** The candidate's essay text. Auto-saved as they type. */
  getWritingEssay(): string {
    return this.get<string>('writingEssay', '') ?? '';
  }
  setWritingEssay(value: string): void {
    this.set('writingEssay', value);
  }

  /** Absolute deadline (ms since epoch) for the writing timer. */
  getWritingDeadline(): number | null {
    const v = this.get<number>('writingDeadline');
    return typeof v === 'number' ? v : null;
  }
  setWritingDeadline(value: number): void {
    this.set('writingDeadline', value);
  }

  /** Writing-time-up flag. */
  getWritingTimeUp(): boolean {
    return this.get<boolean>('writingTimeUp') === true;
  }
  setWritingTimeUp(value: boolean): void {
    this.set('writingTimeUp', value);
  }

  /** Server-issued submission reference ID. Set on submit, read by submitted page. */
  getRefId(): string | null {
    return this.get<string>('refId');
  }
  setRefId(value: string): void {
    this.set('refId', value);
  }

  /**
   * Clear all candidate-flow keys after submission. The submitted page calls
   * this so a refresh doesn't try to fetch test content for an already-
   * submitted invitation. HR pages don't use sessionStorage so no HR keys
   * to wipe.
   */
  clearTestSession(): void {
    this.remove('testContent');
    this.remove('readingAnswers');
    this.remove('readingDeadline');
    this.remove('readingTimeUp');
    this.remove('writingEssay');
    this.remove('writingDeadline');
    this.remove('writingTimeUp');
    // Note: refId stays — the submitted page still needs it to display.
  }
}