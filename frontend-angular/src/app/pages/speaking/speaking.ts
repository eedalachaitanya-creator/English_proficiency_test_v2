import {
  Component, OnInit, OnDestroy, AfterViewInit,
  ElementRef, ViewChild, inject, signal, computed,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router } from '@angular/router';
import { Subscription, firstValueFrom } from 'rxjs';

import { ApiService, ApiError } from '../../core/services/api.service';
import { TestContentService } from '../../core/services/test-content.service';
import { StoreService } from '../../core/services/store.service';
import { TimerService, TimerHandle } from '../../core/services/timer.service';
import { ModalService } from '../../core/services/modal.service';
import { VisibilityTrackerService } from '../../core/services/visibility-tracker.service';
import type { SubmissionReason } from '../../core/services/force-submit.service';
import { TestContent, SpeakingTopicPublic } from '../../core/models/test.models';
import { Topnav } from '../../shared/components/topnav/topnav';
import { Footer } from '../../shared/components/footer/footer';

// Per-question budget: 1 min mandatory prep + 2:18 recording = 3:18 total.
// 3 questions × 3:18 = 9:54, leaving 6 seconds slack against the 10-min
// section safety net for the candidate's between-question clicks.
const PREP_SECONDS = 60;
const RECORD_SECONDS = 138;

type Phase = 'idle' | 'prep' | 'recording';
// Subset of SubmissionReason that the speaking page can produce — reading
// and writing reasons are produced by their own pages.
type SpeakingSubmitReason = Extract<
  SubmissionReason,
  'candidate_finished' | 'tab_switch_termination' | 'speaking_timer_expired'
>;

interface RecordingEntry {
  topic_id: number;
  blob: Blob;
  mime: string;
}

interface SubmitResponse {
  ref_id: string;
  status: string;
}

/**
 * Section 3 of 3 — Speaking. Mirrors frontend/js/speaking.js.
 *
 * IMPORTANT: `recordings` is a SIGNAL so that `canNext` and `canFinish`
 * computed signals re-evaluate when we add a new recording. If we used a
 * plain array property, pushing to it would not trigger Angular change
 * detection because MediaRecorder's onstop callback fires from outside
 * Angular's zone — the template would never re-render to show the
 * NEXT QUESTION or FINISH & SUBMIT button.
 */
@Component({
  selector: 'app-speaking',
  standalone: true,
  imports: [CommonModule, Topnav, Footer],
  templateUrl: './speaking.html',
  styleUrl: './speaking.css',
})
export class Speaking implements OnInit, OnDestroy, AfterViewInit {
  private testContentSvc = inject(TestContentService);
  private store = inject(StoreService);
  timer = inject(TimerService);
  private modal = inject(ModalService);
  private tracker = inject(VisibilityTrackerService);
  private api = inject(ApiService);
  private router = inject(Router);

  @ViewChild('waveformEl', { static: false }) waveformRef?: ElementRef<HTMLDivElement>;
  @ViewChild('playbackEl', { static: false }) playbackRef?: ElementRef<HTMLAudioElement>;

  content = signal<TestContent | null>(null);
  loadError = signal('');
  topics = signal<SpeakingTopicPublic[]>([]);
  currentTopicIdx = signal(0);
  perTopicSeconds = signal(0);

  recStatus = signal('Ready to record');
  recStatusKind = signal<'idle' | 'recording' | 'saved' | 'error'>('idle');
  recording = signal(false);
  /**
   * Per-question state machine: idle → prep (1:00) → recording (2:18) →
   * idle (playback shown). Driven by startPrepPhase / startRecording /
   * onRecorderStopped.
   */
  phase = signal<Phase>('idle');
  hasPlayback = signal(false);
  playbackUrl = signal<string>('');

  // Recordings is a SIGNAL so canFinish/canNext re-evaluate when it changes.
  recordings = signal<RecordingEntry[]>([]);

  // Note: `canStart` was removed alongside the manual START button — recording
  // now auto-starts at the end of each question's prep phase.
  canStop = computed(() => this.recording());
  canFinish = computed(() => {
    const t = this.topics();
    return t.length > 0 && this.recordings().length === t.length;
  });
  canNext = computed(() => {
    const t = this.topics();
    if (t.length === 0) return false;
    const idx = this.currentTopicIdx();
    return this.recordings().length === idx + 1 && idx < t.length - 1;
  });

  submitting = signal(false);

