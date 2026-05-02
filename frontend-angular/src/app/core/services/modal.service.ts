import {
  Injectable,
  ApplicationRef,
  ComponentRef,
  EnvironmentInjector,
  createComponent,
} from '@angular/core';
import { ModalDialog } from '../../shared/components/modal-dialog/modal-dialog';

/**
 * Options accepted by both confirm() and alert(). All are optional.
 */
export interface ModalOptions {
  /** Title shown in bold above the message. Optional. */
  title?: string;
  /** Text on the primary (right-side) button. Default: "OK". */
  okText?: string;
  /** Text on the secondary (left-side) button. Default: "Cancel". Ignored by alert(). */
  cancelText?: string;
  /**
   * If true, the OK button uses the red "danger" style instead of the orange
   * primary style. Used for destructive confirmations like "Submit final test"
   * or "Discard recordings".
   */
  dangerous?: boolean;
}

/**
 * In-app dialog service. Drop-in replacement for the Modal object in the old
 * common.js — same Promise-based API, same UX:
 *
 *   const ok = await modal.confirm('Submit your test?');
 *   await modal.alert('Microphone permission denied.');
 *
 * Both methods:
 *   - Render a styled overlay (uses the .modal-backdrop / .modal CSS in
 *     styles.css — already pasted in File 1).
 *   - Block keyboard focus until dismissed.
 *   - Resolve true/false (confirm) or void (alert) when the user clicks
 *     OK/Cancel, presses Esc/Enter, or clicks the backdrop.
 *
 * Implementation: dynamically creates a ModalDialog component, attaches it
 * to the application root, and destroys it once the user responds. This is
 * the Angular-idiomatic way to do a global dialog without needing a
 * <modal-dialog> tag in every component template.
 */
@Injectable({ providedIn: 'root' })
export class ModalService {
  constructor(
    private appRef: ApplicationRef,
    private envInjector: EnvironmentInjector
  ) {}

  /**
   * Show a confirmation dialog with OK + Cancel buttons.
   *
   * Resolves:
   *   true  — user clicked OK / pressed Enter
   *   false — user clicked Cancel / pressed Esc / clicked the backdrop
   */
  confirm(message: string, opts: ModalOptions = {}): Promise<boolean> {
    return this.show({
      message,
      title: opts.title,
      okText: opts.okText ?? 'OK',
      cancelText: opts.cancelText ?? 'Cancel',
      dangerous: opts.dangerous ?? false,
      isAlert: false,
    });
  }

  /**
   * Show an alert dialog with a single OK button.
   *
   * Resolves once the user dismisses (click OK, press Enter/Esc, click
   * backdrop). Resolves with `true` for symmetry with confirm() but the
   * value is meaningless — the caller typically does `await modal.alert(...)`
   * without inspecting the return.
   */
  alert(message: string, opts: ModalOptions = {}): Promise<boolean> {
    return this.show({
      message,
      title: opts.title,
      okText: opts.okText ?? 'OK',
      cancelText: '',
      dangerous: false,
      isAlert: true,
    });
  }

  // ------------------------------------------------------------------
  //  Internal — creates and mounts the ModalDialog component, then
  //  cleans up after the user responds.
  // ------------------------------------------------------------------

  private show(config: {
    message: string;
    title?: string;
    okText: string;
    cancelText: string;
    dangerous: boolean;
    isAlert: boolean;
  }): Promise<boolean> {
    return new Promise<boolean>((resolve) => {
      // Create the component dynamically and attach it to the DOM.
      const componentRef: ComponentRef<ModalDialog> = createComponent(ModalDialog, {
        environmentInjector: this.envInjector,
      });

      // Pass our config in via the component's @Input properties.
      componentRef.setInput('message', config.message);
      componentRef.setInput('title', config.title ?? '');
      componentRef.setInput('okText', config.okText);
      componentRef.setInput('cancelText', config.cancelText);
      componentRef.setInput('dangerous', config.dangerous);
      componentRef.setInput('isAlert', config.isAlert);

      // Wire up the component's "closed" event — this fires with true/false
      // and we resolve the Promise + tear the component down.
      const sub = componentRef.instance.closed.subscribe((result: boolean) => {
        sub.unsubscribe();
        // Cleanup order matters here. componentRef.destroy() already removes
        // the DOM element AND detaches the change-detection view internally.
        // We previously called document.body.removeChild() AFTER destroy(),
        // which threw NotFoundError ("not a child of this node") because the
        // node was already gone. The error wasn't visible most of the time
        // because callers didn't do anything async after `await modal.confirm`,
        // but it broke any flow that did — the Promise never resolved.
        //
        // The fix: just call destroy(). It handles everything we need.
        this.appRef.detachView(componentRef.hostView);
        componentRef.destroy();
        resolve(result);
      });

      // Mount into Angular's change-detection tree so events fire correctly.
      this.appRef.attachView(componentRef.hostView);
      // Append the component's root DOM element to <body> so it sits above
      // every page (CSS z-index: 1000 already in styles.css).
      document.body.appendChild(componentRef.location.nativeElement);
    });
  }
}