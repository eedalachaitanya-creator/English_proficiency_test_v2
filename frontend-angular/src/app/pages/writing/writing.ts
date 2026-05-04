import { Component, OnInit, OnDestroy, inject, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { Subscription } from 'rxjs';

import { ApiError } from '../../core/services/api.service';
import { TestContentService } from '../../core/services/test-content.service';
import { StoreService } from '../../core/services/store.service';
import { TimerService, TimerHandle } from '../../core/services/timer.service';
import { ModalService } from '../../core/services/modal.service';
import { VisibilityTrackerService } from '../../core/services/visibility-tracker.service';
import { ForceSubmitService } from '../../core/services/force-submit.service';
import { TestContent } from '../../core/models/test.models';
import { Topnav } from '../../shared/components/topnav/topnav';
import { Footer } from '../../shared/components/footer/footer';

/**
 * Section 2 of 3 — Writing. Mirrors frontend/js/writing.js.
 *
 * - Loads /api/test-content (cached after Reading already fetched it).
 * - Renders the assigned writing prompt at top with min/max word range.
 * - Big textarea for the essay with live word counter.
 * - Auto-saves essay to sessionStorage on every keystroke.
 * - 20-minute timer (deadline persisted in sessionStorage).
 * - On timer expiry → navigate to /speaking.
 * - 3-strike tab-switch tracker is ALREADY running from Reading.
 *   We just subscribe to its events; we don't reset or re-start it.
 *
 * No data is POSTed from this page — submit happens at the end of Speaking.
 */
@Component({
  selector: 'app-writing',
  standalone: true,
  imports: [CommonModule, FormsModule, Topnav, Footer],
  templateUrl: './writing.html',
  styleUrl: './writing.css',
})
export class Writing implements OnInit, OnDestroy {
  private testContentSvc = inject(TestContentService);
  private store = inject(StoreService);
  private timer = inject(TimerService);
  private modal = inject(ModalService);
  private tracker = inject(VisibilityTrackerService);
  private forceSubmit = inject(ForceSubmitService);
  private router = inject(Router);

  content = signal<TestContent | null>(null);
  loadError = signal('');
  essay = signal<string>('');
  timerText = signal('--:--');
  timerState = signal<'normal' | 'warning' | 'danger'>('normal');
  // Transient red message under the textarea when the candidate tries to
  // paste/drop. Auto-clears after 3 seconds. See onPaste / onDrop below.
  pasteWarning = signal(false);

  candidateMeta = computed(() => {
    const c = this.content();
    return c ? `${c.candidate_name}  |  ${c.difficulty}` : 'Loading…';
  });

  wordCount = computed(() => {
    const text = this.essay().trim();
    if (!text) return 0;
    return text.split(/\s+/).filter(Boolean).length;
  });

  minWords = computed(() => this.content()?.writing_topic.min_words ?? 200);
  maxWords = computed(() => this.content()?.writing_topic.max_words ?? 300);

  wordCountStatus = computed<'under' | 'over' | 'ok'>(() => {
    const count = this.wordCount();
    if (count < this.minWords()) return 'under';
    if (count > this.maxWords()) return 'over';
    return 'ok';
  });

  private countdown: TimerHandle | null = null;
  /** Window-end auto-submit timer — fires when HR's scheduled window closes. */
  private windowTimerId: ReturnType<typeof setTimeout> | null = null;
  private subs = new Subscription();
  private pasteWarningTimer: ReturnType<typeof setTimeout> | null = null;

  private beforeUnloadHandler = (e: BeforeUnloadEvent) => {
    e.preventDefault();
    e.returnValue = '';
  };

  // Browser back-button block (popstate guard). Re-pushing the current
  // history state on every popstate makes the OS back button a no-op,
  // matching how the legacy writing.js blocks backward navigation.
  private popstateHandler = () => {
    history.pushState(null, '', window.location.href);
  };

  ngOnInit(): void {
    this.testContentSvc.load().subscribe({
      next: (c) => {
        this.content.set(c);
        this.essay.set(this.store.getWritingEssay());
        this.startTimer(c);
        this.scheduleWindowEnd(c.valid_until_iso);
        // Block the browser back button only AFTER content loads cleanly.
        // If load fails, the candidate sees an error screen and needs the
        // back button to escape — so we don't trap them.
        history.pushState(null, '', window.location.href);
        window.addEventListener('popstate', this.popstateHandler);
      },
      error: (err: ApiError) => {
        if (err.status === 401) {
          this.router.navigate(['/login']);
          return;
        }
        if (err.status === 410) {
          this.router.navigate(['/submitted']);
          return;
        }
        this.loadError.set(err.message || 'Could not load test content.');
      },
    });

    this.tracker.start();

    this.subs.add(
      this.tracker.onFirstWarning().subscribe((p) => {
        this.modal.alert(
          `We detected that you switched away from this tab for ${p.durationSeconds} ` +
          `second${p.durationSeconds !== 1 ? 's' : ''}.\n\n` +
          `This incident has been logged and will be visible to your HR reviewer. ` +
          `Please keep this tab focused for the remainder of the test.`,
          { title: 'Tab switch detected' }
        );
      })
    );
    this.subs.add(
      this.tracker.onFinalWarning().subscribe((p) => {
        this.modal.alert(
          `Final warning.\n\n` +
          `You have switched away ${p.count} times. One more switch will ` +
          `automatically end your test and submit whatever data you've ` +
          `completed so far.\n\nPlease keep this tab focused.`,
          { title: '⚠ FINAL WARNING' }
        );
      })
    );
    this.subs.add(
      this.tracker.onTerminate().subscribe(() => {
        if (this.countdown) {
          this.countdown.stop();
          this.countdown = null;
        }
        this.forceSubmit.terminateAndSubmit('tab_switch_termination');
      })
    );

    window.addEventListener('beforeunload', this.beforeUnloadHandler);
  }

  ngOnDestroy(): void {
    if (this.countdown) {
      this.countdown.stop();
      this.countdown = null;
    }
    if (this.windowTimerId !== null) {
      clearTimeout(this.windowTimerId);
      this.windowTimerId = null;
    }
    if (this.pasteWarningTimer) {
      clearTimeout(this.pasteWarningTimer);
      this.pasteWarningTimer = null;
    }
    this.subs.unsubscribe();
    window.removeEventListener('beforeunload', this.beforeUnloadHandler);
    window.removeEventListener('popstate', this.popstateHandler);
  }

  // See reading.ts MAX_TIMEOUT_MS — setTimeout overflows past ~24.8 days.
  private static readonly MAX_TIMEOUT_MS = 2_147_483_000;

  /**
   * Schedule a setTimeout that auto-submits the test when HR's scheduled
   * window closes. See reading.ts:scheduleWindowEnd for the full rationale.
   */
  private scheduleWindowEnd(validUntilIso: string): void {
    this.armWindowTimer(new Date(validUntilIso).getTime());
  }

  private armWindowTimer(deadline: number): void {
    const remaining = deadline - Date.now();
    if (remaining <= 0) {
      this.forceSubmit.terminateAndSubmit('window_expired');
      return;
    }
    const delay = Math.min(remaining, Writing.MAX_TIMEOUT_MS);
    this.windowTimerId = setTimeout(() => {
      this.windowTimerId = null;
      if (Date.now() < deadline) {
        this.armWindowTimer(deadline);
        return;
      }
      if (this.countdown) {
        this.countdown.stop();
        this.countdown = null;
      }
      this.forceSubmit.terminateAndSubmit('window_expired');
    }, delay);
  }

  // ---- Paste/drop block on the essay textarea ----
  // Candidate must type the essay; pasting (Ctrl/Cmd-V, right-click → Paste,
  // long-press paste on mobile) and dropping (drag-and-drop a text file) are
  // both blocked. Each blocked attempt flashes a transient red warning that
  // auto-clears after 3 seconds.
  onPaste(e: Event): void {
    e.preventDefault();
    this.flashPasteWarning();
  }
  onDrop(e: Event): void {
    e.preventDefault();
    this.flashPasteWarning();
  }
  onDragOver(e: Event): void {
    e.preventDefault();
  }
  private flashPasteWarning(): void {
    this.pasteWarning.set(true);
    if (this.pasteWarningTimer) clearTimeout(this.pasteWarningTimer);
    this.pasteWarningTimer = setTimeout(() => {
      this.pasteWarning.set(false);
      this.pasteWarningTimer = null;
    }, 3000);
  }

  private startTimer(c: TestContent): void {
    let deadline = this.store.getWritingDeadline();
    if (deadline === null) {
      deadline = Date.now() + c.duration_writing_seconds * 1000;
      this.store.setWritingDeadline(deadline);
    }

    const initialRemaining = Math.max(0, Math.floor((deadline - Date.now()) / 1000));
    if (initialRemaining === 0) {
      // Writing time was already up before this page loaded — auto-submit.
      this.forceSubmit.terminateAndSubmit('writing_timer_expired');
      return;
    }

    this.countdown = this.timer.startFromDeadline(
      deadline,
      (rem: number) => {
        this.timerText.set('⏱  ' + this.timer.formatTime(rem));
        const cls = this.timer.thresholdClass(rem);
        if (cls === 'danger') {
          this.timerState.set('danger');
        } else if (cls === 'warning') {
          this.timerState.set('warning');
        } else {
          this.timerState.set('normal');
        }
      },
      () => {
        // Writing countdown hit zero — auto-submit (no spillover into speaking).
        this.forceSubmit.terminateAndSubmit('writing_timer_expired');
      }
    );
  }

  onEssayChange(value: string): void {
    this.essay.set(value);
    this.store.setWritingEssay(value);
  }

  // Note: the onBack() handler that used to live here was removed along with
  // its UI button. Backward navigation between test sections is blocked by
  // design — see ngOnInit's popstate guard.

  async onContinue(): Promise<void> {
    const count = this.wordCount();
    const min = this.minWords();
    const max = this.maxWords();

    // HARD floor: backend's routes/submit.py:HARD_FLOOR_WORDS=50 will reject
    // any essay under 50 words at final submission. We block here too so the
    // candidate gets the error at the source instead of seeing "Essay too
    // short" pop up on the Speaking page after they've recorded their audio.
    if (count < 50) {
      await this.modal.alert(
        `Your essay is only ${count} word${count === 1 ? '' : 's'}. ` +
        `The minimum is 50 words. Please write more before continuing.`,
        { title: 'Essay too short' }
      );
      return;
    }

    if (count < min) {
      const ok = await this.modal.confirm(
        `Your essay is ${count} words. The recommended minimum is ${min}. ` +
        `Continue to the Speaking section anyway?`,
        { okText: 'Continue', cancelText: 'Keep Writing', dangerous: true }
      );
      if (!ok) return;
    } else if (count > max) {
      const ok = await this.modal.confirm(
        `Your essay is ${count} words, which is over the recommended ${max}-word maximum. ` +
        `Continue to the Speaking section?`,
        { okText: 'Continue', cancelText: 'Trim Essay' }
      );
      if (!ok) return;
    }

    if (this.countdown) {
      this.countdown.stop();
      this.countdown = null;
    }
    this.router.navigate(['/speaking']);
  }
}