import { Component, OnInit, inject, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router } from '@angular/router';

import { ApiError } from '../../core/services/api.service';
import { TestContentService } from '../../core/services/test-content.service';
import { TestContent } from '../../core/models/test.models';
import { Topnav } from '../../shared/components/topnav/topnav';
import { Footer } from '../../shared/components/footer/footer';

@Component({
  selector: 'app-instructions',
  standalone: true,
  imports: [CommonModule, Topnav, Footer],
  templateUrl: './instructions.html',
  styleUrl: './instructions.css',
})
export class Instructions implements OnInit {
  private testContent = inject(TestContentService);
  private router = inject(Router);

  content = signal<TestContent | null>(null);
  loadError = signal('');
  micStatus = signal<'idle' | 'requesting' | 'ok' | 'failed'>('idle');
  // The candidate must check the acknowledgment before "Begin Test" enables.
  acknowledged = signal(false);

  candidateMeta = computed(() => {
    const c = this.content();
    return c ? `${c.candidate_name}  |  ${c.difficulty}` : 'Loading…';
  });

  welcomeTitle = computed(() => {
    const c = this.content();
    return c ? `Welcome, ${c.candidate_name}` : 'Test Instructions';
  });

  readingMinutes = computed(() => {
    const c = this.content();
    return c ? Math.round(c.duration_written_seconds / 60) : 30;
  });

  writingMinutes = computed(() => {
    const c = this.content();
    return c ? Math.round(c.duration_writing_seconds / 60) : 20;
  });

  speakingMinutes = computed(() => {
    const c = this.content();
    return c ? Math.round(c.duration_speaking_seconds / 60) : 10;
  });

  ngOnInit(): void {
    this.testContent.load().subscribe({
      next: (c) => this.content.set(c),
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
  }

  testMic(): void {
    this.micStatus.set('requesting');
    navigator.mediaDevices
      .getUserMedia({ audio: true })
      .then((stream: MediaStream) => {
        stream.getTracks().forEach((t: MediaStreamTrack) => t.stop());
        this.micStatus.set('ok');
      })
      .catch(() => {
        this.micStatus.set('failed');
      });
  }

  onAcknowledgeChange(checked: boolean): void {
    this.acknowledged.set(checked);
  }

  beginTest(): void {
    // Defensive — the button binding already prevents this, but a determined
    // user could enable the button via DevTools. Keep the gate honest.
    if (!this.acknowledged()) return;
    this.router.navigate(['/reading']);
  }
}