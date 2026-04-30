/* =========================================================================
   login.js — HR sign-in form submission.
   On success, server sets a session cookie and we redirect to the dashboard.
   ========================================================================= */

// If already logged in, skip straight to the dashboard.
api('/api/hr/me').then(() => {
  window.location.href = 'hr-dashboard.html';
}).catch(() => {
  // 401 means not logged in — stay on this page.
});

document.getElementById('loginForm').addEventListener('submit', async function (e) {
  e.preventDefault();

  const email = document.getElementById('email').value.trim();
  const password = document.getElementById('password').value;
  const errorEl = document.getElementById('loginError');
  errorEl.textContent = '';

  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    errorEl.textContent = 'Enter a valid email address.';
    return;
  }
  if (!password) {
    errorEl.textContent = 'Password is required.';
    return;
  }

  const submitBtn = e.target.querySelector('button[type="submit"]');
  submitBtn.disabled = true;
  submitBtn.textContent = 'Signing in…';

  try {
    await api('/api/hr/login', { method: 'POST', body: { email, password } });
    window.location.href = 'hr-dashboard.html';
  } catch (err) {
    errorEl.textContent = err.message || 'Login failed.';
    submitBtn.disabled = false;
    submitBtn.textContent = 'SIGN IN  →';
  }
});
