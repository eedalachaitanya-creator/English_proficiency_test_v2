import { Component, OnInit, inject, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';

import { ApiService, ApiError } from '../../core/services/api.service';
import { AuthService } from '../../core/services/auth.service';
import {
  ResultRow,
  InviteCreateRequest,
  InviteCreateResponse,
} from '../../core/models/hr.models';
import { Topnav } from '../../shared/components/topnav/topnav';
import { Footer } from '../../shared/components/footer/footer';

/**
 * HR Dashboard — list view only.
 *
 * Phase 4 changes (this file):
 *   - REMOVED: detail panel rendering (now lives at /dashboard/candidate/:id)
 *   - REMOVED: selectedRow / detail / detailLoading / detailError state
 *   - REMOVED: onRowClick() loading detail inline
 *   - CHANGED: row click navigates to /dashboard/candidate/:id instead
 *   - ADDED: pagination (10 rows per page)
 *
 * Retained:
 *   - Auth + session check
 *   - KPI cards
 *   - Search + status filter
 *   - Logout
 *   - + INVITE NEW CANDIDATE button + invite modal
 *
 * The page is now noticeably shorter and faster to scan.
 */
/**
 * Convert a wall-clock date/time entered by HR into a UTC Date, interpreting
 * the input in the given IANA timezone — NOT in the browser's local zone.
 *
 * Why this is non-trivial: JavaScript's `new Date("2026-05-04T16:57")`
 * parses the string in browser-local time. There is no built-in way to say
 * "interpret these wall-clock numbers as IST" if the browser is set to PST.
 * So we compute the offset by formatting a candidate UTC moment back into
 * the target zone and measuring how far off we are. Two iterations are
 * enough — this technique is robust across DST transitions because it
 * uses Intl.DateTimeFormat (which knows the IANA database).
 *
 * Returns null if the inputs are malformed (e.g. empty strings).
 *
 * Inputs:
 *   dateStr = "YYYY-MM-DD"
 *   timeStr = "HH:MM"
 *   tz      = IANA zone name (e.g. "Asia/Kolkata", "America/Los_Angeles")
 */
function wallClockToUtc(dateStr: string, timeStr: string, tz: string): Date | null {
  if (!dateStr || !timeStr) return null;
  const [yStr, mStr, dStr] = dateStr.split('-');
  const [hStr, minStr] = timeStr.split(':');
  const y = +yStr, m = +mStr, d = +dStr, h = +hStr, min = +minStr;
  if ([y, m, d, h, min].some(n => Number.isNaN(n))) return null;

  // First guess: pretend the wall clock IS UTC. We'll measure how wrong
  // this is in the target timezone and correct.
  const guess = Date.UTC(y, m - 1, d, h, min, 0);

  // Iterate twice — once to correct, once to handle DST edge cases where
  // the first correction crosses a transition.
  let utcMs = guess;
  for (let i = 0; i < 2; i++) {
    const partsInTz = getPartsInTimezone(new Date(utcMs), tz);
    const tzAsUtcMs = Date.UTC(
      partsInTz.year, partsInTz.month - 1, partsInTz.day,
      partsInTz.hour, partsInTz.minute, 0
    );
    // Difference = how many ms ahead the timezone is of UTC at this instant.
    const offsetMs = tzAsUtcMs - utcMs;
    // To make the wall clock in tz read (y, m, d, h, min), the actual UTC
    // moment must be the guess MINUS the timezone's offset.
    utcMs = guess - offsetMs;
  }
  return new Date(utcMs);
}

/**
 * Read y/m/d/h/min of a Date as it appears in the given IANA timezone.
 * Uses Intl.DateTimeFormat — the only stdlib API that knows IANA zones.
 */
function getPartsInTimezone(d: Date, tz: string): {
  year: number; month: number; day: number; hour: number; minute: number;
} {
  const fmt = new Intl.DateTimeFormat('en-US', {
    timeZone: tz,
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  });
  const parts: Record<string, string> = {};
  for (const p of fmt.formatToParts(d)) {
    if (p.type !== 'literal') parts[p.type] = p.value;
  }
  // Intl can return "24" for hour at midnight in some locales — normalize.
  const hour = parts['hour'] === '24' ? 0 : +parts['hour'];
  return {
    year: +parts['year'],
    month: +parts['month'],
    day: +parts['day'],
    hour,
    minute: +parts['minute'],
  };
}

@Component({
  selector: 'app-hr-dashboard',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink, Topnav, Footer],
  templateUrl: './hr-dashboard.html',
  styleUrl: './hr-dashboard.css',
})
export class HrDashboard implements OnInit {
  private api = inject(ApiService);
  private auth = inject(AuthService);
  private router = inject(Router);

