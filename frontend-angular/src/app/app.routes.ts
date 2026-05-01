import { Routes } from '@angular/router';
import { hrAuthGuard } from './core/guards/hr-auth.guard';

/**
 * Route table for the entire app.
 *
 * Built so far:
 *   /                          → redirects to /login
 *   /login                     → Login (HR sign-in)
 *   /dashboard                 → HrDashboard (HR-guarded)
 *   /dashboard/candidate/:id   → CandidateDetail (HR-guarded)
 *   /exam/:token               → ExamEntry (passcode entry, public)
 *   /instructions              → Instructions (Step 6 — candidate-side)
 *
 * Coming in later steps:
 *   /reading       → section 1
 *   /writing       → section 2
 *   /speaking      → section 3
 *   /submitted     → confirmation page
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
    path: '**',
    redirectTo: 'login',
  },
];