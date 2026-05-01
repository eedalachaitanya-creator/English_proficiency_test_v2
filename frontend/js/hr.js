/* =========================================================================
   hr.js — HR dashboard logic. Talks to /api/hr/* endpoints.
   ========================================================================= */

let allResults = [];
let currentHr = null;

// ---- Auth check + greeting ----
async function init() {
  try {
    currentHr = await api('/api/hr/me');
    document.getElementById('hrEmail').textContent = currentHr.email;
  } catch (err) {
    // Not logged in — bounce to login page
    window.location.href = 'index.html';
    return;
  }

  await loadResults();
  bindEvents();
}

// ---- Logout ----
document.getElementById('logoutLink').addEventListener('click', async (e) => {
  e.preventDefault();
  try { await api('/api/hr/logout', { method: 'POST' }); } catch {}
  window.location.href = 'index.html';
});

// ---- Load results from server ----
async function loadResults() {
  try {
    allResults = await api('/api/hr/results');
  } catch (err) {
    if (err.status === 401) {
      window.location.href = 'index.html';
      return;
    }
    allResults = [];
    console.error('Failed to load results:', err);
  }
  renderKPIs(allResults);
  renderTable(allResults);
  if (allResults.length > 0) {
    showDetail(allResults[0]);
  }
}

// ---- KPIs ----
function renderKPIs(rows) {
  document.getElementById('kpiTotal').textContent = rows.length;

  const submitted = rows.filter(r => r.submitted_at);
  document.getElementById('kpiSubmitted').textContent = submitted.length;
  document.getElementById('kpiPending').textContent = rows.length - submitted.length;

  const scored = rows.filter(r => r.total_score != null);
  if (scored.length) {
    const avg = scored.reduce((s, r) => s + r.total_score, 0) / scored.length;
    document.getElementById('kpiAvg').textContent = Math.round(avg) + ' / 100';
  } else {
    document.getElementById('kpiAvg').textContent = '—';
  }
}

// ---- Table ----
function renderTable(rows) {
  const tbody = document.getElementById('resultsTbody');
  if (rows.length === 0) {
    tbody.innerHTML = `<tr><td colspan="9" class="text-muted text-center" style="padding: 24px;">No invitations yet. Click "+ INVITE NEW CANDIDATE" to send the first one.</td></tr>`;
    return;
  }

  tbody.innerHTML = '';
  rows.forEach(r => {
    const tr = document.createElement('tr');
    const submitted = r.submitted_at
      ? new Date(r.submitted_at).toLocaleDateString()
      : '<span class="text-muted">—</span>';
    const reading = r.reading_score != null ? r.reading_score : '—';
    const writing = r.writing_score != null ? r.writing_score : '—';
    const speaking = r.speaking_score != null ? r.speaking_score : '—';
    const total = r.total_score != null ? `<strong>${r.total_score}</strong>` : '—';
    const ratingBadge = r.rating
      ? `<span class="badge ${ratingClass(r.rating)}">${ratingLabel(r.rating)}</span>`
      : '<span class="text-muted">pending</span>';

    tr.innerHTML = `
      <td><strong>${escapeHtml(r.candidate_name)}</strong></td>
      <td class="text-muted">${escapeHtml(r.candidate_email)}</td>
      <td>${escapeHtml(r.difficulty)}</td>
      <td>${submitted}</td>
      <td class="text-mono">${reading}</td>
      <td class="text-mono">${writing}</td>
      <td class="text-mono">${speaking}</td>
      <td>${total}</td>
      <td>${ratingBadge}</td>
    `;
    tr.addEventListener('click', () => showDetail(r));
    tbody.appendChild(tr);
  });
}

function ratingClass(rating) {
  if (rating === 'recommended') return 'reviewed';
  if (rating === 'borderline') return 'new';
  return 'flagged';
}
function ratingLabel(rating) {
  if (rating === 'recommended') return 'Recommended';
  if (rating === 'borderline') return 'Borderline';
  if (rating === 'not_recommended') return 'Not Recommended';
  return rating;
}

