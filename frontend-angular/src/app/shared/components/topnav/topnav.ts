import { Component, EventEmitter, Input, Output } from '@angular/core';
import { CommonModule } from '@angular/common';

/**
 * Top navigation bar — appears on every page.
 *
 * Supports three variants by toggling inputs:
 *
 *   1. CANDIDATE-FACING (default)
 *      - title       = "English Proficiency Test"
 *      - meta        = "{candidate_name} | {difficulty}" (or "Loading…")
 *      - variant     = '' (default — flat navy background)
 *
 *   2. HR DASHBOARD
 *      - title       = "HR Portal — English Proficiency Results"
 *      - meta        = "{hrEmail}"
 *      - variant     = 'hr' (gradient navy → navy-light)
 *      - showLogout  = true (renders the Logout link)
 *
 *   3. PUBLIC (login, submitted)
 *      - title       = "English Proficiency Test"
 *      - meta        = "Internal use only" or "Submission complete"
 *      - variant     = '' or 'hr' depending on the page
 *
 * The HR dashboard listens for the (logout) event and calls AuthService
 * to clear the session, then navigates to /login.
 *
 * Usage in a template:
 *
 *   <app-topnav
 *     [title]="'HR Portal — English Proficiency Results'"
 *     [meta]="auth.currentUser()?.email || 'Loading…'"
 *     variant="hr"
 *     [showLogout]="true"
 *     (logout)="onLogout()">
 *   </app-topnav>
 */
@Component({
  selector: 'app-topnav',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './topnav.html',
  styleUrl: './topnav.css',
})
export class Topnav {
  /** Brand text on the left side. */
  @Input() title = 'English Proficiency Test';

  /** Meta text on the right side (candidate name, HR email, or status). */
  @Input() meta = '';

  /** '' for the default flat navy bar; 'hr' for the gradient HR variant. */
  @Input() variant: '' | 'hr' = '';

  /** Show the "Logout" link to the right of the meta text. */
  @Input() showLogout = false;

  /** Fired when the Logout link is clicked. Parent handles the actual logout. */
  @Output() logout = new EventEmitter<void>();

  onLogoutClick(event: Event): void {
    // Prevent the <a href="#"> default of jumping to the top of the page.
    event.preventDefault();
    this.logout.emit();
  }
}