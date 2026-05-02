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
  private subs = new Subscription();

  private beforeUnloadHandler = (e: BeforeUnloadEvent) => {
    e.preventDefault();
    e.returnValue = '';
  };

  ngOnInit(): void {
    this.testContentSvc.load().subscribe({
      next: (c) => {
        this.content.set(c);
        this.essay.set(this.store.getWritingEssay());
        this.startTimer(c);
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
        this.forceSubmit.terminateAndSubmit();
      })
    );

    window.addEventListener('beforeunload', this.beforeUnloadHandler);
  }

  ngOnDestroy(): void {
    if (this.countdown) {
      this.countdown.stop();
      this.countdown = null;
    }
    this.subs.unsubscribe();
    window.removeEventListener('beforeunload', this.beforeUnloadHandler);
  }

  private startTimer(c: TestContent): void {
    let deadline = this.store.getWritingDeadline();
    if (deadline === null) {
      deadline = Date.now() + c.duration_writing_seconds * 1000;
      this.store.setWritingDeadline(deadline);
    }

    const initialRemaining = Math.max(0, Math.floor((deadline - Date.now()) / 1000));
    if (initialRemaining === 0) {
      this.store.setWritingTimeUp(true);
      this.router.navigate(['/speaking']);
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
        this.store.setWritingTimeUp(true);
        this.router.navigate(['/speaking']);
      }
    );
  }

  onEssayChange(value: string): void {
    this.essay.set(value);
    this.store.setWritingEssay(value);
  }

  async onBack(): Promise<void> {
    const ok = await this.modal.confirm(
      'Going back will not stop the timer. The countdown keeps running.',
      { okText: 'Go Back', cancelText: 'Stay' }
    );
    if (ok) this.router.navigate(['/reading']);
  }

  async onContinue(): Promise<void> {
    const count = this.wordCount();
    const min = this.minWords();
    const max = this.maxWords();

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