// ---- Detail panel ----
async function showDetail(row) {
  document.getElementById('detailTitle').textContent =
    `Candidate Detail  —  ${row.candidate_name}`;

  let detail;
  try {
    detail = await api(`/api/hr/results/${row.invitation_id}`);
  } catch (err) {
    if (err.status === 401) {
      window.location.href = 'index.html';
      return;
    }
    document.getElementById('readingDetail').textContent = `Error: ${err.message}`;
    return;
  }

  // Reading
  if (detail.reading_score != null) {
    document.getElementById('readingDetail').innerHTML = `
      Score: <strong>${detail.reading_score} / 100</strong><br>
      Correct: ${detail.reading_correct} of ${detail.reading_total}<br>
      Submitted: ${new Date(detail.submitted_at).toLocaleString()}
    `;
  } else {
    document.getElementById('readingDetail').textContent = 'Not yet submitted.';
  }

  // Writing — score breakdown + the candidate's essay text
  renderWritingDetail(detail);

  // Speaking — rubric scores + per-question audio playback.
  // Audio players use HR's session cookie automatically because the <audio>
  // src is on the same origin; the server's tenancy check enforces access.
  const speakingEl = document.getElementById('speakingDetail');
  let speakingHtml = '';

  if (detail.speaking_breakdown) {
    // Two-column flex per row so labels and scores line up — HTML collapses
    // multiple spaces, so padEnd alignment never worked here.
    const lines = Object.entries(detail.speaking_breakdown)
      .map(([k, v]) => `
        <div class="text-mono" style="display: flex; justify-content: space-between; max-width: 240px;">
          <span>${escapeHtml(k)}</span>
          <span>${v == null ? '—' : v}</span>
        </div>
      `)
      .join('');
    speakingHtml += lines +
      `<div style="margin-top: 12px;"><strong>Score: ${detail.speaking_score} / 100</strong></div>`;
  } else if (detail.submitted_at) {
    speakingHtml += '<em>AI scoring pending.</em>';
  } else {
    speakingHtml += 'Not yet submitted.';
  }

  // Audio recordings list — one player per question
  if (detail.audio_recordings && detail.audio_recordings.length > 0) {
    speakingHtml += '<br><br><strong>RECORDED RESPONSES</strong>';
    detail.audio_recordings.forEach(rec => {
      const qLabel = rec.question_index >= 0
        ? `Q${rec.question_index + 1}`
        : 'Q?';
      speakingHtml += `
        <div style="margin-top: 12px; padding: 10px; background: var(--bg); border-radius: var(--radius);">
          <div style="font-size: 12px; color: var(--text-muted); margin-bottom: 6px;">
            ${qLabel}: ${escapeHtml(rec.topic_prompt)}
          </div>
          <audio controls preload="none" style="width: 100%; height: 36px;"
                 src="/api/hr/audio/${rec.id}"></audio>
          ${rec.transcript ? `<div style="margin-top: 6px; font-size: 12px; color: var(--text); font-family: var(--font-body);"><em>Transcript:</em> ${escapeHtml(rec.transcript)}</div>` : ''}
        </div>
      `;
    });
  }

  speakingEl.innerHTML = speakingHtml;

  // Feedback
  document.getElementById('feedback').textContent =
    detail.ai_feedback || 'AI feedback will appear here after the candidate submits.';
}

// ---- Filters ----
function applyFilters() {
  const q = document.getElementById('search').value.trim().toLowerCase();
  const status = document.getElementById('statusFilter').value;

  const filtered = allResults.filter(r => {
    const matchesQ = !q
      || r.candidate_name.toLowerCase().includes(q)
      || r.candidate_email.toLowerCase().includes(q);
    const isSubmitted = !!r.submitted_at;
    const matchesS = !status
      || (status === 'submitted' && isSubmitted)
      || (status === 'pending' && !isSubmitted);
    return matchesQ && matchesS;
  });
  renderTable(filtered);
}

