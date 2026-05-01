/* =========================================================================
   exam-code.js — handles the 6-digit access code entry.
   The token comes from ?token=... in the URL (set by /exam/{token} redirect).
   On correct code, the server sets a session cookie and we go to instructions.
   ========================================================================= */

(function () {
  const params = new URLSearchParams(window.location.search);
  const token = params.get('token');

  // No token in URL = someone hit /exam-code.html directly without a real
  // invitation. Show a friendly error instead of letting them mash the form.
  if (!token) {
    document.body.innerHTML = `
      <header class="topnav"><div class="brand">English Proficiency Test</div></header>
      <main class="page-narrow">
        <div class="card text-center">
          <h1>No invitation link</h1>
          <p class="subtitle">This page is reached via your invitation URL.
          If you received an exam URL by email, please re-open it.</p>
        </div>
      </main>
    `;
    return;
  }

  const form = document.getElementById('codeForm');
  const input = document.getElementById('codeInput');
  const submitBtn = document.getElementById('submitBtn');
  const errorMsg = document.getElementById('errorMsg');

  // Restrict input to digits only and enable submit only at exactly 6 digits.
  // We do this on input (not just submit) so the button visually communicates
  // when the form is ready.
  input.addEventListener('input', () => {
    // Strip anything non-numeric in case of paste from email signatures etc.
    const cleaned = input.value.replace(/\D/g, '').slice(0, 6);
    if (cleaned !== input.value) input.value = cleaned;
    submitBtn.disabled = cleaned.length !== 6;
    input.classList.remove('error');
    errorMsg.textContent = '';
  });

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const code = input.value.trim();
    if (code.length !== 6) return;

    submitBtn.disabled = true;
    submitBtn.textContent = 'Verifying...';
    errorMsg.textContent = '';

    try {
      const res = await api('/api/exam/verify-code', {
        method: 'POST',
        body: { token, code },
      });
      if (res.success && res.redirect_to) {
        // Server set the session cookie. Off to the test.
        window.location.href = res.redirect_to;
      } else {
        // Shouldn't happen — server returns success=true OR throws — but
        // be defensive.
        errorMsg.textContent = res.detail || 'Verification failed.';
        input.classList.add('error');
      }
    } catch (err) {
      // common.js api() throws on non-2xx with err.message = server detail.
      // 401 = wrong code (with attempts_remaining in the message).
      // 423 = locked (after MAX_CODE_ATTEMPTS or the link is fully locked).
      // 410 = expired/submitted.
      // 404 = bad token (rare — would only happen if URL is mangled).
      input.classList.add('error');
      input.value = '';
      errorMsg.textContent = err.message || 'Could not verify code.';

      // Hard-lock states (423, 410) — disable the form so the candidate can't
      // keep banging on it. 404 same — token is junk.
      if (err.status === 423 || err.status === 410 || err.status === 404) {
        submitBtn.disabled = true;
        submitBtn.textContent = 'TEST UNAVAILABLE';
        input.disabled = true;
        return;
      }

      // 401 = wrong code with attempts left — let them retry.
      submitBtn.disabled = false;
      submitBtn.textContent = 'VERIFY & START TEST  →';
      input.focus();
    }
  });
})();