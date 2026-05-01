import { Component, Input } from '@angular/core';

/**
 * Footer strip — appears at the bottom of every page.
 *
 * Single configurable input (`text`) so each page can show its own
 * footer text without duplicating the markup. Examples from the old
 * frontend:
 *
 *   index.html        → "© Company HR • Privacy • Support"
 *   reading.html      → "Auto-saves on selection • Do not refresh"
 *   writing.html      → "Auto-saves on every keystroke • Do not refresh"
 *   speaking.html     → "Mic permission required • Do not refresh"
 *   submitted.html    → "© Company HR • Privacy • Support"
 *   hr-dashboard.html → "© Company HR • Scores written by Claude (LLM rubric scoring)"
 *
 * Default value matches the most common case.
 *
 * Usage:
 *   <app-footer></app-footer>
 *   <app-footer text="Auto-saves on every keystroke • Do not refresh"></app-footer>
 */
@Component({
  selector: 'app-footer',
  standalone: true,
  imports: [],
  templateUrl: './footer.html',
  styleUrl: './footer.css',
})
export class Footer {
  @Input() text = '© Company HR  •  Privacy  •  Support';
}