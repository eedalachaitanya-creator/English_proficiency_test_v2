import { inject } from '@angular/core';
import { CanActivateFn, Router, UrlTree } from '@angular/router';
import { Observable, map, catchError, of } from 'rxjs';
import { AuthService } from '../services/auth.service';
import { ApiError } from '../services/api.service';

/**
 * Route guard for HR-only pages.
 *
 * Calls /api/hr/me before allowing navigation. Decision tree:
 *
 *   ✓ 200 → user logged in → ALLOW
 *   ✗ 401 → no session → REDIRECT to /login
 *   ✗ Any other error → DENY → REDIRECT to /login (user can retry from login)
 *
 * Apply to HR routes:
 *   { path: 'dashboard', loadComponent: ..., canActivate: [hrAuthGuard] }
 */
export const hrAuthGuard: CanActivateFn = (): Observable<boolean | UrlTree> => {
  const auth = inject(AuthService);
  const router = inject(Router);

  return auth.checkSession().pipe(
    map((user): boolean | UrlTree => {
      if (user) {
        return true;
      }
      return router.createUrlTree(['/login']);
    }),
    catchError((_err: ApiError): Observable<UrlTree> => {
      return of(router.createUrlTree(['/login']));
    })
  );
};