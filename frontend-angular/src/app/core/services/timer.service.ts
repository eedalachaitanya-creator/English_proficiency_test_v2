import { Injectable } from '@angular/core';

/**
 * A running countdown handle. Returned by TimerService.start() so callers
 * can stop the timer programmatically (e.g., when the user clicks Next
 * before time expires).
 */
export interface TimerHandle {
  /** Stop the countdown. Idempotent — safe to call multiple times. */
  stop(): void;
}

/**
 * Countdown timer utilities. Mirrors the helpers from the old common.js:
 *
 *   formatTime(secs)   → "MM:SS" string for display
 *   startCountdown(...) → tick-once-per-second timer with onExpire callback
 *
 * The only Angular-specific addition is that startFromDeadline() is built
 * around an absolute deadline timestamp rather than a relative duration.
 * That matters because reading.html, writing.html, and speaking.html all
 * persist their deadline in sessionStorage so navigating away and back
 * does NOT reset the clock — matching the old "going back will not stop
 * the timer" UX promise.
 */
@Injectable({ providedIn: 'root' })
export class TimerService {

  /**
   * Format a duration in seconds as "MM:SS".
   *
   * Pads both fields with leading zeros so the width is consistent — the
   * orange .timer pill in the section bar would otherwise jump width as
   * digits change, which is visually distracting.
   *
   *   formatTime(0)    → "00:00"
   *   formatTime(65)   → "01:05"
   *   formatTime(3599) → "59:59"
   */
  formatTime(seconds: number): string {
    if (seconds < 0) seconds = 0;
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  }

  /**
   * Start a countdown that ticks once per second.
   *
   * @param totalSeconds Initial duration. The first onTick fires immediately
   *                     with this value, then once per second.
   * @param onTick      Called every second with the remaining seconds. Use
   *                    this to update the timer DOM and toggle warning/danger
   *                    classes. Component should manage its own NgZone if
   *                    UI updates need to trigger change detection — Angular's
   *                    setInterval does this automatically.
   * @param onExpire    Called once when the countdown reaches 0.
   *                    NOT called if stop() is invoked before expiry.
   */
  start(
    totalSeconds: number,
    onTick: (remaining: number) => void,
    onExpire: () => void
  ): TimerHandle {
    let remaining = Math.max(0, Math.floor(totalSeconds));
    let stopped = false;

    // Fire the first tick immediately so the timer label doesn't show
    // a stale value for the first second.
    onTick(remaining);

    if (remaining <= 0) {
      onExpire();
      return { stop: () => { stopped = true; } };
    }

    const intervalId = setInterval(() => {
      if (stopped) return;
      remaining -= 1;
      onTick(remaining);
      if (remaining <= 0) {
        clearInterval(intervalId);
        stopped = true;
        onExpire();
      }
    }, 1000);

    return {
      stop: () => {
        if (stopped) return;
        stopped = true;
        clearInterval(intervalId);
      },
    };
  }

  /**
   * Start a countdown that targets an absolute deadline (ms since epoch).
   *
   * This is the "do-not-reset-on-navigation" mode used by the test pages.
   * The deadline lives in sessionStorage; on every page entry we compute
   * `Math.floor((deadline - Date.now()) / 1000)` and start a timer for
   * exactly that long.
   *
   *   const ms = Date.now() + 30 * 60 * 1000;        // 30 min from now
   *   store.setReadingDeadline(ms);
   *   timer.startFromDeadline(ms, onTick, onExpire);
   *
   * If the deadline has already passed (e.g., candidate left the tab open
   * for too long), onExpire is called immediately and the returned handle
   * is a no-op. Callers should still navigate to the next section.
   */
  startFromDeadline(
    deadlineMs: number,
    onTick: (remaining: number) => void,
    onExpire: () => void
  ): TimerHandle {
    const remaining = Math.max(0, Math.floor((deadlineMs - Date.now()) / 1000));
    return this.start(remaining, onTick, onExpire);
  }

  /**
   * CSS class for the .timer pill based on remaining time. Matches the
   * thresholds used in the old reading.js / writing.js / speaking.js:
   *
   *   > 60 seconds  → ''        (orange — calm)
   *   16-60 seconds → 'warning' (yellow with slow pulse)
   *   ≤ 15 seconds  → 'danger'  (red with fast pulse)
   *
   * Timer thresholds for the speaking section's per-question countdown
   * are slightly different (30s warning / 10s danger) — that page passes
   * custom thresholds via the optional argument.
   */
  thresholdClass(
    remainingSeconds: number,
    warningAt = 60,
    dangerAt = 15
  ): 'warning' | 'danger' | '' {
    if (remainingSeconds <= dangerAt) return 'danger';
    if (remainingSeconds <= warningAt) return 'warning';
    return '';
  }
}