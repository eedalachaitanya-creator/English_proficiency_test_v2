/* =========================================================================
   login.js — handles the login form.
   For now there is no real backend, so we accept any non-empty token of
   length >= 6. When the backend is built, swap the validation block
   for a fetch() call to /api/login.
   ========================================================================= */

document.getElementById('loginForm').addEventListener('submit', function (e) {
  e.preventDefault();

  const email = document.getElementById('email').value.trim();
  const token = document.getElementById('token').value.trim();
  const errorEl = document.getElementById('loginError');
  errorEl.textContent = '';

  // Basic validation (front-end only)
  const emailValid = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
  if (!emailValid) {
    errorEl.textContent = 'Please enter a valid email address.';
    return;
  }
  if (token.length < 6) {
    errorEl.textContent = 'Access token must be at least 6 characters.';
    return;
  }

  // ---- BACKEND HOOK ----
  // Replace this block with: fetch('/api/login', { method:'POST', body: JSON.stringify({email, token}) })
  // For now we just accept the token and move on.
  const candidate = {
    email,
    token,
    name: email.split('@')[0].replace(/[._]/g, ' ').replace(/\b\w/g, c => c.toUpperCase()),
    startedAt: new Date().toISOString(),
  };
  Store.set('candidate', candidate);

  window.location.href = 'instructions.html';
});
