import { inject } from '@angular/core';
import { CanActivateFn, Router, UrlTree } from '@angular/router';
import { Observable, map, catchError, of } from 'rxjs';
import { TestContentService } from '../services/test-content.service';
import { ApiError } from '../services/api.service';

/**
 * Route guard for candidate-only pages (instructions, reading, writing,
 * speaking, submitted).
 *
 * Probes /api/test-content as a combined session-check + content-prefetch.
 * Successful response means the candidate has a valid session and the test
 * is still active. Cached test content is reused by subsequent pages.
 *
 * Decision tree:
 *
 *   ✓ 200 → ALLOW
 *   ✗ 410 → REDIRECT to /submitted (if refId cached) else /login
 *   ✗ Any other failure → REDIRECT to /login
 *
 * Apply to candidate routes:
 *   { path: 'reading', loadComponent: ..., canActivate: [candidateGuard] }
 */
export const candidateGuard: CanActivateFn = (): Observable<boolean | UrlTree> => {
  const testContent = inject(TestContentService);
  const router = inject(Router);

  return testContent.load().pipe(
    map((): boolean | UrlTree => true),
    catchError((err: ApiError): Observable<UrlTree> => {
      if (err.status === 410) {
        const cached = sessionStorage.getItem('refId');
        if (cached) {
          return of(router.createUrlTree(['/submitted']));
        }
      }
      return of(router.createUrlTree(['/login']));
    })
  );
};