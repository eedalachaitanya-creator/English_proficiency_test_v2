import { Component, inject, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';

import { AuthService } from '../../core/services/auth.service';
import { ApiError } from '../../core/services/api.service';
import { Topnav } from '../../shared/components/topnav/topnav';
import { Footer } from '../../shared/components/footer/footer';

/**
 * HR Sign-in page. Replaces the old index.html + login.js exactly.
 *
 * Behavior (matching the old login.js):
 *
 *   1. On mount, check if there's an active HR session via /api/hr/me.
 *      If yes, skip the form and redirect to /dashboard immediately.
 *      (Avoids forcing an already-logged-in HR to re-enter credentials.)
 *
 *   2. Validate email format and password presence client-side before
 *      hitting the server. Same regex as login.js.
 *
 *   3. POST /api/hr/login with {email, password}. On 200, the session
 *      cookie is set automatically and we redirect to /dashboard.
 *
 *   4. On error, display the server's "detail" message under the form.
 *      The button reverts from "Signing in…" back to "SIGN IN →".
 *
 * Note: this component is lazy-loaded by the router (see app.routes.ts),
 * so its code only ships to the browser when the user actually visits /login.
 */
@Component({
  selector: 'app-login',
  standalone: true,
  imports: [CommonModule, FormsModule, Topnav, Footer],
  templateUrl: './login.html',
  styleUrl: './login.css',
})
export class Login implements OnInit {
  private auth = inject(AuthService);
  private router = inject(Router);

  // Form state — bound to <input> via [(ngModel)] in the template.
  email = '';
  password = '';

  // UI state — Signals so the template auto-re-renders on change.
  errorMessage = signal('');
  submitting = signal(false);

  ngOnInit(): void {
    // If there's already an active HR session (e.g., user came back to /login
    // with a valid cookie), skip the form and go straight to the dashboard.
    // checkSession() in AuthService handles 401 silently and emits null.
    this.auth.checkSession().subscribe({
      next: (user) => {
        if (user) {
          this.router.navigate(['/dashboard']);
        }
      },
      // checkSession only re-throws non-401 errors (network down, etc.).
      // We swallow them here — the user can just see the empty form and
      // try to log in normally. If the server is genuinely unreachable
      // they'll find out when they hit Submit.
      error: () => {},
    });
  }

  /**
   * Form submit handler. Triggered by (ngSubmit) on the <form>.
   * Validates locally first, then calls the auth service.
   */
  onSubmit(): void {
    this.errorMessage.set('');

    // Same validation regex as the old login.js — accepts most reasonable
    // email shapes without being draconian. The server validates again
    // anyway (Pydantic EmailStr).
    const emailValid = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(this.email.trim());
    if (!emailValid) {
      this.errorMessage.set('Enter a valid email address.');
      return;
    }
    if (!this.password) {
      this.errorMessage.set('Password is required.');
      return;
    }

    this.submitting.set(true);
    this.auth.login({ email: this.email.trim(), password: this.password }).subscribe({
      next: () => {
        // AuthService caches the user on success — dashboard reads from cache.
        this.router.navigate(['/dashboard']);
      },
      error: (err: ApiError) => {
        this.submitting.set(false);
        // Server returns 401 with detail "Invalid email or password" for
        // bad creds; 422 with field-validation array for malformed input
        // (handled by ApiService, joined into a readable string).
        this.errorMessage.set(err.message || 'Login failed.');
      },
    });
  }
}