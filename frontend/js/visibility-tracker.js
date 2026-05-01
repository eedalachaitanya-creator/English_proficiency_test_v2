/* =========================================================================
   visibility-tracker.js — track tab/window switches via Page Visibility API.

   Loaded on reading.html, writing.html, and speaking.html.

   THREE-STRIKE POLICY:
     - Strike 1: First warning shown (informational)
     - Strike 2: "Final warning" — explicit notice that one more ends the test
     - Strike 3: No alert. Dispatches `visibility:terminate` event so the
       page can auto-submit whatever data exists and redirect.

   What counts as a "strike":
     - Tab/window switched away for >= 2 seconds
     - Brief absences (notifications, accidental clicks) are ignored
     - Multi-second alt-tab, minimize, or switching apps all count

   What this does NOT do:
     - Pause the test timer (cheaters would love that — it stays running)
     - Distinguish "tab switch" from "alt-tab" from "minimize"
     - Catch side-by-side ChatGPT cheating (no focus change to detect)

   How other scripts use this:
     Tracker.getStats() — returns { count, totalSeconds } for FormData
     Tracker.reset() — clears everything (call after successful submit)
     'visibility:terminate' event on document — fires once when count hits 3
   ========================================================================= */

(function () {
  // Brief absences below this threshold are ignored. 2 seconds is short
  // enough to catch a real cheating attempt (looking at ChatGPT) but long
  // enough to ignore a fleeting Slack notification or accidental click.
  const MIN_SWITCH_SECONDS = 2;

  // Three strikes and the test ends. After the 3rd switch, no warning is
  // shown — we just terminate. Reasoning: a 3rd warning would feel like
  // bargaining ("ok one more chance") which contradicts the policy.
  const MAX_STRIKES = 3;

  const STORAGE_KEY = 'visibilityStats';

  // Once we've fired the terminate event, stop tracking. Without this guard
  // a 4th switch during the auto-submit network round-trip would cause a
  // second termination event and a duplicate POST. Belt-and-suspenders.
  let terminated = false;

  function loadStats() {
    try {
      const raw = sessionStorage.getItem(STORAGE_KEY);
      if (!raw) return { count: 0, totalSeconds: 0 };
      const parsed = JSON.parse(raw);
      return {
        count: Number.isFinite(parsed.count) ? parsed.count : 0,
        totalSeconds: Number.isFinite(parsed.totalSeconds) ? parsed.totalSeconds : 0,
      };
    } catch {
      return { count: 0, totalSeconds: 0 };
    }
  }

  function saveStats(stats) {
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(stats));
    } catch {
      // sessionStorage full or disabled — silently ignore.
    }
  }

  let stats = loadStats();
  let hiddenSince = null;

  // -- Warning messages ----------------------------------------------------
  // Strike 1: informational, friendly tone, but clearly stating that this
  // is being logged and visible to HR.
  function showFirstWarning(durationSeconds) {
    if (typeof Modal === 'undefined' || !Modal.alert) {
      console.warn(`[visibility] first warning, ${durationSeconds}s`);
      return;
    }
    const message =
      `We detected that you switched away from this tab for ${durationSeconds} second${durationSeconds !== 1 ? 's' : ''}.\n\n` +
      `This incident has been logged and will be visible to your HR reviewer. ` +
      `Please keep this tab focused for the remainder of the test.`;
    Modal.alert(message, { title: 'Tab switch detected' });
  }

  // Strike 2: explicitly says one more switch ends the test. No ambiguity.
  function showFinalWarning(durationSeconds) {
    if (typeof Modal === 'undefined' || !Modal.alert) {
      console.warn(`[visibility] FINAL warning, ${durationSeconds}s`);
      return;
    }
    const message =
      `Final warning.\n\n` +
      `You have switched away ${stats.count} times. ` +
      `One more switch will automatically end your test and submit ` +
      `whatever data you've completed so far.\n\n` +
      `Please keep this tab focused.`;
    Modal.alert(message, { title: '⚠ FINAL WARNING' });
  }

  // -- Visibility change handler -----------------------------------------
  document.addEventListener('visibilitychange', () => {
    if (terminated) return;

    if (document.hidden) {
      hiddenSince = Date.now();
      return;
    }

    // Tab is visible again
    if (hiddenSince === null) return;
    const elapsedMs = Date.now() - hiddenSince;
    hiddenSince = null;
    const elapsedSec = Math.round(elapsedMs / 1000);
    if (elapsedSec < MIN_SWITCH_SECONDS) return;

    stats.count += 1;
    stats.totalSeconds += elapsedSec;
    saveStats(stats);

    // Decide what to do based on strike number
    if (stats.count >= MAX_STRIKES) {
      // Terminate. Page-specific handler will collect data, build FormData,
      // POST it, and redirect. We don't show a modal — the page handler
      // shows a "test ended" notice via its own UI.
      terminated = true;
      console.warn(`[visibility] TERMINATE — strike ${stats.count}`);
      document.dispatchEvent(new CustomEvent('visibility:terminate', {
        detail: { count: stats.count, totalSeconds: stats.totalSeconds },
      }));
      return;
    }

    if (stats.count === MAX_STRIKES - 1) {
      showFinalWarning(elapsedSec);
    } else {
      showFirstWarning(elapsedSec);
    }
  });

  // Edge case: page navigated/closed while tab was hidden. Flush the time.
  window.addEventListener('pagehide', () => {
    if (hiddenSince !== null) {
      const elapsedSec = Math.round((Date.now() - hiddenSince) / 1000);
      if (elapsedSec >= MIN_SWITCH_SECONDS) {
        stats.count += 1;
        stats.totalSeconds += elapsedSec;
        saveStats(stats);
      }
      hiddenSince = null;
    }
  });

  // -- Public API --------------------------------------------------------
  window.Tracker = {
    /** Returns the current { count, totalSeconds }. */
    getStats() { return loadStats(); },
    /** Clear all tracking data. Called on successful submit. */
    reset() {
      stats = { count: 0, totalSeconds: 0 };
      hiddenSince = null;
      terminated = false;
      sessionStorage.removeItem(STORAGE_KEY);
    },
    /** Internal config exposed for tests/debugging. Don't rely on this. */
    _maxStrikes: MAX_STRIKES,
  };
})();