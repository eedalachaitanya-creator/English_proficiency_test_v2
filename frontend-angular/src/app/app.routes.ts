import { Routes } from '@angular/router';
import { hrAuthGuard } from './core/guards/hr-auth.guard';

/**
 * Wired routes:
 *   /                                  → /login
 *   /login                             → Login (HR)
 *   /dashboard                         → HrDashboard
 *   /dashboard/candidate/:id           → CandidateDetail
 *   /dashboard/content                 → ContentHub (HR question authoring landing)
 *   /dashboard/content/questions       → ContentQuestions (Phase 3)
 *   /dashboard/content/passages        → (Phase 4 — coming next)
 *   /dashboard/content/writing-topics  → (Phase 5)
 *   /dashboard/content/speaking-topics → (Phase 6)
 *   /exam/:token                       → ExamEntry (passcode)
 *   /instructions, /reading, /writing, /speaking, /submitted → candidate flow
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
    path: 'dashboard/content',
    loadComponent: () =>
      import('./pages/content-hub/content-hub').then(m => m.ContentHub),
    canActivate: [hrAuthGuard],
  },
  {
    path: 'dashboard/content/questions',
    loadComponent: () =>
      import('./pages/content-questions/content-questions').then(m => m.ContentQuestions),
    canActivate: [hrAuthGuard],
  },
  {
    path: 'dashboard/content/passages',
    loadComponent: () =>
      import('./pages/content-passages/content-passages').then(m => m.ContentPassages),
    canActivate: [hrAuthGuard],
  },
  {
    path: 'dashboard/content/writing-topics',
    loadComponent: () =>
      import('./pages/content-writing-topics/content-writing-topics').then(m => m.ContentWritingTopics),
    canActivate: [hrAuthGuard],
  },
  {
    path: 'dashboard/content/speaking-topics',
    loadComponent: () =>
      import('./pages/content-speaking-topics/content-speaking-topics').then(m => m.ContentSpeakingTopics),
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