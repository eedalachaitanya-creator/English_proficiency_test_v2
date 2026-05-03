import { Injectable, NgZone, inject, signal } from '@angular/core';
import { Subject, Observable } from 'rxjs';

/**
 * Aggregate stats reported back to the backend on submit.
 * Frontend appends as form fields:
 *   tab_switches_count           = stats.count
 *   tab_switches_total_seconds   = stats.totalSeconds
 * Backend trusts the count for the 3-strike termination check
 * (see backend/routes/submit.py is_terminated).
 */
export interface TabSwitchStats {
  count: number;
  totalSeconds: number;
}

interface WarningPayload {
  durationSeconds: number;
  count: number;
}

/**
 * Visibility tracker — Angular port of frontend/js/visibility-tracker.js.
 *
 * THREE-STRIKE POLICY (mirrors teammate's logic exactly):
 *   - Strike 1: emit 'first' warning event for component to render modal
 *   - Strike 2: emit 'final' warning event ("one more = end of test")
 *   - Strike 3: emit 'terminate' — component force-submits and redirects
 *
 * sessionStorage key 'visibilityStats' is identical to teammate's, so the
 * legacy frontend and our Angular frontend share the same persistence.
 */
@Injectable({ providedIn: 'root' })
export class VisibilityTrackerService {
  private zone = inject(NgZone);

  private readonly MIN_SWITCH_SECONDS = 2;
  private readonly MAX_STRIKES = 3;
  /**
   * If a single tab-switch lasts longer than this, terminate immediately
   * — independent of the strike count. Catches the "switch once, stay
   * away to look up answers" cheat that the 3-strike count alone doesn't
   * stop. Uses setTimeout so termination fires while the candidate is
   * still away (their next return will land on /submitted).
   */
  private readonly MAX_SINGLE_SWITCH_SECONDS = 30;
  private readonly STORAGE_KEY = 'visibilityStats';

  private hiddenSince: number | null = null;
  /** setTimeout id for the 30-second long-away termination; null when not pending. */
  private awayTimeoutId: ReturnType<typeof setTimeout> | null = null;
  private terminated = false;
  private listening = false;
  private boundHandler: (() => void) | null = null;
  private boundPagehide: (() => void) | null = null;

  count = signal(0);

  private firstWarning$ = new Subject<WarningPayload>();
  private finalWarning$ = new Subject<WarningPayload>();
  private terminate$ = new Subject<TabSwitchStats>();

  /**
   * Begin listening for visibility changes. Idempotent — safe to call from
   * each test-page's ngOnInit, even if the previous page already started it.
   */
  start(): void {
    if (this.listening) return;
    const saved = this.loadStats();
    this.count.set(saved.count);

    this.boundHandler = () => this.onVisibilityChange();
    this.boundPagehide = () => this.onPageHide();
    document.addEventListener('visibilitychange', this.boundHandler);
    window.addEventListener('pagehide', this.boundPagehide);
    this.listening = true;
  }

  stop(): void {
    if (!this.listening) return;
    if (this.boundHandler) {
      document.removeEventListener('visibilitychange', this.boundHandler);
      this.boundHandler = null;
    }
    if (this.boundPagehide) {
      window.removeEventListener('pagehide', this.boundPagehide);
      this.boundPagehide = null;
    }
    this.clearAwayTimeout();
    this.listening = false;
  }

  getStats(): TabSwitchStats {
    return this.loadStats();
  }

  reset(): void {
    this.stop();
    this.hiddenSince = null;
    this.clearAwayTimeout();
    this.terminated = false;
    this.count.set(0);
    try {
      sessionStorage.removeItem(this.STORAGE_KEY);
    } catch {
      // sessionStorage disabled — silently ignore
    }
  }

  onFirstWarning(): Observable<WarningPayload> {
    return this.firstWarning$.asObservable();
  }
  onFinalWarning(): Observable<WarningPayload> {
    return this.finalWarning$.asObservable();
  }
  onTerminate(): Observable<TabSwitchStats> {
    return this.terminate$.asObservable();
  }

  private onVisibilityChange(): void {
    if (this.terminated) return;

    if (document.hidden) {
      this.hiddenSince = Date.now();
      // Schedule a long-away termination — fires while the candidate is
      // still on another tab / browser. Cleared if they return in time.
      this.awayTimeoutId = setTimeout(() => {
        this.awayTimeoutId = null;
        if (this.terminated || this.hiddenSince === null) return;
        const elapsedSec = Math.round((Date.now() - this.hiddenSince) / 1000);
        const stats = this.loadStats();
        stats.count += 1;
        stats.totalSeconds += elapsedSec;
        this.saveStats(stats);
        this.count.set(stats.count);
        this.terminated = true;
        this.hiddenSince = null;
        this.zone.run(() => this.terminate$.next(stats));
      }, this.MAX_SINGLE_SWITCH_SECONDS * 1000);
      return;
    }

    // Candidate returned. Cancel the pending long-away termination.
    this.clearAwayTimeout();
    if (this.hiddenSince === null) return;
    const elapsedMs = Date.now() - this.hiddenSince;
    this.hiddenSince = null;
    const elapsedSec = Math.round(elapsedMs / 1000);
    if (elapsedSec < this.MIN_SWITCH_SECONDS) return;

    const stats = this.loadStats();
    stats.count += 1;
    stats.totalSeconds += elapsedSec;
    this.saveStats(stats);
    this.count.set(stats.count);

    if (stats.count >= this.MAX_STRIKES) {
      this.terminated = true;
      this.zone.run(() => this.terminate$.next(stats));
      return;
    }

    if (stats.count === this.MAX_STRIKES - 1) {
      this.zone.run(() =>
        this.finalWarning$.next({ durationSeconds: elapsedSec, count: stats.count })
      );
    } else {
      this.zone.run(() =>
        this.firstWarning$.next({ durationSeconds: elapsedSec, count: stats.count })
      );
    }
  }

  private clearAwayTimeout(): void {
    if (this.awayTimeoutId !== null) {
      clearTimeout(this.awayTimeoutId);
      this.awayTimeoutId = null;
    }
  }

  private onPageHide(): void {
    if (this.hiddenSince === null) return;
    const elapsedSec = Math.round((Date.now() - this.hiddenSince) / 1000);
    if (elapsedSec >= this.MIN_SWITCH_SECONDS) {
      const stats = this.loadStats();
      stats.count += 1;
      stats.totalSeconds += elapsedSec;
      this.saveStats(stats);
    }
    this.hiddenSince = null;
  }

  private loadStats(): TabSwitchStats {
    try {
      const raw = sessionStorage.getItem(this.STORAGE_KEY);
      if (!raw) return { count: 0, totalSeconds: 0 };
      const parsed = JSON.parse(raw);
      return {
        count: Number.isFinite(parsed.count) ? parsed.count : 0,
        totalSeconds: Number.isFinite(parsed.totalSeconds) ? parsed.totalSeconds : 0,
      };
    } catch {
      return { count: 0, totalSeconds: 0 };
    }
  }

  private saveStats(stats: TabSwitchStats): void {
    try {
      sessionStorage.setItem(this.STORAGE_KEY, JSON.stringify(stats));
    } catch {
      // sessionStorage full or disabled — silently ignore
    }
  }
}