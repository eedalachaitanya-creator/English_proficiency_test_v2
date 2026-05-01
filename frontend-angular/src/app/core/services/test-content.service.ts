import { Injectable, inject, signal } from '@angular/core';
import { Observable, from, of, tap, catchError, throwError } from 'rxjs';
import { ApiService, ApiError } from './api.service';
import { StoreService } from './store.service';
import { TestContent } from '../models/test.models';

/**
 * Loads and caches the candidate's test content (passage + questions +
 * writing topic + speaking topics + timing).
 *
 * Mirrors the loadTestContent() helper from the old common.js:
 *
 *   1. Check sessionStorage. If we already fetched it, return that.
 *   2. Otherwise, GET /api/test-content. The candidate session cookie
 *      tells the server which invitation to look up.
 *   3. Cache the result in sessionStorage and return it.
 *
 * On error, the service propagates an ApiError with an actionable message:
 *   - 401: candidate session expired or never set (link not yet visited)
 *   - 410: invitation already submitted or token expired
 *   - 500: no content seeded
 *
 * Pages that consume this service should catch the error and either:
 *   - Show a friendly inline error and offer to redirect, OR
 *   - Bounce to a candidate-error page (we don't have one yet — see the
 *     fallback in the old common.js).
 *
 * Also exposed: a `content` signal so templates can bind to it directly
 * once loaded. This is optional — most pages will subscribe to load()
 * once and assign the result to their own component property.
 */
@Injectable({ providedIn: 'root' })
export class TestContentService {
  private api = inject(ApiService);
  private store = inject(StoreService);

  /**
   * Reactive view of the currently loaded test content. Components can
   * either bind to this in templates ({{ contentSignal()?.candidate_name }})
   * or read the value returned by load(). Both stay in sync.
   */
  readonly content = signal<TestContent | null>(null);

  /**
   * Returns the test content — from cache if available, otherwise from
   * the server. Subsequent calls within the same session hit the cache
   * and complete synchronously (wrapped in `of()` for Observable consistency).
   *
   * Caller pattern:
   *
   *   private testContent = inject(TestContentService);
   *
   *   ngOnInit() {
   *     this.testContent.load().subscribe({
   *       next: (content) => { this.content = content; ... },
   *       error: (err: ApiError) => { ... show inline error ... },
   *     });
   *   }
   */
  load(): Observable<TestContent> {
    const cached = this.store.getTestContent();
    if (cached) {
      this.content.set(cached);
      return of(cached);
    }

    return this.api.get<TestContent>('/api/test-content').pipe(
      tap((data) => {
        this.store.setTestContent(data);
        this.content.set(data);
      }),
      catchError((err: ApiError) => {
        // Re-throw so the caller can branch on err.status. We don't
        // attempt to render an error page here — pages own their UX.
        return throwError(() => err);
      })
    );
  }

  /**
   * Force a fresh fetch even if the cache is populated. Rarely needed —
   * one use case is the candidate's exam-entry landing route, which sets
   * the candidate session cookie via /exam/:token and then wants the
   * content under the new session.
   */
  reload(): Observable<TestContent> {
    this.store.clearTestContent();
    this.content.set(null);
    return this.load();
  }

  /**
   * Clear cached content. Called after submission so a refresh on the
   * submitted page doesn't try to fetch content for an already-submitted
   * invitation. The submitted page calls store.clearTestSession() which
   * already wipes this; this helper is here for symmetry / explicit clears.
   */
  clear(): void {
    this.store.clearTestContent();
    this.content.set(null);
  }
}