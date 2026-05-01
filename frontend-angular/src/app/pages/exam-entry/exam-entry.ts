import { Component, OnInit, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';

import { ApiService, ApiError } from '../../core/services/api.service';
import { Topnav } from '../../shared/components/topnav/topnav';
import { Footer } from '../../shared/components/footer/footer';

interface VerifyCodeRequest {
  token: string;
  code: string;
}

interface VerifyCodeResponse {
  success: boolean;
  redirect_to?: string;
  detail?: string;
}

@Component({
  selector: 'app-exam-entry',
  standalone: true,
  imports: [CommonModule, FormsModule, Topnav, Footer],
  templateUrl: './exam-entry.html',
  styleUrl: './exam-entry.css',
})
export class ExamEntry implements OnInit {
  private api = inject(ApiService);
  private route = inject(ActivatedRoute);
  private router = inject(Router);

  token = signal<string>('');
  code = '';
  isValidCode = signal(false);
  submitting = signal(false);
  errorMsg = signal('');
  hasError = signal(false);
  isLocked = signal(false);
  noToken = signal(false);

  ngOnInit(): void {
    const token = this.route.snapshot.paramMap.get('token');
    if (!token) {
      this.noToken.set(true);
      return;
    }
    this.token.set(token);
  }

  onCodeChange(): void {
    const cleaned = this.code.replace(/\D/g, '').slice(0, 6);
    if (cleaned !== this.code) {
      this.code = cleaned;
    }
    this.isValidCode.set(cleaned.length === 6);
    this.hasError.set(false);
    this.errorMsg.set('');
  }

  onSubmit(): void {
    if (!this.isValidCode() || this.submitting() || this.isLocked()) return;

    this.submitting.set(true);
    this.errorMsg.set('');
    this.hasError.set(false);

    const body: VerifyCodeRequest = {
      token: this.token(),
      code: this.code,
    };

    this.api.post<VerifyCodeResponse>('/api/exam/verify-code', body).subscribe({
      next: (res) => {
        this.submitting.set(false);
        if (res.success) {
          this.router.navigate(['/instructions']);
        } else {
          this.errorMsg.set(res.detail || 'Verification failed.');
          this.hasError.set(true);
        }
      },
      error: (err: ApiError) => {
        this.submitting.set(false);
        this.code = '';
        this.isValidCode.set(false);
        this.hasError.set(true);
        this.errorMsg.set(err.message || 'Could not verify code.');

        if (err.status === 423 || err.status === 410 || err.status === 404) {
          this.isLocked.set(true);
        }
      },
    });
  }
}