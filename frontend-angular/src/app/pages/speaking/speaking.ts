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
import { TestContent, SpeakingTopicPublic } from '../../core/models/test.models';
import { Topnav } from '../../shared/components/topnav/topnav';
import { Footer } from '../../shared/components/footer/footer';

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
  hasPlayback = signal(false);
  playbackUrl = signal<string>('');

  // Recordings is a SIGNAL so canFinish/canNext re-evaluate when it changes.
  recordings = signal<RecordingEntry[]>([]);

  canStart = computed(() => !this.recording() && !!this.content());
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
  private subs = new Subscription();

  private beforeUnloadHandler = (e: BeforeUnloadEvent) => {
    e.preventDefault();
    e.returnValue = '';
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
        this.perTopicSeconds.set(Math.floor(c.duration_speaking_seconds / topics.length));
        this.timerText.set('⏱  ' + this.timer.formatTime(this.perTopicSeconds()));
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
    this.subs.unsubscribe();
    window.removeEventListener('beforeunload', this.beforeUnloadHandler);
    if (this.playbackUrl()) {
      URL.revokeObjectURL(this.playbackUrl());
    }
  }

  async onStart(): Promise<void> {
    try {
      this.audioStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      this.recStatus.set('✗ Microphone permission denied');
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

    this.recording.set(true);
    this.hasPlayback.set(false);
    if (this.playbackUrl()) {
      URL.revokeObjectURL(this.playbackUrl());
      this.playbackUrl.set('');
    }
    this.recStatusKind.set('recording');
    this.recStatus.set(`RECORDING  •  00:00 / ${this.timer.formatTime(this.perTopicSeconds())}`);

    this.countdown = this.timer.start(
      this.perTopicSeconds(),
      (rem: number) => {
        const elapsed = this.perTopicSeconds() - rem;
        this.timerText.set('⏱  ' + this.timer.formatTime(rem));
        this.recStatus.set(
          `RECORDING  •  ${this.timer.formatTime(elapsed)} / ${this.timer.formatTime(this.perTopicSeconds())}`
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
    this.recStatus.set('Ready to record');
    this.recStatusKind.set('idle');
    this.timerText.set('⏱  ' + this.timer.formatTime(this.perTopicSeconds()));
    this.timerState.set('normal');
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
      this.submitting.set(false);
      const msg = (err as ApiError)?.message ?? 'Unknown error';
      await this.modal.alert(
        `Could not submit your test: ${msg}\n\nPlease check your connection and try again.`,
        { title: 'Submission failed' }
      );
    }
  }

  private buildSubmitFormData(submissionReason: 'candidate_finished' | 'tab_switch_termination'): FormData {
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

  private async handleTerminate(): Promise<void> {
    if (this.recording()) {
      this.onStop();
      await new Promise((r) => setTimeout(r, 100));
    }

    this.showTerminationOverlay();

    try {
      const fd = this.buildSubmitFormData('tab_switch_termination');
      const res = await firstValueFrom(this.api.post<SubmitResponse>('/api/submit', fd));
      if (res?.ref_id) {
        this.store.setRefId(res.ref_id);
      }
      this.tracker.reset();
      this.store.clearTestSession();
      this.router.navigate(['/submitted']);
    } catch (err) {
      console.error('[speaking-terminate] submission failed:', err);
      this.setOverlayMessage(
        'Submission could not be completed. Please contact your HR manager.'
      );
    }
  }

  private showTerminationOverlay(): void {
    if (document.getElementById('terminationOverlay')) return;
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
        Your test has been terminated due to repeated tab switches.
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

  async onBack(): Promise<void> {
    const ok = await this.modal.confirm(
      'Going back to Writing will discard any recordings you made on this section. Continue?',
      { okText: 'Discard & Go Back', cancelText: 'Stay Here', dangerous: true }
    );
    if (!ok) return;
    this.cleanupRecording();
    this.recordings.set([]);
    this.router.navigate(['/writing']);
  }

  trackByIndex = (i: number) => i;
}