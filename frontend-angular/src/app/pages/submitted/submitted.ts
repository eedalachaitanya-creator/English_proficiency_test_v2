import { Component, OnInit, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';

import { StoreService } from '../../core/services/store.service';
import { Topnav } from '../../shared/components/topnav/topnav';
import { Footer } from '../../shared/components/footer/footer';

/**
 * Step 10 — Submitted confirmation page.
 *
 * The candidate lands here after:
 *   - speaking.ts → successful POST /api/submit (normal completion)
 *   - speaking.ts → tab-switch termination submit
 *   - reading.ts or writing.ts → ForceSubmitService termination submit
 *
 * Behavior:
 *   - Read refId from sessionStorage (set by whichever path submitted)
 *   - Display thank-you + the reference ID for HR follow-up
 *   - Clear all candidate-flow sessionStorage keys EXCEPT refId
 *     (so a refresh of /submitted still shows the ref ID)
 *   - No way to navigate back into the test — invitation is now expired
 *
 * Per spec: scores are never shown to the candidate. Results go to HR.
 */
@Component({
  selector: 'app-submitted',
  standalone: true,
  imports: [CommonModule, Topnav, Footer],
  templateUrl: './submitted.html',
  styleUrl: './submitted.css',
})
export class Submitted implements OnInit {
  private store = inject(StoreService);

  /** Reference ID set by the submit path. May be null if user reached this
   *  page directly without submitting (defensive — usually unreachable). */
  refId = signal<string | null>(null);

  ngOnInit(): void {
    this.refId.set(this.store.getRefId());

    // Wipe test-flow keys so a refresh doesn't leak stale state into other
    // pages (e.g., /reading would otherwise restore the old timer deadline).
    // refId itself stays in storage so this page survives refresh.
    this.store.clearTestSession();
  }
}