  timerText = signal('--:--');
  timerState = signal<'normal' | 'warning' | 'danger'>('normal');

  candidateMeta = computed(() => {
    const c = this.content();
    return c ? `${c.candidate_name}  |  ${c.difficulty}` : 'Loading…';
  });

  topicHeading = computed(() => {
    const t = this.topics();
    if (t.length === 0) return '';
    const idx = this.currentTopicIdx();
    return `Question ${idx + 1} of ${t.length}`;
  });

  topicPrompt = computed(() => {
    const t = this.topics();
    if (t.length === 0) return '';
    return t[this.currentTopicIdx()]?.prompt_text ?? '';
  });

  waveformHeights = signal<number[]>(new Array(40).fill(0));

  private mediaRecorder: MediaRecorder | null = null;
  private audioStream: MediaStream | null = null;
  private audioContext: AudioContext | null = null;
  private analyser: AnalyserNode | null = null;
  private waveformAnimId: number | null = null;
  private chunks: Blob[] = [];

  private countdown: TimerHandle | null = null;
  private prepTimer: TimerHandle | null = null;
  /** Hidden 10-min section-level safety net. setTimeout id for cancellation. */
  private sectionTimerId: ReturnType<typeof setTimeout> | null = null;
  /** Race guard: only one submit (manual finish, tab-term, section-timer) wins. */
  private isAutoSubmitting = false;
  private subs = new Subscription();

  private beforeUnloadHandler = (e: BeforeUnloadEvent) => {
    e.preventDefault();
    e.returnValue = '';
  };

  // Browser back-button block (popstate guard). Re-pushing the current
  // history state on every popstate makes the OS back button a no-op.
  private popstateHandler = () => {
    history.pushState(null, '', window.location.href);
  };

  ngOnInit(): void {
    this.testContentSvc.load().subscribe({
      next: (c) => {
        this.content.set(c);
        const topics = c.speaking_topics ?? [];
        if (topics.length === 0) {
          this.modal.alert(
            'No speaking topics were assigned. Please contact your HR manager.',
            { title: 'Setup error' }
          );
          return;
        }
        this.topics.set(topics);
        // Recording phase is fixed at RECORD_SECONDS regardless of section
        // budget — the section budget just acts as the safety-net total.
        this.perTopicSeconds.set(RECORD_SECONDS);
        this.timerText.set('⏱  PREP: ' + this.timer.formatTime(PREP_SECONDS));

        // Request mic permission once, up front. Browsers cache the grant
        // for the page session, so subsequent getUserMedia calls during
        // recording resolve silently. This stops the permission prompt
        // from interrupting the end of Q1's prep timer.
        this.requestMicPermissionUpfront().then((granted) => {
          if (!granted) {
            this.recStatus.set(
              '✗ Microphone permission denied. Please enable mic access and reload this page.'
            );
            this.recStatusKind.set('error');
            return;
          }
          this.startPrepPhase();
        });

        // Start the 10-min section-level safety net. Hidden from the
        // candidate (per-question timer is the primary UI). Auto-submits
        // if the candidate idles past the budget.
        this.startSectionSafetyNet(c.duration_speaking_seconds);

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
        this.handleTerminate();
      })
    );

    window.addEventListener('beforeunload', this.beforeUnloadHandler);
  }

  ngAfterViewInit(): void { /* no-op */ }

  ngOnDestroy(): void {
    this.cleanupRecording();
    if (this.sectionTimerId !== null) {
      clearTimeout(this.sectionTimerId);
      this.sectionTimerId = null;
    }
    this.subs.unsubscribe();
    window.removeEventListener('beforeunload', this.beforeUnloadHandler);
    window.removeEventListener('popstate', this.popstateHandler);
    if (this.playbackUrl()) {
      URL.revokeObjectURL(this.playbackUrl());
    }
  }

