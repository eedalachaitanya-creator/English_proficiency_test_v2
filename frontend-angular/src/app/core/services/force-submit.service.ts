import { Injectable, inject } from '@angular/core';
import { Router } from '@angular/router';
import { firstValueFrom } from 'rxjs';

import { ApiService } from './api.service';
import { StoreService } from './store.service';
import { VisibilityTrackerService } from './visibility-tracker.service';

interface SubmitResponse {
  ref_id: string;
  status: string;
}

/**
 * The "force-submit on tab-switch termination" logic is needed by the
 * reading, writing, and speaking components. Centralising it here prevents
 * drift across three pages — same as teammate's force-submit.js does
 * for the legacy frontend.
 *
 * Behavior:
 *   Components subscribe to VisibilityTrackerService.onTerminate() and
 *   call this.terminateAndSubmit() inside the handler. Method:
 *     1. Renders a fixed "Test Ended" overlay (blocks the whole page)
 *     2. POSTs whatever data is in StoreService to /api/submit
 *     3. Clears all candidate-flow sessionStorage keys
 *     4. Navigates to /submitted
 *
 * Fire-and-forget — once called, the candidate cannot recover.
 */
@Injectable({ providedIn: 'root' })
export class ForceSubmitService {
  private api = inject(ApiService);
  private router = inject(Router);
  private store = inject(StoreService);
  private tracker = inject(VisibilityTrackerService);

  private inFlight = false;

  async terminateAndSubmit(): Promise<void> {
    if (this.inFlight) return;
    this.inFlight = true;

    this.showOverlay();

    const fd = new FormData();
    fd.append('answers', JSON.stringify(this.store.getReadingAnswers()));
    fd.append('topic_ids', JSON.stringify([]));
    fd.append('essay_text', this.store.getWritingEssay());

    const stats = this.tracker.getStats();
    fd.append('tab_switches_count', String(stats.count));
    fd.append('tab_switches_total_seconds', String(stats.totalSeconds));
    fd.append('submission_reason', 'tab_switch_termination');

    try {
      const res = await firstValueFrom(
        this.api.post<SubmitResponse>('/api/submit', fd)
      );

      if (res?.ref_id) {
        this.store.setRefId(res.ref_id);
      }

      this.store.clearTestSession();
      this.tracker.reset();

      this.router.navigate(['/submitted']);
    } catch (err) {
      console.error('[force-submit] submission failed:', err);
      this.setOverlayMessage(
        'Submission could not be completed. Please contact your HR manager.'
      );
      // Deliberately do NOT navigate — leave the candidate on the overlay
      // so they don't think they submitted successfully. The error is final.
    }
  }

  private showOverlay(): void {
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
}