// ---- Invite modal ----
function bindEvents() {
  const modal = document.getElementById('inviteModal');
  const openBtn = document.getElementById('inviteBtn');
  const cancelBtn = document.getElementById('inviteCancelBtn');
  const form = document.getElementById('inviteForm');
  const resultEl = document.getElementById('inviteResult');
  const errorEl = document.getElementById('inviteError');

  openBtn.addEventListener('click', () => {
    form.reset();
    resultEl.classList.add('hidden');
    errorEl.textContent = '';
    modal.classList.add('show');
  });
  cancelBtn.addEventListener('click', () => modal.classList.remove('show'));
  modal.addEventListener('click', (e) => {
    if (e.target === modal) modal.classList.remove('show');
  });

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    errorEl.textContent = '';

    const candidate_name = document.getElementById('candName').value.trim();
    const candidate_email = document.getElementById('candEmail').value.trim();
    const difficulty = document.querySelector('input[name="difficulty"]:checked').value;

    if (!candidate_name || !candidate_email) {
      errorEl.textContent = 'Both fields are required.';
      return;
    }

    const submitBtn = form.querySelector('button[type="submit"]');
    submitBtn.disabled = true;
    submitBtn.textContent = 'Generating…';

    try {
      const res = await api('/api/hr/invite', {
        method: 'POST',
        body: { candidate_name, candidate_email, difficulty },
      });
      resultEl.innerHTML = `
        <strong>Invitation created.</strong><br>
        Send this URL to <em>${escapeHtml(candidate_email)}</em>:<br>
        <span style="user-select: all;">${escapeHtml(res.exam_url)}</span><br>
        <small>Expires: ${new Date(res.expires_at).toLocaleString()}</small><br>
        <button type="button" class="btn btn-secondary" id="copyUrlBtn">Copy URL</button>
      `;
      resultEl.classList.remove('hidden');
      document.getElementById('copyUrlBtn').addEventListener('click', () => {
        navigator.clipboard.writeText(res.exam_url);
        document.getElementById('copyUrlBtn').textContent = 'Copied ✓';
      });
      // Refresh table in background
      loadResults();
    } catch (err) {
      errorEl.textContent = err.message || 'Could not create invitation.';
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = 'GENERATE LINK  →';
    }
  });

  // Search + filter inputs
  document.getElementById('search').addEventListener('input', applyFilters);
  document.getElementById('statusFilter').addEventListener('change', applyFilters);
}

// ---- Render the Writing card (essay text + rubric scores) ----
function renderWritingDetail(detail) {
  const el = document.getElementById('writingDetail');
  if (!el) return;

  if (!detail.submitted_at) {
    el.textContent = 'Not yet submitted.';
    return;
  }

  let html = '';

  // Score block (rubric or pending message). Two-column flex per row so the
  // dimension labels and scores line up without depending on whitespace
  // preservation (HTML collapses multiple spaces, so padEnd never worked here).
  if (detail.writing_breakdown) {
    const lines = Object.entries(detail.writing_breakdown)
      .map(([k, v]) => `
        <div class="text-mono" style="display: flex; justify-content: space-between; max-width: 240px;">
          <span>${escapeHtml(k)}</span>
          <span>${v == null ? '—' : v}</span>
        </div>
      `)
      .join('');
    html += `<div style="margin-bottom: 12px;">
      ${lines}
      <div style="margin-top: 12px;"><strong>Score: ${detail.writing_score} / 100</strong></div>
    </div>`;
  } else {
    html += `<div style="margin-bottom: 12px;"><em>AI grading not available for this submission — see feedback below for details.</em></div>`;
  }

  // Topic
  if (detail.writing_topic_text) {
    html += `<div style="margin-top: 16px; padding: 10px 12px; background: var(--bg); border-radius: var(--radius); font-size: 12px; color: var(--text-muted);">
      <strong>Prompt:</strong> ${escapeHtml(detail.writing_topic_text)}
    </div>`;
  }

  // Essay text
  if (detail.essay_text) {
    const wordCountLabel = detail.essay_word_count != null ? ` (${detail.essay_word_count} words)` : '';
    html += `<div style="margin-top: 12px;">
      <div style="font-size: 12px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 6px;">
        Candidate's essay${wordCountLabel}
      </div>
      <div style="white-space: pre-wrap; padding: 14px; background: var(--white); border: 1px solid var(--border); border-radius: var(--radius); line-height: 1.5; max-height: 320px; overflow-y: auto;">${escapeHtml(detail.essay_text)}</div>
    </div>`;
  } else {
    html += `<div style="margin-top: 12px; color: var(--text-muted); font-style: italic;">No essay text on file.</div>`;
  }

  el.innerHTML = html;
}


// ---- HTML escaping (defence against bad data) ----
function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

// ---- Boot ----
init();
