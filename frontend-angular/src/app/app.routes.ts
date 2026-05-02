import { Routes } from '@angular/router';
import { hrAuthGuard } from './core/guards/hr-auth.guard';

/**
 * COMPLETE — all 10 steps wired:
 *   /                          → /login
 *   /login                     → Login (HR)
 *   /dashboard                 → HrDashboard (HR-guarded)
 *   /dashboard/candidate/:id   → CandidateDetail (HR-guarded)
 *   /exam/:token               → ExamEntry (passcode)
 *   /instructions              → Instructions
 *   /reading                   → Reading (Step 7)
 *   /writing                   → Writing (Step 8)
 *   /speaking                  → Speaking (Step 9)
 *   /submitted                 → Submitted (Step 10)
 */
export const routes: Routes = [
  {
    path: '',
    pathMatch: 'full',
    redirectTo: 'login',
  },
  {
    path: 'login',
    loadComponent: () =>
      import('./pages/login/login').then(m => m.Login),
  },
  {
    path: 'dashboard',
    loadComponent: () =>
      import('./pages/hr-dashboard/hr-dashboard').then(m => m.HrDashboard),
    canActivate: [hrAuthGuard],
  },
  {
    path: 'dashboard/candidate/:id',
    loadComponent: () =>
      import('./pages/candidate-detail/candidate-detail').then(m => m.CandidateDetail),
    canActivate: [hrAuthGuard],
  },
  {
    path: 'exam/:token',
    loadComponent: () =>
      import('./pages/exam-entry/exam-entry').then(m => m.ExamEntry),
  },
  {
    path: 'instructions',
    loadComponent: () =>
      import('./pages/instructions/instructions').then(m => m.Instructions),
  },
  {
    path: 'reading',
    loadComponent: () =>
      import('./pages/reading/reading').then(m => m.Reading),
  },
  {
    path: 'writing',
    loadComponent: () =>
      import('./pages/writing/writing').then(m => m.Writing),
  },
  {
    path: 'speaking',
    loadComponent: () =>
      import('./pages/speaking/speaking').then(m => m.Speaking),
  },
  {
    path: 'submitted',
    loadComponent: () =>
      import('./pages/submitted/submitted').then(m => m.Submitted),
  },
  {
    path: '**',
    redirectTo: 'login',
  },
];