  // ---- Mic permission, requested once at page load ----
  private async requestMicPermissionUpfront(): Promise<boolean> {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      // Immediately release — we'll re-acquire fresh per recording.
      stream.getTracks().forEach((t) => t.stop());
      return true;
    } catch {
      return false;
    }
  }

  // ---- Section-level safety net (10 min total speaking budget) ----
  // Persisted via store so a back-then-forward bounce doesn't reset it.
  private startSectionSafetyNet(budgetSeconds: number): void {
    let deadline = this.store.getSpeakingSectionDeadline();
    if (deadline === null) {
      deadline = Date.now() + budgetSeconds * 1000;
      this.store.setSpeakingSectionDeadline(deadline);
    }
    const remaining = Math.max(0, Math.floor((deadline - Date.now()) / 1000));
    if (remaining === 0) {
      this.autoSubmitWithRecordings('speaking_timer_expired');
      return;
    }
    this.sectionTimerId = setTimeout(() => {
      this.autoSubmitWithRecordings('speaking_timer_expired');
    }, remaining * 1000);
  }

  // ---- Per-question prep phase ----
  // Question shown → 1:00 prep countdown → at 0:00, recording auto-starts.
  // Start button is hidden in the template; the candidate has no manual
  // "begin recording" action.
  private startPrepPhase(): void {
    this.phase.set('prep');
    this.timerText.set('⏱  PREP: ' + this.timer.formatTime(PREP_SECONDS));
    this.timerState.set('normal');
    this.recStatus.set(
      'Read the question and gather your thoughts. Recording starts when this timer hits zero.'
    );
    this.recStatusKind.set('idle');

    if (this.prepTimer) this.prepTimer.stop();
    this.prepTimer = this.timer.start(
      PREP_SECONDS,
      (rem: number) => {
        this.timerText.set('⏱  PREP: ' + this.timer.formatTime(rem));
        const cls = this.timer.thresholdClass(rem, 15, 5);
        if (cls === 'danger') {
          this.timerState.set('danger');
        } else if (cls === 'warning') {
          this.timerState.set('warning');
        } else {
          this.timerState.set('normal');
        }
      },
      () => {
        this.prepTimer = null;
        this.startRecording();
      }
    );
  }

  // Auto-called at the end of the prep phase. The candidate has no manual
  // "Start recording" action — the START button is hidden in the template.
  private async startRecording(): Promise<void> {
    try {
      this.audioStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      this.recStatus.set('✗ Microphone permission denied. Cannot record.');
      this.recStatusKind.set('error');
      return;
    }

    const Ctor: typeof AudioContext =
      window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    this.audioContext = new Ctor();
    const source = this.audioContext.createMediaStreamSource(this.audioStream);
    this.analyser = this.audioContext.createAnalyser();
    this.analyser.fftSize = 128;
    source.connect(this.analyser);
    this.animateWaveform();

    this.chunks = [];
    this.mediaRecorder = new MediaRecorder(this.audioStream);
    this.mediaRecorder.ondataavailable = (e) => {
      if (e.data.size > 0) this.chunks.push(e.data);
    };
    this.mediaRecorder.onstop = () => this.onRecorderStopped();
    this.mediaRecorder.start();

    this.phase.set('recording');
    this.recording.set(true);
    this.hasPlayback.set(false);
    if (this.playbackUrl()) {
      URL.revokeObjectURL(this.playbackUrl());
      this.playbackUrl.set('');
    }
    this.recStatusKind.set('recording');
    this.recStatus.set(`RECORDING  •  00:00 / ${this.timer.formatTime(RECORD_SECONDS)}`);

    this.countdown = this.timer.start(
      RECORD_SECONDS,
      (rem: number) => {
        const elapsed = RECORD_SECONDS - rem;
        this.timerText.set('⏱  RECORDING: ' + this.timer.formatTime(rem));
        this.recStatus.set(
          `RECORDING  •  ${this.timer.formatTime(elapsed)} / ${this.timer.formatTime(RECORD_SECONDS)}`
        );
        const cls = this.timer.thresholdClass(rem, 30, 10);
        if (cls === 'danger') {
          this.timerState.set('danger');
        } else if (cls === 'warning') {
          this.timerState.set('warning');
        } else {
          this.timerState.set('normal');
        }
      },
      () => this.onStop()
    );
  }

  /**
   * Stop button or timer expiry. Triggers MediaRecorder.stop() — the actual
   * cleanup runs inside onRecorderStopped() once the recorder has flushed
   * its final chunk. We do NOT cleanup synchronously here because the
   * audio stream tracks must stay alive until the last chunk is written,
   * otherwise the blob ends up empty.
   */
  onStop(): void {
    if (this.mediaRecorder && this.mediaRecorder.state !== 'inactive') {
      this.mediaRecorder.stop();
      this.recording.set(false);
      if (this.countdown) {
        this.countdown.stop();
        this.countdown = null;
      }
    } else {
      this.cleanupRecording();
    }
  }

  private cleanupRecording(): void {
    if (this.prepTimer) {
      this.prepTimer.stop();
      this.prepTimer = null;
    }
    if (this.countdown) {
      this.countdown.stop();
      this.countdown = null;
    }
    if (this.audioStream) {
      this.audioStream.getTracks().forEach((t) => t.stop());
      this.audioStream = null;
    }
    if (this.audioContext) {
      this.audioContext.close().catch(() => {});
      this.audioContext = null;
    }
    if (this.waveformAnimId !== null) {
      cancelAnimationFrame(this.waveformAnimId);
      this.waveformAnimId = null;
    }
    this.analyser = null;
    this.recording.set(false);
    this.waveformHeights.set(new Array(40).fill(0));
  }

  /**
   * Called by the MediaRecorder's onstop event after the final chunk has
   * been flushed. This is where we actually save the blob and clean up
   * the audio resources.
   */
  private onRecorderStopped(): void {
    const blob = new Blob(this.chunks, { type: 'audio/webm' });
    const topic = this.topics()[this.currentTopicIdx()];
    if (!topic) {
      this.cleanupRecording();
      this.phase.set('idle');
      return;
    }

    this.recordings.update(arr => [...arr, {
      topic_id: topic.id,
      blob,
      mime: blob.type,
    }]);

    const url = URL.createObjectURL(blob);
    this.playbackUrl.set(url);
    this.hasPlayback.set(true);

    const sizeKb = (blob.size / 1024).toFixed(1);
    this.recStatus.set(
      `Recorded for question ${this.currentTopicIdx() + 1} (${sizeKb} KB)`
    );
    this.recStatusKind.set('saved');

    this.cleanupRecording();
    // Recording is finalized — return to idle so the topic-hint stops
    // saying "Speak now" and reflects "Recording complete." until the
    // candidate clicks Next (which will move to 'prep' for the next Q).
    this.phase.set('idle');
  }

  onNextTopic(): void {
    if (this.recordings().length !== this.currentTopicIdx() + 1) return;
    if (this.currentTopicIdx() >= this.topics().length - 1) return;

    this.currentTopicIdx.update((i) => i + 1);
    this.hasPlayback.set(false);
    if (this.playbackUrl()) {
      URL.revokeObjectURL(this.playbackUrl());
      this.playbackUrl.set('');
    }
    // Kick off the next question's mandatory prep phase. Recording for
    // this question will auto-start at 0:00 of prep — no Start button.
    this.startPrepPhase();
  }

  private animateWaveform(): void {
    if (!this.analyser) return;
    const data = new Uint8Array(this.analyser.frequencyBinCount);
    const BAR_COUNT = 40;
    const draw = () => {
      if (!this.analyser) return;
      this.analyser.getByteFrequencyData(data);
      const step = Math.floor(data.length / BAR_COUNT);
      const heights: number[] = [];
      for (let i = 0; i < BAR_COUNT; i++) {
        heights.push(data[i * step] / 255);
      }
      this.waveformHeights.set(heights);
      this.waveformAnimId = requestAnimationFrame(draw);
    };
    draw();
  }

  async onFinish(): Promise<void> {
    // If the section safety net or tab-switch handler is already mid-submit,
    // bail out quietly — the auto-submit overlay is already on screen.
    if (this.isAutoSubmitting) return;

    const total = this.topics().length;
    const recCount = this.recordings().length;

    if (recCount < total) {
      const ok = await this.modal.confirm(
        `You only recorded ${recCount} of ${total} questions. Submit anyway?`,
        { okText: 'Submit Anyway', cancelText: 'Keep Recording', dangerous: true }
      );
      if (!ok) return;
    }

    const ok = await this.modal.confirm(
      'Once you submit, your test is final. You cannot re-record.',
      { okText: 'Submit Test', cancelText: 'Wait', dangerous: true, title: 'Confirm submission' }
    );
    if (!ok) return;

    // Final guard — modal awaits give the section timer a chance to fire
    // between the confirm dialog opening and the user clicking Submit Test.
    if (this.isAutoSubmitting) return;

    // Claim the race guard so the section timer cannot fire mid-POST and
    // attempt a duplicate submission.
    this.isAutoSubmitting = true;
    this.submitting.set(true);

    try {
      const fd = this.buildSubmitFormData('candidate_finished');
      const res = await firstValueFrom(this.api.post<SubmitResponse>('/api/submit', fd));
      if (res?.ref_id) {
        this.store.setRefId(res.ref_id);
      }
      this.tracker.reset();
      this.store.clearTestSession();
      this.router.navigate(['/submitted']);
    } catch (err) {
      // Release both guards on failure so the candidate can retry.
      this.submitting.set(false);
      this.isAutoSubmitting = false;
      const msg = (err as ApiError)?.message ?? 'Unknown error';
      await this.modal.alert(
        `Could not submit your test: ${msg}\n\nPlease check your connection and try again.`,
        { title: 'Submission failed' }
      );
    }
  }

  private buildSubmitFormData(submissionReason: SpeakingSubmitReason): FormData {
    const fd = new FormData();
    const recs = this.recordings();
    fd.append('answers', JSON.stringify(this.store.getReadingAnswers()));
    fd.append('topic_ids', JSON.stringify(recs.map((r) => r.topic_id)));
    fd.append('essay_text', this.store.getWritingEssay());

    const stats = this.tracker.getStats();
    fd.append('tab_switches_count', String(stats.count));
    fd.append('tab_switches_total_seconds', String(stats.totalSeconds));
    fd.append('submission_reason', submissionReason);

    recs.forEach((r, i) => {
      fd.append(`audio_${i}`, r.blob, `q${i}.webm`);
    });

    return fd;
  }

  /**
   * Handle 3-strike tab-switch termination. Captures any in-progress audio
   * blob first, then submits with reason='tab_switch_termination'.
   */
  private handleTerminate(): Promise<void> {
    return this.autoSubmitWithRecordings('tab_switch_termination');
  }

  /**
   * Generic auto-submit path used by both the 3-strike tab termination AND
   * the speaking section's 10-min safety-net timer. Captures any in-progress
   * recording first (so the partial audio is included), then POSTs with the
   * appropriate submission_reason. Race guard prevents the section timer
   * and tab-switch handler from both firing.
   */
  private async autoSubmitWithRecordings(reason: SpeakingSubmitReason): Promise<void> {
    if (this.isAutoSubmitting) return;
    this.isAutoSubmitting = true;

    if (this.recording()) {
      this.onStop();
      // Wait briefly for MediaRecorder.onstop -> handleStopped to flush
      // the final chunk into recordings[] before we build the FormData.
      await new Promise((r) => setTimeout(r, 100));
    }

    this.showTerminationOverlay(reason);

    try {
      const fd = this.buildSubmitFormData(reason);
      const res = await firstValueFrom(this.api.post<SubmitResponse>('/api/submit', fd));
      if (res?.ref_id) {
        this.store.setRefId(res.ref_id);
      }
      this.tracker.reset();
      this.store.clearTestSession();
      this.router.navigate(['/submitted']);
    } catch (err) {
      console.error('[speaking-autosubmit] submission failed:', err);
      this.setOverlayMessage(
        'Submission could not be completed. Please contact your HR manager.'
      );
    }
  }

  private showTerminationOverlay(reason: SpeakingSubmitReason): void {
    if (document.getElementById('terminationOverlay')) return;
    const reasonText = reason === 'tab_switch_termination'
      ? 'Your test has been terminated due to repeated tab switches.'
      : 'Your test has ended because the time limit was reached.';
    const overlay = document.createElement('div');
    overlay.id = 'terminationOverlay';
    overlay.style.cssText = `
      position: fixed; inset: 0; z-index: 9999;
      background: rgba(11, 37, 69, 0.97); color: #fff;
      display: flex; flex-direction: column;
      align-items: center; justify-content: center;
      font-family: var(--font-sans, Arial, sans-serif);
      padding: 24px; text-align: center;
    `;
    overlay.innerHTML = `
      <div style="font-size: 64px; margin-bottom: 16px;">⏹</div>
      <h1 style="font-size: 28px; margin-bottom: 12px;">Test Ended</h1>
      <p style="font-size: 16px; max-width: 480px; line-height: 1.5; margin-bottom: 24px;">
        ${reasonText}
        We are submitting the data you completed so far.
      </p>
      <div id="termSpinnerMsg" style="font-size: 14px; opacity: 0.85;">
        Submitting…
      </div>
    `;
    document.body.appendChild(overlay);
  }

  private setOverlayMessage(text: string): void {
    const el = document.getElementById('termSpinnerMsg');
    if (el) el.textContent = text;
  }

  // Note: the onBack() handler that used to live here was removed along with
  // its UI button. Backward navigation between test sections is blocked by
  // design — see ngOnInit's popstate guard.

  trackByIndex = (i: number) => i;
}