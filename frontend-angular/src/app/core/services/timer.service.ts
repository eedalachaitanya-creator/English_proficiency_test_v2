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
   * Start a countdown that ticks roughly once per second.
   *
   * Internally a thin wrapper around startFromDeadline — both forms use
   * the same wall-clock-aware tick loop so they behave identically when
   * the tab is hidden.
   *
   * @param totalSeconds Initial duration. The first onTick fires immediately
   *                     with this value, then once per second.
   * @param onTick      Called with the remaining seconds. Each tick reads
   *                    the wall clock; if the browser throttles setInterval
   *                    while the tab is hidden, the next tick still shows
   *                    the correct remaining time (no "free" time accrues).
   * @param onExpire    Called once when the countdown reaches 0.
   *                    NOT called if stop() is invoked before expiry.
   */
  start(
    totalSeconds: number,
    onTick: (remaining: number) => void,
    onExpire: () => void
  ): TimerHandle {
    const deadlineMs = Date.now() + Math.max(0, Math.floor(totalSeconds)) * 1000;
    return this.startFromDeadline(deadlineMs, onTick, onExpire);
  }

  /**
   * Start a countdown that targets an absolute deadline (ms since epoch).
   *
   * This is the "do-not-reset-on-navigation" mode used by the test pages.
   * The deadline lives in sessionStorage; on every page entry we compute
   * remaining from the deadline and start a timer.
   *
   *   const ms = Date.now() + 30 * 60 * 1000;        // 30 min from now
   *   store.setReadingDeadline(ms);
   *   timer.startFromDeadline(ms, onTick, onExpire);
   *
   * Wall-clock-aware: every tick recomputes `(deadline - Date.now()) / 1000`
   * rather than decrementing a counter. This means even if the browser
   * throttles setInterval in a hidden tab (Chrome's intensive throttling
   * fires only once per minute after 5 min of being hidden), the tick that
   * does fire shows the correct remaining time. Switching tabs no longer
   * gives the candidate "free" exam time. The visibilitychange listener
   * also forces an immediate tick when the tab becomes visible again so
   * the displayed value snaps to the correct wall-clock value instead of
   * lagging until the next setInterval fires.
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
    let stopped = false;
    let expired = false;

    const computeRemaining = (): number =>
      Math.max(0, Math.floor((deadlineMs - Date.now()) / 1000));

    const tick = (): void => {
      if (stopped || expired) return;
      const remaining = computeRemaining();
      onTick(remaining);
      if (remaining <= 0) {
        expired = true;
        cleanup();
        onExpire();
      }
    };

    // Fire the first tick immediately so the timer label doesn't show
    // a stale value for the first second.
    onTick(computeRemaining());

    if (computeRemaining() <= 0) {
      expired = true;
      onExpire();
      return { stop: () => { stopped = true; } };
    }

    const intervalId = setInterval(tick, 1000);

    // When the tab returns to the foreground, force an immediate tick so
    // the displayed value reflects the wall clock — without this, the
    // user sees the throttled-stale value for up to ~1 minute (until the
    // throttled setInterval next fires).
    const onVisibilityChange = (): void => {
      if (document.visibilityState === 'visible') tick();
    };
    document.addEventListener('visibilitychange', onVisibilityChange);

    const cleanup = (): void => {
      clearInterval(intervalId);
      document.removeEventListener('visibilitychange', onVisibilityChange);
    };

    return {
      stop: () => {
        if (stopped) return;
        stopped = true;
        cleanup();
      },
    };
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