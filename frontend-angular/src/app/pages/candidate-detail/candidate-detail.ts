import { Component, OnInit, inject, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { forkJoin, of, catchError } from 'rxjs';
import { environment } from '../../../environments/environment';

import { ApiService, ApiError } from '../../core/services/api.service';
import { AuthService } from '../../core/services/auth.service';
import { ModalService } from '../../core/services/modal.service';
import {
  ResultDetail,
  InvitationDetails,
  ResendEmailResponse,
} from '../../core/models/hr.models';
import { Topnav } from '../../shared/components/topnav/topnav';
import { Footer } from '../../shared/components/footer/footer';

/**
 * Candidate detail page — shows full breakdown for one candidate.
 *
 * Reached by clicking a row on /dashboard. URL: /dashboard/candidate/:id
 *
 * Behavior:
 *   1. On mount: extract :id from the route, call /api/hr/results/:id
 *   2. Render four panels: Reading, Speaking (with audio), Writing (essay + rubric),
 *      AI Feedback summary
 *   3. "← Back to Dashboard" link in the page header
 *   4. Logout via Topnav (same as dashboard)
 *
 * The hr-dashboard component used to render this content inline. We've moved
 * it to its own page so:
 *   - Each candidate has a shareable URL
 *   - The dashboard page is shorter and easier to scan
 *   - HR can open multiple detail pages in browser tabs to compare
 */
@Component({
  selector: 'app-candidate-detail',
  standalone: true,
  imports: [CommonModule, RouterLink, Topnav, Footer],
  templateUrl: './candidate-detail.html',
  styleUrl: './candidate-detail.css',
})
export class CandidateDetail implements OnInit {
  private api = inject(ApiService);
  private auth = inject(AuthService);
  private modal = inject(ModalService);
  private route = inject(ActivatedRoute);
  private router = inject(Router);

  // Reactive state.
  loading = signal(true);
  loadError = signal('');
  detail = signal<ResultDetail | null>(null);
  /**
   * Invitation state — URL, access code, email status, etc. Always populated
   * (we fetch this for both pending and submitted candidates). The template
   * decides whether to render the INVITATION DETAILS card based on whether
   * the candidate has submitted (hidden once submitted).
   */
  invitation = signal<InvitationDetails | null>(null);
  invitationId = signal<number | null>(null);

  // -------- Access code reveal/hide state --------
  // Hidden by default — clicking "Reveal code" shows it. Clicking again
  // hides it. Protects against shoulder-surfing during screen shares.
  codeRevealed = signal(false);

  // -------- Resend email state --------
  resending = signal(false);
  // Toast message shown briefly after resend (success or failure).
  toastMessage = signal('');
  toastVisible = signal(false);
  toastIsError = signal(false);
  /** Tracks the auto-dismiss timer so a second toast cancels the first. */
  private toastTimer: ReturnType<typeof setTimeout> | null = null;

  // -------- Copy URL/code feedback --------
  urlCopied = signal(false);
  codeCopied = signal(false);

  // Convenience for template.
  hrEmail = computed(() => this.auth.currentUser()?.email ?? 'Loading…');

  /**
   * Show INVITATION DETAILS card only when the candidate hasn't submitted.
   * Once they submit, this card disappears and the page goes straight to
   * score breakdowns — same as before today's changes.
   */
  showInvitationCard = computed(() => {
    const inv = this.invitation();
    return inv !== null && inv.submitted_at === null;
  });

  /**
   * Email status badge data — drives the colored pill in the invitation card.
   * Returns null when there's no invitation loaded yet.
   */
  emailStatusBadge = computed((): { label: string; color: string; bg: string } | null => {
    const inv = this.invitation();
    if (!inv) return null;
    if (inv.email_status === 'sent') {
      return { label: 'Email sent', color: '#166534', bg: '#dcfce7' };
    }
    if (inv.email_status === 'failed') {
      return {
        label: `Email failed: ${inv.email_error || 'unknown reason'}`,
        color: '#991b1b',
        bg: '#fef2f2',
      };
    }
    // pending — SMTP wasn't configured at the time. Show neutral.
    return { label: 'Email not sent', color: '#6b7280', bg: '#f3f4f6' };
  });

  ngOnInit(): void {
    // Extract :id from URL. Route is /dashboard/candidate/:id, configured
    // with paramMap, so we read snapshot.paramMap.get('id').
    const idParam = this.route.snapshot.paramMap.get('id');
    const id = idParam ? parseInt(idParam, 10) : NaN;

    if (!Number.isInteger(id) || id <= 0) {
      this.loadError.set('Invalid candidate ID.');
      this.loading.set(false);
      return;
    }

    this.invitationId.set(id);

    // Verify session and load the detail. The route guard runs first, but
    // we still need checkSession() to populate currentUser for the topnav.
    this.auth.checkSession().subscribe({
      next: (user) => {
        if (!user) {
          this.router.navigate(['/login']);
          return;
        }
        this.loadDetail(id);
      },
      error: () => this.router.navigate(['/login']),
    });
  }

  private loadDetail(id: number): void {
    this.loading.set(true);
    this.loadError.set('');

    // We need data from TWO endpoints:
    //   /api/hr/invitation/:id/details  → URL, access code, email status
    //                                     (always — used for pending too)
    //   /api/hr/results/:id              → score breakdown
    //                                     (only meaningful for submitted)
    //
    // forkJoin calls both in parallel and gives us results once BOTH return
    // (or one errors). For the score endpoint we wrap with catchError so a
    // 404 on results doesn't fail the whole load — pending candidates have
    // no score row yet, but we still want to show invitation details.
    const invitation$ = this.api.get<InvitationDetails>(
      `/api/hr/invitation/${id}/details`
    );
    const results$ = this.api.get<ResultDetail>(`/api/hr/results/${id}`).pipe(
      catchError((err: ApiError) => {
        // 404 = no score yet (candidate hasn't submitted) — that's fine,
        // just return null and we'll skip the score panels in the template.
        // Other errors propagate up so we can show a load error.
        if (err.status === 404) return of(null);
        throw err;
      })
    );

    forkJoin({ invitation: invitation$, result: results$ }).subscribe({
      next: ({ invitation, result }) => {
        this.invitation.set(invitation);
        this.detail.set(result);
        this.loading.set(false);
      },
      error: (err: ApiError) => {
        if (err.status === 401) {
          this.router.navigate(['/login']);
          return;
        }
        if (err.status === 404) {
          // Per backend tenancy: 404 either means doesn't exist OR belongs
          // to a different HR. Don't leak which.
          this.loadError.set('Candidate not found.');
        } else {
          this.loadError.set(err.message || 'Could not load candidate detail.');
        }
        this.loading.set(false);
      },
    });
  }

  onLogout(): void {
    this.auth.logout().subscribe(() => this.router.navigate(['/login']));
  }

  // ===== INVITATION DETAILS card actions =====

  /**
   * Toggle access code visibility. Default = hidden (mask shown).
   * Click "Reveal code" to show; click "Hide" to mask again.
   *
   * Why: the access code is sensitive — an attacker with both the URL and
   * the code can take the candidate's test. Default-hide protects against
   * shoulder-surfing during screen shares.
   */
  toggleCodeReveal(): void {
    this.codeRevealed.update(v => !v);
  }

  /**
   * Copy the test URL to clipboard. Shows transient "Copied ✓" feedback
   * on the button for 2s. Catches the rare failure mode where the browser
   * blocks clipboard write — we still show success so the user retries
   * (the alternative is a confusing silent failure).
   */
  copyUrl(): void {
    const inv = this.invitation();
    if (!inv) return;
    navigator.clipboard.writeText(inv.exam_url).finally(() => {
      this.urlCopied.set(true);
      setTimeout(() => this.urlCopied.set(false), 2000);
    });
  }

  /** Same as copyUrl but for the 6-digit access code. */
  copyCode(): void {
    const inv = this.invitation();
    if (!inv) return;
    navigator.clipboard.writeText(inv.access_code).finally(() => {
      this.codeCopied.set(true);
      setTimeout(() => this.codeCopied.set(false), 2000);
    });
  }

  /**
   * Resend the invitation email — same URL + access code, no regeneration.
   * Confirms with HR first via modal so a stray click doesn't spam the
   * candidate. On confirm, calls POST /api/hr/invite/:id/resend-email and
   * shows a toast with the result. Updates the email status badge in place.
   */
  async resendEmail(): Promise<void> {
    const inv = this.invitation();
    if (!inv) return;

    const confirmed = await this.modal.confirm(
      `Resend the invitation email to ${inv.candidate_email}?`,
      {
        title: 'Resend invitation email',
        okText: 'Resend',
        cancelText: 'Cancel',
      }
    );
    if (!confirmed) return;

    this.resending.set(true);
    this.api
      .post<ResendEmailResponse>(`/api/hr/invite/${inv.invitation_id}/resend-email`)
      .subscribe({
        next: (res) => {
          this.resending.set(false);
          // Update the badge in place — the InvitationDetails signal needs
          // to reflect the new email_status without a full page reload.
          this.invitation.update(v =>
            v ? { ...v, email_status: res.email_status, email_error: res.email_error } : v
          );
          if (res.email_status === 'sent') {
            this.showToast(`Invitation email resent to ${inv.candidate_email}`, false);
          } else {
            this.showToast(
              `Email failed: ${res.email_error || 'unknown reason'}`,
              true
            );
          }
        },
        error: (err: ApiError) => {
          this.resending.set(false);
          if (err.status === 401) {
            this.router.navigate(['/login']);
            return;
          }
          if (err.status === 410) {
            // Test was submitted between page load and resend click.
            this.showToast(
              'This test has already been submitted — cannot resend.',
              true
            );
          } else {
            this.showToast(err.message || 'Could not resend email.', true);
          }
        },
      });
  }

  /**
   * Show a brief notification at the top of the page. Auto-dismisses after
   * 4 seconds. isError=true uses red styling instead of green.
   */
  private showToast(message: string, isError: boolean): void {
    if (this.toastTimer !== null) {
      clearTimeout(this.toastTimer);
    }
    this.toastMessage.set(message);
    this.toastIsError.set(isError);
    this.toastVisible.set(true);
    this.toastTimer = setTimeout(() => {
      this.toastVisible.set(false);
      this.toastTimer = null;
    }, 4000);
  }

  // ----- Template helpers (same as the old dashboard) -----

  formatSubmittedDateTime(submitted_at: string | null): string {
    return submitted_at ? new Date(submitted_at).toLocaleString() : '—';
  }

  audioQuestionLabel(question_index: number): string {
    return question_index >= 0 ? `Q${question_index + 1}` : 'Q?';
  }

  breakdownEntries(
    breakdown: Record<string, number | null> | null
  ): Array<{ key: string; value: number | null }> {
    if (!breakdown) return [];
    return Object.entries(breakdown).map(([key, value]) => ({
      key: this.humanizeKey(key),
      value,
    }));
  }

  /**
   * Convert a snake_case rubric key (e.g. "professional_communication") into
   * a human-readable label ("Professional Communication") for display.
   * Mirrors the legacy hr.js helper of the same name.
   */
  private humanizeKey(k: string): string {
    return String(k)
      .replace(/_/g, ' ')
      .replace(/\b\w/g, (c) => c.toUpperCase());
  }

  audioUrl(audioId: number): string {
    return `${environment.apiUrl}/api/hr/audio/${audioId}`;
  }

  /**
   * Map a submission_reason value to a visual badge for the candidate detail
   * header. Returns null for the default 'candidate_finished' (no badge shown)
   * or for unknown values (graceful fallback for old / forward-compat rows).
   */
  submissionReasonBadge(
    reason: string | null
  ): { color: string; icon: string; label: string } | null {
    switch (reason) {
      case 'reading_timer_expired':
        return { color: 'var(--text-muted)', icon: '⏱', label: 'Auto-submitted (reading timer)' };
      case 'writing_timer_expired':
        return { color: 'var(--text-muted)', icon: '⏱', label: 'Auto-submitted (writing timer)' };
      case 'speaking_timer_expired':
        return { color: 'var(--text-muted)', icon: '⏱', label: 'Auto-submitted (speaking timer)' };
      case 'tab_switch_termination':
        return { color: 'var(--red)', icon: '🚫', label: 'Terminated (tab switching)' };
      default:
        return null;
    }
  }
}