  // -------- List/filter state --------
  loading = signal(true);
  loadError = signal('');
  allResults = signal<ResultRow[]>([]);
  filteredResults = signal<ResultRow[]>([]);

  searchQuery = '';
  statusFilter: '' | 'submitted' | 'pending' = '';

  // -------- Pagination state --------
  /** 1-indexed page number. The user-visible "page 1" maps to filteredResults[0..9]. */
  currentPage = signal(1);

  /** How many rows to show per page. Matches the old hr.js default. */
  readonly pageSize = 10;

  /**
   * Slice of filteredResults for the currently visible page.
   * Recomputes when filteredResults or currentPage changes.
   */
  pagedResults = computed(() => {
    const start = (this.currentPage() - 1) * this.pageSize;
    const end = start + this.pageSize;
    return this.filteredResults().slice(start, end);
  });

  /** Total page count, minimum 1 (so the UI shows "Page 1 of 1" even when empty). */
  totalPages = computed(() => {
    const total = this.filteredResults().length;
    return Math.max(1, Math.ceil(total / this.pageSize));
  });

  // -------- KPI computations (unchanged from Phase 3) --------
  kpiTotal = computed(() => this.allResults().length);
  kpiSubmitted = computed(() => this.allResults().filter((r: ResultRow) => r.submitted_at).length);
  kpiPending = computed(() => this.kpiTotal() - this.kpiSubmitted());
  kpiAvg = computed(() => {
    const scored = this.allResults().filter((r: ResultRow) => r.total_score != null);
    if (scored.length === 0) return null;
    const sum = scored.reduce((s: number, r: ResultRow) => s + (r.total_score ?? 0), 0);
    return Math.round(sum / scored.length);
  });

  hrEmail = computed(() => this.auth.currentUser()?.email ?? 'Loading…');

  // -------- Invite modal state --------
  inviteOpen = signal(false);
  invName = '';
  invEmail = '';
  invDifficulty: 'intermediate' | 'expert' = 'intermediate';
  // Scheduled URL window — split into one date + two times (start/end on the
  // same day). Bound to native <input type="date"> ("YYYY-MM-DD") and
  // <input type="time"> ("HH:MM"). Combined into ISO UTC before POST.
  invDate = '';
  invStartTime = '';
  invEndTime = '';
  // IANA timezone the HR picked. Defaults to IST since most users are in
  // India; the dropdown lists 7 supported zones (IST + 6 US zones). Keep
  // these in sync with backend schemas.ALLOWED_TIMEZONES — the backend
  // rejects any zone not in its allowlist with HTTP 422.
  invTimezone: string = 'Asia/Kolkata';
  inviteSubmitting = signal(false);
  inviteError = signal('');
  inviteResult = signal<InviteCreateResponse | null>(null);
  inviteCopied = signal(false);

  // -------- Toast state --------
  // Small notification banner shown briefly at the top of the page after
  // a successful invitation (the modal closes immediately on success).
  // We use a single signal pair instead of building a full Toast component
  // because there's only one place we need this and the markup is small.
  toastMessage = signal('');
  toastVisible = signal(false);
  /** Tracks the auto-dismiss timer so a second toast cancels the first. */
  private toastTimer: ReturnType<typeof setTimeout> | null = null;

  // -------- Lifecycle --------
  ngOnInit(): void {
    this.auth.checkSession().subscribe({
      next: (user) => {
        if (!user) {
          this.router.navigate(['/login']);
          return;
        }
        this.loadResults();
      },
      error: () => this.router.navigate(['/login']),
    });
  }

  private loadResults(): void {
    this.loading.set(true);
    this.loadError.set('');
    this.api.get<ResultRow[]>('/api/hr/results').subscribe({
      next: (rows) => {
        this.allResults.set(rows);
        this.filteredResults.set(rows);
        this.loading.set(false);
        // Reset to page 1 in case the previous filter state left us on a
        // page that no longer exists with the new data.
        this.currentPage.set(1);
      },
      error: (err: ApiError) => {
        if (err.status === 401) {
          this.router.navigate(['/login']);
          return;
        }
        this.loadError.set(err.message || 'Could not load results.');
        this.loading.set(false);
      },
    });
  }

