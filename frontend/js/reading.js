/* =========================================================================
   reading.js — fetches the assigned passage + questions from the backend,
   renders them, runs the countdown timer, persists answers in sessionStorage.
   ========================================================================= */

(async function () {
  const content = await loadTestContent();
  if (!content) return;

  document.getElementById('candidateMeta').textContent =
    `${content.candidate_name}  |  ${content.difficulty}`;

  // ---- Render passage ----
  const passageEl = document.getElementById('passage');
  passageEl.innerHTML =
    `<h2>${escapeHtml(content.passage.title)}</h2>` +
    content.passage.body
      .split(/\n\s*\n/)
      .map(p => `<p>${escapeHtml(p)}</p>`)
      .join('');

  // ---- Render questions ----
  const formEl = document.getElementById('questionsForm');
  const answers = Store.get('readingAnswers', {});

  content.questions.forEach((q, qi) => {
    const block = document.createElement('div');
    block.className = 'question';
    const typeLabel = labelForType(q.question_type);
    block.innerHTML = `
      <div class="question-stem">
        <span style="font-size: 11px; color: var(--text-muted); font-weight: normal; text-transform: uppercase; letter-spacing: 0.5px;">${typeLabel}</span><br>
        ${qi + 1}. ${escapeHtml(q.stem)}
      </div>
      ${q.options.map((opt, oi) => `
        <label class="option ${answers[q.id] === oi ? 'selected' : ''}" data-q="${q.id}" data-o="${oi}">
          <input type="radio" name="q_${q.id}" value="${oi}" ${answers[q.id] === oi ? 'checked' : ''}>
          <span><strong>${String.fromCharCode(65 + oi)}.</strong> ${escapeHtml(opt)}</span>
        </label>
      `).join('')}
    `;
    formEl.appendChild(block);
  });

  function updateAnsweredCount() {
    const stored = Store.get('readingAnswers', {});
    const n = Object.keys(stored).length;
    const total = content.questions.length;
    const pct = total === 0 ? 0 : Math.round((n / total) * 100);

    document.getElementById('answeredCount').textContent = `${n} of ${total} answered`;
    document.getElementById('qProgressFill').style.width = pct + '%';
    document.getElementById('qProgressLabel').textContent = `${n} / ${total}`;
  }

  formEl.addEventListener('change', e => {
    if (e.target.matches('input[type="radio"]')) {
      const qId = parseInt(e.target.name.replace('q_', ''), 10);
      const oIdx = parseInt(e.target.value, 10);
      const stored = Store.get('readingAnswers', {});
      stored[qId] = oIdx;
      Store.set('readingAnswers', stored);

      const labels = formEl.querySelectorAll(`label[data-q="${qId}"]`);
      labels.forEach(l => l.classList.toggle(
        'selected',
        parseInt(l.dataset.o, 10) === oIdx
      ));
      updateAnsweredCount();
    }
  });

  updateAnsweredCount();

  // ---- Timer ----
  // Persist the deadline (absolute timestamp) so navigating Back to instructions
  // and re-entering reading.html does NOT reset the clock — matching the
  // back-button warning's "will not stop the timer" promise.
  const timerEl = document.getElementById('timer');
  let deadline = Store.get('readingDeadline');
  if (typeof deadline !== 'number') {
    deadline = Date.now() + content.duration_written_seconds * 1000;
    Store.set('readingDeadline', deadline);
  }
  const initialRemaining = Math.max(0, Math.floor((deadline - Date.now()) / 1000));
  if (initialRemaining === 0) {
    // Reading time is up — advance to the *next* section (Writing), not Speaking.
    // This must match onExpire below so all three exit points behave consistently.
    Store.set('readingTimeUp', true);
    window.location.href = 'writing.html';
    return;
  }
  const ticker = startCountdown(initialRemaining,
    (rem) => {
      timerEl.textContent = `⏱  ${formatTime(rem)}`;
      timerEl.classList.toggle('warning', rem <= 60 && rem > 15);
      timerEl.classList.toggle('danger',  rem <= 15);
    },
    () => {
      Store.set('readingTimeUp', true);
      window.location.href = 'writing.html';
    }
  );

  document.getElementById('backBtn').addEventListener('click', async () => {
    const ok = await Modal.confirm(
      'Going back will not stop the timer. The countdown keeps running.',
      { okText: 'Go Back', cancelText: 'Stay' }
    );
    if (ok) window.location.href = 'instructions.html';
  });

  document.getElementById('nextBtn').addEventListener('click', async () => {
    const stored = Store.get('readingAnswers', {});
    const unanswered = content.questions.filter(q => stored[q.id] === undefined);
    if (unanswered.length > 0) {
      const ok = await Modal.confirm(
        `You have ${unanswered.length} unanswered question${unanswered.length === 1 ? '' : 's'}. Continue to the Writing section?`,
        { okText: 'Continue', cancelText: 'Keep Answering', dangerous: true }
      );
      if (!ok) return;
    }
    ticker.stop();
    window.location.href = 'writing.html';
  });

  window.addEventListener('beforeunload', e => {
    e.preventDefault();
    e.returnValue = '';
  });

  // 3-strike termination from visibility-tracker.js. When the candidate
  // switches away the 3rd time, the tracker dispatches this event and we
  // hand off to force-submit.js which posts whatever data exists.
  document.addEventListener('visibility:terminate', () => {
    ForceSubmit.terminateAndSubmit();
  });

  // ---- helpers ----
  function labelForType(t) {
    return ({
      reading_comp: 'Reading Comprehension',
      grammar: 'Grammar',
      vocabulary: 'Vocabulary',
      fill_blank: 'Fill in the Blank',
    })[t] || t;
  }
  function escapeHtml(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({
      '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
    }[c]));
  }
})();