import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpErrorResponse, HttpHeaders } from '@angular/common/http';
import { Observable, catchError, throwError } from 'rxjs';
import { environment } from '../../../environments/environment';

/**
 * Custom error class thrown by ApiService on non-2xx responses.
 * Mirrors the shape of the old common.js api() helper:
 *   - .message      → human-readable error text (FastAPI's "detail" field)
 *   - .status       → HTTP status code (401, 410, 422, 500, etc.)
 *   - .data         → the raw response body, useful for debugging
 *
 * Components catch this and inspect .status to decide what to do — e.g.,
 * 401 means redirect to /login; 410 means the test link is expired.
 */
export class ApiError extends Error {
  constructor(
    public override message: string,
    public status: number,
    public data: unknown
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

/**
 * Thin wrapper around Angular's HttpClient that mirrors the old
 * common.js api() function exactly:
 *
 *   - Always sends cookies (withCredentials: true) so the session
 *     middleware on FastAPI sees the HR/candidate session cookie.
 *   - Auto-stringifies JSON request bodies and sets Content-Type.
 *   - Lets FormData pass through untouched (the browser sets the
 *     correct multipart boundary header automatically — used by the
 *     speaking section's submit, where audio blobs go up).
 *   - Normalises FastAPI's two error shapes:
 *       { detail: "string message" }                  ← 401, 410, 500
 *       { detail: [ { msg, loc, type }, ... ] }       ← 422 validation
 *     into a single readable string on the thrown ApiError.
 *
 * Usage:
 *   const api = inject(ApiService);
 *   api.get<HRUser>('/api/hr/me').subscribe({...});
 *   api.post<HRUser>('/api/hr/login', { email, password }).subscribe({...});
 */
@Injectable({ providedIn: 'root' })
export class ApiService {
  private http = inject(HttpClient);

  /**
   * Where to send API requests.
   *
   * In development the Angular dev server runs at :4200 and FastAPI runs at
   * :8000, so we need an absolute URL here. In production (single-deploy
   * where FastAPI serves the built Angular files) the frontend and API
   * share an origin and the absolute URL still works.
   *
   * Override at build time via Angular environment files later if needed.
   */
  private readonly baseUrl = environment.apiUrl;

  /** Standard GET. Returns an Observable of the typed response. */
  get<T>(path: string): Observable<T> {
    return this.http
      .get<T>(this.url(path), { withCredentials: true })
      .pipe(catchError(err => this.handleError(err)));
  }

  /** Standard POST with a JSON body. Pass null/undefined for empty body. */
  post<T>(path: string, body: unknown = null): Observable<T> {
    if (body instanceof FormData) {
      // Browser sets multipart Content-Type with boundary automatically.
      // Don't set it manually or the boundary will be missing and the
      // server will reject the request as malformed multipart.
      return this.http
        .post<T>(this.url(path), body, { withCredentials: true })
        .pipe(catchError(err => this.handleError(err)));
    }

    const headers = new HttpHeaders({ 'Content-Type': 'application/json' });
    return this.http
      .post<T>(this.url(path), body, { headers, withCredentials: true })
      .pipe(catchError(err => this.handleError(err)));
  }

  /** Standard DELETE. Used for any future cleanup endpoints. */
  delete<T>(path: string): Observable<T> {
    return this.http
      .delete<T>(this.url(path), { withCredentials: true })
      .pipe(catchError(err => this.handleError(err)));
  }

  // ---------------------------------------------------------------------
  //  Internal helpers
  // ---------------------------------------------------------------------

  private url(path: string): string {
    // Tolerate paths passed with or without a leading slash.
    if (path.startsWith('http')) return path; // absolute URL passthrough
    if (path.startsWith('/')) return `${this.baseUrl}${path}`;
    return `${this.baseUrl}/${path}`;
  }

  /**
   * Convert HttpErrorResponse into our ApiError with a clean human message.
   * Handles FastAPI's two distinct error shapes — see class doc comment.
   */
  private handleError(err: HttpErrorResponse): Observable<never> {
    let message: string;
    const data = err.error;

    if (data && Array.isArray(data.detail)) {
      // 422 validation — array of { msg, loc, type } objects.
      message = data.detail
        .map((e: { msg: string; loc?: (string | number)[] }) => {
          const field = Array.isArray(e.loc) ? e.loc[e.loc.length - 1] : '';
          return field ? `${field}: ${e.msg}` : e.msg;
        })
        .join('; ');
    } else if (data && typeof data.detail === 'string') {
      // Most application-level errors — single string detail.
      message = data.detail;
    } else if (err.status === 0) {
      // Status 0 means the request never reached the server — usually CORS
      // misconfiguration in dev, or the FastAPI process isn't running.
      message =
        'Could not reach the server. Is the backend running on port 8000?';
    } else {
      message = err.statusText || `HTTP ${err.status}`;
    }

    return throwError(() => new ApiError(message, err.status, data));
  }
}