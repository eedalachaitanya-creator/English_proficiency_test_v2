/* =========================================================================
   common.js — utilities used by every page.
   Loaded before any page-specific script.
   ========================================================================= */

// ---- sessionStorage helpers (used to cache candidate test content client-side) ----
const Store = {
  set(key, value) {
    sessionStorage.setItem(key, JSON.stringify(value));
  },
  get(key, fallback = null) {
    const raw = sessionStorage.getItem(key);
    if (raw === null) return fallback;
    try { return JSON.parse(raw); } catch { return fallback; }
  },
  remove(key) { sessionStorage.removeItem(key); },
  clear() { sessionStorage.clear(); }
};

// ---- API fetch wrapper ----
// Sends cookies (so the session middleware sees the HR/candidate cookie),
// auto-JSON-encodes body, throws on non-2xx with the server's error detail.
async function api(path, { method = 'GET', body = null, headers = {} } = {}) {
  const opts = {
    method,
    credentials: 'include',           // crucial — sends the session cookie
    headers: { ...headers },
  };
  if (body !== null && !(body instanceof FormData)) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  } else if (body instanceof FormData) {
    opts.body = body;                 // browser sets multipart Content-Type with boundary
  }

  const res = await fetch(path, opts);
  let data = null;
  const text = await res.text();
  if (text) {
    try { data = JSON.parse(text); } catch { data = text; }
  }
  if (!res.ok) {
    // FastAPI returns two error shapes:
    //   - 401/410/500/etc → { detail: "string message" }
    //   - 422 (validation) → { detail: [ { msg: "...", loc: [...], type: "..." }, ... ] }
    // Normalise both into a single readable string.
    let detail;
    if (data && Array.isArray(data.detail)) {
      detail = data.detail
        .map(e => {
          const field = Array.isArray(e.loc) ? e.loc[e.loc.length - 1] : '';
          return field ? `${field}: ${e.msg}` : e.msg;
        })
        .join('; ');
    } else if (data && typeof data.detail === 'string') {
      detail = data.detail;
    } else {
      detail = res.statusText || `HTTP ${res.status}`;
    }
    const err = new Error(detail);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

// ---- Styled in-app dialogs ----
// Replacement for browser's native confirm()/alert() that show "localhost:8000 says".
// Returns Promises so callers can use await.
const Modal = {
  /**
   * Show a Yes/No (or custom-labelled) confirm dialog. Resolves to true if user clicks OK.
   * Usage: const ok = await Modal.confirm('Submit your test?');
   * Options: { okText, cancelText, dangerous (uses red OK button), title }
   */
  confirm(message, opts = {}) {
    return new Promise((resolve) => {
      const overlay = document.createElement('div');
      overlay.className = 'modal-backdrop show';
      const okClass = opts.dangerous ? 'btn-danger' : 'btn-primary';
      overlay.innerHTML = `
        <div class="modal" style="max-width: 420px;">
          ${opts.title ? `<h2>${escapeHtmlText(opts.title)}</h2>` : ''}
          <p class="modal-message">${escapeHtmlText(message)}</p>
          <div class="flex-row">
            <button type="button" class="btn btn-secondary" data-action="cancel">${escapeHtmlText(opts.cancelText || 'Cancel')}</button>
            <span class="spacer"></span>
            <button type="button" class="btn ${okClass}" data-action="ok">${escapeHtmlText(opts.okText || 'OK')}</button>
          </div>
        </div>
      `;
      document.body.appendChild(overlay);
      // Cleanup must remove the keydown listener too — otherwise click/backdrop
      // dismissal leaks it and stale listeners pile up across modals.
      let onKey;
      const cleanup = (result) => {
        overlay.remove();
        document.removeEventListener('keydown', onKey);
        resolve(result);
      };
      onKey = e => {
        if (e.key === 'Escape') cleanup(false);
        else if (e.key === 'Enter') cleanup(true);
      };
      overlay.querySelector('[data-action="ok"]').addEventListener('click', () => cleanup(true));
      overlay.querySelector('[data-action="cancel"]').addEventListener('click', () => cleanup(false));
      overlay.addEventListener('click', e => { if (e.target === overlay) cleanup(false); });
      document.addEventListener('keydown', onKey);
    });
  },

  /**
   * Show a single-button alert dialog. Resolves when user clicks OK.
   * Usage: await Modal.alert('Microphone permission denied.');
   */
  alert(message, opts = {}) {
    return new Promise((resolve) => {
      const overlay = document.createElement('div');
      overlay.className = 'modal-backdrop show';
      overlay.innerHTML = `
        <div class="modal" style="max-width: 420px;">
          ${opts.title ? `<h2>${escapeHtmlText(opts.title)}</h2>` : ''}
          <p class="modal-message">${escapeHtmlText(message)}</p>
          <div class="flex-row">
            <span class="spacer"></span>
            <button type="button" class="btn btn-primary" data-action="ok">${escapeHtmlText(opts.okText || 'OK')}</button>
          </div>
        </div>
      `;
      document.body.appendChild(overlay);
      let onKey;
      const cleanup = () => {
        overlay.remove();
        document.removeEventListener('keydown', onKey);
        resolve();
      };
      onKey = e => {
        if (e.key === 'Escape' || e.key === 'Enter') cleanup();
      };
      overlay.querySelector('[data-action="ok"]').addEventListener('click', cleanup);
      document.addEventListener('keydown', onKey);
    });
  },
};

function escapeHtmlText(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  }[c]));
}

// ---- Format seconds as MM:SS ----
function formatTime(secs) {
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

// ---- Countdown timer factory ----
function startCountdown(seconds, onTick, onExpire) {
  let remaining = seconds;
  onTick(remaining);
  const intervalId = setInterval(() => {
    remaining -= 1;
    onTick(remaining);
    if (remaining <= 0) {
      clearInterval(intervalId);
      onExpire();
    }
  }, 1000);
  return { stop: () => clearInterval(intervalId) };
}

// ---- Guard: load test content for candidate pages, or render an inline error ----
// Used by instructions/reading/speaking pages. Caches result in sessionStorage
// so subsequent pages don't re-hit the server.
async function loadTestContent() {
  const cached = Store.get('testContent');
  if (cached) return cached;

  try {
    const data = await api('/api/test-content');
    Store.set('testContent', data);
    return data;
  } catch (err) {
    // 401 = no session, 410 = expired/submitted, 500 = no content seeded.
    // Replace the page body with a friendly explanation rather than redirecting
    // (no candidate-error page exists yet, and an alert+redirect loops on refresh).
    document.body.innerHTML = `
      <header class="topnav">
        <div class="brand">English Proficiency Test</div>
      </header>
      <main class="page-narrow">
        <div class="card text-center">
          <h1>Could not load your test</h1>
          <p class="subtitle">${err.message || 'Unknown error.'}</p>
          <p class="text-muted">If you received an exam URL by email, please re-open it.
          If the link has expired or already been used, contact your HR manager.</p>
        </div>
      </main>
    `;
    return null;
  }
}
