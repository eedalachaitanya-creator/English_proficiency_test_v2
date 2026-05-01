import { Injectable, signal, computed, inject } from '@angular/core';
import { Observable, map, tap, catchError, of } from 'rxjs';
import { ApiService, ApiError } from './api.service';
import { HRUser, HRLoginRequest } from '../models/hr.models';

/**
 * Wire-format response from GET /api/hr/session-status.
 * Always returns 200 — no red console errors when logged out.
 *
 * Logged in:  { logged_in: true,  user: { id, name, email } }
 * Logged out: { logged_in: false, user: null }
 */
interface SessionStatusResponse {
  logged_in: boolean;
  user: HRUser | null;
}

/**
 * HR authentication service.
 *
 * Two state signals:
 *   currentUser  — the HRUser object, or null if not logged in
 *   isLoggedIn   — derived boolean for *ngIf gating
 *
 * Three methods:
 *   checkSession() — silent probe via /api/hr/session-status (200 OK regardless)
 *   login()        — POST /api/hr/login (401 on bad creds, error propagates)
 *   logout()       — POST /api/hr/logout, swallows errors, always clears state
 */
@Injectable({ providedIn: 'root' })
export class AuthService {
  private api = inject(ApiService);

  readonly currentUser = signal<HRUser | null>(null);
  readonly isLoggedIn = computed(() => this.currentUser() !== null);

  /**
   * Silent session probe. Calls /api/hr/session-status which ALWAYS returns
   * 200 (never 401), so this method never triggers the red "401 Unauthorized"
   * error in the browser DevTools console.
   *
   * On success: updates currentUser and emits the user (or null if logged out).
   * On unexpected error (network down, 500): clears currentUser and emits null
   *                                            for HRUser semantics, but rethrows
   *                                            for the caller's error handler.
   *
   * The legacy /api/hr/me endpoint still exists on the backend for routes that
   * actually require auth (it raises 401). This service uses session-status
   * specifically because it's a probe, not a gate.
   */
  checkSession(): Observable<HRUser | null> {
    return this.api.get<SessionStatusResponse>('/api/hr/session-status').pipe(
      map((res) => {
        const user = res.logged_in ? res.user : null;
        this.currentUser.set(user);
        return user;
      }),
      catchError((err: ApiError) => {
        // Network/CORS/server errors land here. session-status itself never
        // returns 401, but we still defensively handle it for completeness.
        this.currentUser.set(null);
        if (err.status === 401) {
          return of(null);
        }
        throw err;
      })
    );
  }

  /**
   * Log in with email + password. On 200, server sets a session cookie
   * (carried automatically via withCredentials in ApiService) and we cache
   * the returned user. On 401 (bad creds), the error propagates so the
   * Login component can surface err.message ("Invalid email or password.").
   */
  login(credentials: HRLoginRequest): Observable<HRUser> {
    return this.api.post<HRUser>('/api/hr/login', credentials).pipe(
      tap((user) => this.currentUser.set(user))
    );
  }

  /**
   * Log out and clear local state. Even if the server call fails (e.g.
   * session already expired), we still clear local currentUser so the UI
   * reflects logged-out state. Matches the old hr.js behaviour:
   *
   *   try { await api('/api/hr/logout', { method: 'POST' }); } catch {}
   *   window.location.href = 'index.html';
   */
  logout(): Observable<void> {
    return this.api.post<void>('/api/hr/logout').pipe(
      tap(() => this.currentUser.set(null)),
      catchError(() => {
        this.currentUser.set(null);
        return of(undefined as void);
      })
    );
  }
}