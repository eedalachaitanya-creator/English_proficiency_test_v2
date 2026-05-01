import { Component, OnInit, inject, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';

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
@Component({
  selector: 'app-hr-dashboard',
  standalone: true,
  imports: [CommonModule, FormsModule, Topnav, Footer],
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
  inviteSubmitting = signal(false);
  inviteError = signal('');
  inviteResult = signal<InviteCreateResponse | null>(null);
  inviteCopied = signal(false);

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
    this.inviteError.set('');
    this.inviteResult.set(null);
    this.inviteCopied.set(false);
    this.inviteSubmitting.set(false);
    this.inviteOpen.set(true);
  }

  closeInvite(): void {
    this.inviteOpen.set(false);
  }

  submitInvite(): void {
    this.inviteError.set('');
    const name = this.invName.trim();
    const email = this.invEmail.trim();

    if (!name || !email) {
      this.inviteError.set('Both fields are required.');
      return;
    }
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      this.inviteError.set('Enter a valid email address.');
      return;
    }

    const body: InviteCreateRequest = {
      candidate_name: name,
      candidate_email: email,
      difficulty: this.invDifficulty,
    };

    this.inviteSubmitting.set(true);
    this.api.post<InviteCreateResponse>('/api/hr/invite', body).subscribe({
      next: (res) => {
        this.inviteResult.set(res);
        this.inviteSubmitting.set(false);
        this.loadResults();
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