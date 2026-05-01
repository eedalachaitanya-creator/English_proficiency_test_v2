import { Component, OnInit, inject, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';

import { ApiService, ApiError } from '../../core/services/api.service';
import { AuthService } from '../../core/services/auth.service';
import { ResultDetail } from '../../core/models/hr.models';
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
  private route = inject(ActivatedRoute);
  private router = inject(Router);

  // Reactive state.
  loading = signal(true);
  loadError = signal('');
  detail = signal<ResultDetail | null>(null);
  invitationId = signal<number | null>(null);

  // Convenience for template.
  hrEmail = computed(() => this.auth.currentUser()?.email ?? 'Loading…');

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
    this.api.get<ResultDetail>(`/api/hr/results/${id}`).subscribe({
      next: (d) => {
        this.detail.set(d);
        this.loading.set(false);
      },
      error: (err: ApiError) => {
        if (err.status === 401) {
          this.router.navigate(['/login']);
          return;
        }
        if (err.status === 404) {
          // 404 either means the invitation doesn't exist OR (per the
          // backend's tenancy check) it belongs to a different HR. The
          // backend deliberately returns 404 in both cases to avoid
          // leaking which invitation IDs exist.
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
    return Object.entries(breakdown).map(([key, value]) => ({ key, value }));
  }

  audioUrl(audioId: number): string {
    return `http://localhost:8000/api/hr/audio/${audioId}`;
  }
}