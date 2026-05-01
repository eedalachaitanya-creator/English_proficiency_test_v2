/* =========================================================================
   force-submit.js — auto-submit partial test data when the candidate is
   terminated for tab-switching during reading or writing pages.

   Why a separate file:
     The submit logic is needed by reading.js, writing.js, AND speaking.js.
     Speaking has audio blobs in memory and uses its own submit path, but
     reading and writing only have what's in sessionStorage. Centralizing
     "build FormData from storage and POST" prevents drift.

   What gets submitted:
     - reading answers (from Store.get('readingAnswers'))
     - writing essay text (from Store.get('writingEssay'))
     - tab_switches stats (from Tracker)
     - topic_ids: [] and no audio files — since termination happened before
       the speaking section completed, there are no recordings to send.
       The backend's submit endpoint handles missing audio gracefully.

   Behavior:
     ForceSubmit.terminateAndSubmit() — call from a `visibility:terminate`
     event handler. Disables the page, posts to /api/submit, redirects.

   This is fire-and-forget — once called, the candidate cannot recover.
   ========================================================================= */

(function () {

  // Block all interaction with the page during the submit. We replace the
  // whole body with a "test ended" overlay so the candidate can't keep
  // typing/clicking while the network call is in flight.
  function showTerminatedOverlay() {
    const overlay = document.createElement('div');
    overlay.id = 'terminationOverlay';
    overlay.style.cssText = `
      position: fixed; inset: 0; z-index: 9999;
      background: rgba(11, 37, 69, 0.97); color: #fff;
      display: flex; flex-direction: column;
      align-items: center; justify-content: center;
      font-family: Arial, "Helvetica Neue", Helvetica, sans-serif;
      padding: 24px; text-align: center;
    `;
    overlay.innerHTML = `
      <div style="font-size: 64px; margin-bottom: 16px;">⏹</div>
      <h1 style="font-size: 28px; margin-bottom: 12px;">Test Ended</h1>
      <p style="font-size: 16px; max-width: 480px; line-height: 1.5; margin-bottom: 24px;">
        Your test has been terminated due to repeated tab switches.
        We are submitting the data you completed so far.
      </p>
      <div id="termSpinnerMsg" style="font-size: 14px; opacity: 0.85;">
        Submitting…
      </div>
    `;
    document.body.appendChild(overlay);
  }

  function setOverlayMessage(text) {
    const el = document.getElementById('termSpinnerMsg');
    if (el) el.textContent = text;
  }

  /**
   * Build FormData from sessionStorage + Tracker, POST to /api/submit,
   * redirect to submitted page. No audio blobs (those only exist on the
   * speaking page; if the candidate got terminated on reading/writing,
   * there's nothing to record).
   */
  async function terminateAndSubmit() {
    showTerminatedOverlay();

    const fd = new FormData();
    // Reading answers — defaults to {} if the candidate never opened the page.
    fd.append('answers', JSON.stringify(Store.get('readingAnswers', {})));
    // No topic IDs — speaking section was never started or completed.
    fd.append('topic_ids', JSON.stringify([]));
    // Writing essay — defaults to '' if they never started writing.
    fd.append('essay_text', Store.get('writingEssay', '') || '');

    // Tab-switching telemetry. Tracker is loaded on every test page so this
    // should always be defined, but defensive check in case of load order issues.
    const stats = (typeof Tracker !== 'undefined' && Tracker.getStats)
      ? Tracker.getStats()
      : { count: 0, totalSeconds: 0 };
    fd.append('tab_switches_count', String(stats.count));
    fd.append('tab_switches_total_seconds', String(stats.totalSeconds));

    try {
      const res = await api('/api/submit', { method: 'POST', body: fd });
      Store.set('refId', res.ref_id);
      // Clear all test state so a future retry attempt has a clean slate.
      Store.remove('testContent');
      Store.remove('readingAnswers');
      Store.remove('writingEssay');
      Store.remove('readingDeadline');
      Store.remove('writingDeadline');
      Store.remove('readingTimeUp');
      Store.remove('writingTimeUp');
      if (typeof Tracker !== 'undefined' && Tracker.reset) Tracker.reset();
      window.location.href = 'submitted.html';
    } catch (err) {
      // Submission failed (server error, network, expired link). Still
      // redirect to submitted page — we already told the candidate the
      // test is over, and the candidate can't recover from here. The
      // backend log will show the error for HR to investigate.
      console.error('[force-submit] submission failed:', err);
      setOverlayMessage(
        'Submission could not be completed. Please contact your HR manager.'
      );
      // Do not redirect — leave them on this overlay so they don't think
      // they submitted successfully. The error is final.
    }
  }

  // Public API. Pages call this from their `visibility:terminate` handler.
  window.ForceSubmit = {
    terminateAndSubmit,
  };
})();