  // -------- Filter handler --------
  applyFilters(): void {
    const q = this.searchQuery.trim().toLowerCase();
    const status = this.statusFilter;
    const all = this.allResults();
    const filtered = all.filter((r: ResultRow) => {
      const matchesQ = !q
        || r.candidate_name.toLowerCase().includes(q)
        || r.candidate_email.toLowerCase().includes(q);
      const isSubmitted = !!r.submitted_at;
      const matchesS = !status
        || (status === 'submitted' && isSubmitted)
        || (status === 'pending' && !isSubmitted);
      return matchesQ && matchesS;
    });
    this.filteredResults.set(filtered);
    // After filtering, reset to page 1 (otherwise filtering down to 3 results
    // while you're on page 5 would show an empty page).
    this.currentPage.set(1);
  }

  // -------- Pagination handlers --------
  goToPage(page: number): void {
    if (page < 1) page = 1;
    const max = this.totalPages();
    if (page > max) page = max;
    this.currentPage.set(page);
  }

  prevPage(): void {
    this.goToPage(this.currentPage() - 1);
  }

  nextPage(): void {
    this.goToPage(this.currentPage() + 1);
  }

  /**
   * Returns an array of page numbers to render in the pagination bar.
   * For small page counts we just show all pages: [1, 2, 3]
   * For larger counts we truncate around the current page: [1, '…', 4, 5, 6, '…', 20]
   *
   * Returning a string '…' makes the template easy: render number as a button,
   * render string as a non-clickable spacer.
   */
  pageNumbers = computed((): Array<number | string> => {
    const total = this.totalPages();
    const current = this.currentPage();

    if (total <= 7) {
      // Show all pages — fits without truncation.
      return Array.from({ length: total }, (_, i) => i + 1);
    }

    const pages: Array<number | string> = [1];
    if (current > 3) pages.push('…');
    const start = Math.max(2, current - 1);
    const end = Math.min(total - 1, current + 1);
    for (let i = start; i <= end; i++) pages.push(i);
    if (current < total - 2) pages.push('…');
    pages.push(total);
    return pages;
  });

  // -------- Logout --------
  onLogout(): void {
    this.auth.logout().subscribe(() => this.router.navigate(['/login']));
  }

  // -------- Row click → navigate to detail page --------
  onRowClick(row: ResultRow): void {
    this.router.navigate(['/dashboard/candidate', row.invitation_id]);
  }

  // -------- Invite modal --------
  openInvite(): void {
    this.invName = '';
    this.invEmail = '';
    this.invDifficulty = 'intermediate';
    // Empty by default — HR must explicitly pick the date and times.
    this.invDate = '';
    this.invStartTime = '';
    this.invEndTime = '';
    this.invTimezone = 'Asia/Kolkata';
    this.inviteError.set('');
    this.inviteResult.set(null);
    this.inviteCopied.set(false);
    this.inviteSubmitting.set(false);
    this.inviteOpen.set(true);
  }

  closeInvite(): void {
    this.inviteOpen.set(false);
  }

  /**
   * Programmatically open the native date/time picker when the user clicks
   * anywhere on the input — not just the icon. Without this, Chrome only
   * opens the picker when the user clicks the calendar/clock icon on the
   * right edge of the input, which most users don't realize they need to do.
   *
   * showPicker() is a recent addition (Chrome 99+, Firefox 101+, Safari 16+).
   * The optional chaining (?.()) makes the call a no-op on older browsers,
   * so we degrade gracefully — old browsers still need an icon click but
   * nothing throws.
   *
   * Why a method (not inline in the template): the template is HTML, and
   * embedding TypeScript expressions like $any($event.target).showPicker?.()
   * works but is hard to read and skips type checking. Putting it here gives
   * us a real cast and a place to add behavior later (e.g. analytics).
   */
  openPicker(event: Event): void {
    const target = event.target as HTMLInputElement & { showPicker?: () => void };
    target.showPicker?.();
  }

  submitInvite(): void {
    this.inviteError.set('');
    const name = this.invName.trim();
    const email = this.invEmail.trim();

    if (!name || !email) {
      this.inviteError.set('Both name and email are required.');
      return;
    }
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      this.inviteError.set('Enter a valid email address.');
      return;
    }
    if (!this.invDate || !this.invStartTime || !this.invEndTime) {
      this.inviteError.set('Pick the test date, start time, and end time.');
      return;
    }

