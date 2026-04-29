/* =========================================================================
   reading.js — renders the passage + MCQs, runs the countdown timer,
   stores answers in sessionStorage, auto-advances when time expires.
   ========================================================================= */

(function () {
  const candidate = requireCandidate();
  if (!candidate) return;

  document.getElementById('candidateMeta').textContent =
    `${candidate.name}  |  ${candidate.email}`;

  const { reading } = window.TEST_CONTENT;

  // ---- Render passage ----
  const passageEl = document.getElementById('passage');
  passageEl.innerHTML = `<h2>${reading.passage.title}</h2>` +
    reading.passage.paragraphs.map(p => `<p>${p}</p>`).join('');

  // ---- Render questions ----
  const formEl = document.getElementById('questionsForm');
  const answers = Store.get('readingAnswers', {});

  reading.questions.forEach((q, qi) => {
    const block = document.createElement('div');
    block.className = 'question';
    block.innerHTML = `
      <div class="question-stem">${qi + 1}. ${q.stem}</div>
      ${q.options.map((opt, oi) => `
        <label class="option ${answers[q.id] === oi ? 'selected' : ''}" data-q="${q.id}" data-o="${oi}">
          <input type="radio" name="${q.id}" value="${oi}" ${answers[q.id] === oi ? 'checked' : ''}>
          <span><strong>${String.fromCharCode(65 + oi)}.</strong> ${opt}</span>
        </label>
      `).join('')}
    `;
    formEl.appendChild(block);
  });

  // ---- Persist answers as the user clicks ----
  function updateAnsweredCount() {
    const stored = Store.get('readingAnswers', {});
    const n = Object.keys(stored).length;
    const total = reading.questions.length;
    const pct = total === 0 ? 0 : Math.round((n / total) * 100);

    // Bottom-of-page text counter (existing)
    document.getElementById('answeredCount').textContent =
      `${n} of ${total} answered`;

    // NEW: per-question progress bar inside the questions card
    document.getElementById('qProgressFill').style.width = pct + '%';
    document.getElementById('qProgressLabel').textContent = `${n} / ${total}`;
  }

  formEl.addEventListener('change', e => {
    if (e.target.matches('input[type="radio"]')) {
      const qId = e.target.name;
      const oIdx = parseInt(e.target.value, 10);
      const stored = Store.get('readingAnswers', {});
      stored[qId] = oIdx;
      Store.set('readingAnswers', stored);

      // visual: highlight selected option, un-highlight siblings
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
  const timerEl = document.getElementById('timer');
  const ticker = startCountdown(reading.durationSeconds,
    (rem) => {
      timerEl.textContent = `⏱  ${formatTime(rem)}`;
      timerEl.classList.toggle('warning', rem <= 60 && rem > 15);
      timerEl.classList.toggle('danger',  rem <= 15);
    },
    () => {
      // Time's up — store whatever we have and move on
      Store.set('readingTimeUp', true);
      window.location.href = 'speaking.html';
    }
  );

  // ---- Buttons ----
  document.getElementById('backBtn').addEventListener('click', () => {
    if (confirm('Going back will not stop the timer. Continue?')) {
      window.location.href = 'instructions.html';
    }
  });

  document.getElementById('nextBtn').addEventListener('click', () => {
    const stored = Store.get('readingAnswers', {});
    const unanswered = reading.questions.filter(q => stored[q.id] === undefined);
    if (unanswered.length > 0) {
      if (!confirm(`You have ${unanswered.length} unanswered question(s). Continue to Speaking section?`)) {
        return;
      }
    }
    ticker.stop();
    window.location.href = 'speaking.html';
  });

  // Warn before unload (refresh/close)
  window.addEventListener('beforeunload', e => {
    e.preventDefault();
    e.returnValue = '';
  });
})();
