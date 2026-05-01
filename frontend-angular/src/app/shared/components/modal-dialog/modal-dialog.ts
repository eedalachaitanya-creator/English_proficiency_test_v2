import { Component, EventEmitter, HostListener, Input, Output, OnInit, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';

/**
 * Visual component for confirm/alert dialogs.
 *
 * NOT meant to be used directly via <modal-dialog> in templates. ModalService
 * creates instances of this dynamically via createComponent() and tears them
 * down on close. See modal.service.ts for the public API.
 *
 * Inputs:
 *   message    — the body text shown to the user
 *   title      — optional bold heading above the message ('' to hide)
 *   okText     — primary button label (default "OK")
 *   cancelText — secondary button label, '' for alert mode (no Cancel button)
 *   dangerous  — when true, OK button uses red .btn-danger style
 *   isAlert    — when true, only the OK button shows (used by alert())
 *
 * Output:
 *   closed (boolean) — fires once with true (OK/Enter) or false (Cancel/Esc/
 *                      backdrop click). Component should be torn down after.
 */
@Component({
  selector: 'app-modal-dialog',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './modal-dialog.html',
  styleUrl: './modal-dialog.css',
})
export class ModalDialog implements OnInit, OnDestroy {
  @Input() message = '';
  @Input() title = '';
  @Input() okText = 'OK';
  @Input() cancelText = 'Cancel';
  @Input() dangerous = false;
  @Input() isAlert = false;

  @Output() closed = new EventEmitter<boolean>();

  private resolved = false;

  ngOnInit(): void {
    // Lock body scroll while modal is open — matches the old behaviour where
    // the backdrop covered the page and the user couldn't scroll under it.
    document.body.style.overflow = 'hidden';
  }

  ngOnDestroy(): void {
    document.body.style.overflow = '';
  }

  /** OK button — resolves with true. */
  onOk(): void {
    this.close(true);
  }

  /** Cancel button — resolves with false. */
  onCancel(): void {
    this.close(false);
  }

  /** Backdrop click — counts as Cancel. */
  onBackdropClick(event: MouseEvent): void {
    // Only dismiss if the click was on the backdrop itself, not on the modal
    // content. Without this check, clicking inside the dialog would also close.
    if (event.target === event.currentTarget) {
      this.close(false);
    }
  }

  /**
   * Keyboard shortcuts — bound at the document level so they work regardless
   * of where focus is. HostListener handles the cleanup automatically when
   * the component is destroyed.
   */
  @HostListener('document:keydown.escape')
  onEscape(): void {
    this.close(false);
  }

  @HostListener('document:keydown.enter')
  onEnter(): void {
    this.close(true);
  }

  private close(result: boolean): void {
    if (this.resolved) return; // guard against double-fire (e.g., Enter + click)
    this.resolved = true;
    this.closed.emit(result);
  }
}