    // Combine date + time strings into a UTC instant, interpreting the
    // wall-clock value in the HR-selected timezone (NOT browser local).
    //   invDate      = "YYYY-MM-DD"
    //   invStartTime = "HH:MM"
    //   invTimezone  = IANA zone name (e.g. "America/Los_Angeles")
    // wallClockToUtc returns a UTC Date or null if the inputs are malformed.
    const fromDate = wallClockToUtc(this.invDate, this.invStartTime, this.invTimezone);
    const untilDate = wallClockToUtc(this.invDate, this.invEndTime, this.invTimezone);
    if (!fromDate || !untilDate) {
      this.inviteError.set('Invalid date/time. Please re-enter.');
      return;
    }
    const fromMs = fromDate.getTime();
    const untilMs = untilDate.getTime();
    if (fromMs < Date.now() - 60_000) {
      this.inviteError.set('Start time cannot be in the past.');
      return;
    }
    if (untilMs <= fromMs) {
      this.inviteError.set('End time must be after start time.');
      return;
    }
    if (untilMs - fromMs < 60 * 60 * 1000) {
      this.inviteError.set('Window must be at least 60 minutes (the test takes ~60 min).');
      return;
    }

    const body: InviteCreateRequest = {
      candidate_name: name,
      candidate_email: email,
      difficulty: this.invDifficulty,
      valid_from: fromDate.toISOString(),
      valid_until: untilDate.toISOString(),
      timezone: this.invTimezone,
    };

    this.inviteSubmitting.set(true);
    this.api.post<InviteCreateResponse>('/api/hr/invite', body).subscribe({
      next: (res) => {
        this.inviteSubmitting.set(false);

        if (res.email_status === 'sent') {
          // Happy path: email went out. Close the modal, show a brief toast,
          // refresh the table so the new candidate row appears immediately.
          this.closeInvite();
          this.showToast(`Invitation sent to ${res.candidate_email}`);
          this.loadResults();
        } else {
          // Failed (or 'pending' = SMTP not configured at all). Keep the
          // modal open so HR can copy URL+code manually. inviteResult drives
          // the "fallback" view in the template; inviteError shows the
          // SMTP failure reason at the top of that view.
          this.inviteResult.set(res);
          this.inviteError.set(
            res.email_error
              ? `Email failed: ${res.email_error}`
              : 'Email could not be sent. Copy the URL and code below to send manually.'
          );
          // Refresh table even on failure — the invitation IS in the database,
          // just the email send failed. HR can see it in the list with a
          // "failed" status badge (Step 2c will add that).
          this.loadResults();
        }
      },
      error: (err: ApiError) => {
        this.inviteSubmitting.set(false);
        if (err.status === 401) {
          this.router.navigate(['/login']);
          return;
        }
        this.inviteError.set(err.message || 'Could not create invitation.');
      },
    });
  }

  /**
   * Show a brief notification at the top of the page. Auto-dismisses after
   * 4 seconds. Calling again before dismiss replaces the previous message
   * (and resets the timer).
   */
  private showToast(message: string): void {
    if (this.toastTimer !== null) {
      clearTimeout(this.toastTimer);
    }
    this.toastMessage.set(message);
    this.toastVisible.set(true);
    this.toastTimer = setTimeout(() => {
      this.toastVisible.set(false);
      this.toastTimer = null;
    }, 4000);
  }

  copyInviteUrl(): void {
    const res = this.inviteResult();
    if (!res) return;
    navigator.clipboard.writeText(res.exam_url).then(() => {
      this.inviteCopied.set(true);
    }).catch(() => {
      this.inviteCopied.set(true);
    });
  }

  // -------- Template helpers --------
  ratingLabel(rating: string | null): string {
    if (rating === 'recommended') return 'Recommended';
    if (rating === 'borderline') return 'Borderline';
    if (rating === 'not_recommended') return 'Not Recommended';
    return 'pending';
  }

  ratingClass(rating: string | null): string {
    if (rating === 'recommended') return 'reviewed';
    if (rating === 'borderline') return 'new';
    if (rating === 'not_recommended') return 'flagged';
    return '';
  }

  formatSubmittedDate(submitted_at: string | null): string {
    return submitted_at ? new Date(submitted_at).toLocaleDateString() : '—';
  }

  formatSubmittedDateTime(submitted_at: string | null): string {
    return submitted_at ? new Date(submitted_at).toLocaleString() : '—';
  }
}