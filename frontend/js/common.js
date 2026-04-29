/* =========================================================================
   common.js — utilities used by every page.
   Loaded before any page-specific script.
   ========================================================================= */

// ---- sessionStorage helpers (so the candidate's state survives page nav) ----
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

// ---- Reference ID generator (used on submission) ----
function generateRefId(candidateName) {
  const date = new Date();
  const yyyy = date.getFullYear();
  const mm = String(date.getMonth() + 1).padStart(2, '0');
  const dd = String(date.getDate()).padStart(2, '0');
  const initials = (candidateName || 'XX')
    .split(' ')
    .map(w => w[0]?.toUpperCase() || '')
    .join('')
    .slice(0, 2) || 'XX';
  const rand = Math.random().toString(16).slice(2, 6).toUpperCase();
  return `EPT-${yyyy}-${mm}${dd}-${initials}-${rand}`;
}

// ---- Format seconds as MM:SS ----
function formatTime(secs) {
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

// ---- Countdown timer factory ----
//   onTick(remaining) called every second
//   onExpire() called once when time hits 0
//   Returns { stop() } so caller can cancel.
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

// ---- Guard: redirect to login if no candidate session exists ----
function requireCandidate() {
  const candidate = Store.get('candidate');
  if (!candidate) {
    window.location.href = 'index.html';
    return null;
  }
  return candidate;
}
