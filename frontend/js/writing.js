/* =========================================================================
   writing.js — fetches the assigned essay prompt, runs a 20-min countdown,
   live-updates the word counter, persists the essay to sessionStorage so a
   refresh doesn't lose progress, navigates to speaking.html on Next.
   ========================================================================= */

(async function () {
  const content = await loadTestContent();
  if (!content) return;

  document.getElementById('candidateMeta').textContent =
    `${content.candidate_name}  |  ${content.difficulty}`;

  const topic = content.writing_topic;
  if (!topic) {
    await Modal.alert(
      'No essay prompt was assigned. Please contact your HR manager.',
      { title: 'Setup error' }
    );
    return;
  }

  // ---- Render prompt + word-range hint ----
  document.getElementById('topicText').textContent = topic.prompt_text;
  const rangeText = `Aim for ${topic.min_words}–${topic.max_words} words.`;
  document.getElementById('wordRangeHint').textContent = rangeText;
  document.getElementById('wordRangeLabel').textContent = `(target: ${topic.min_words}–${topic.max_words})`;

  // ---- Restore saved essay if the candidate is returning to this page ----
  const textarea = document.getElementById('essayTextarea');
  const saved = Store.get('writingEssay', '');
  if (typeof saved === 'string' && saved.length > 0) {
    textarea.value = saved;
  }

  // ---- Live word counter ----
  const wordCountEl = document.getElementById('wordCount');
  const wordWarnEl = document.getElementById('wordWarning');

  function countWords(text) {
    return (text.trim().match(/\S+/g) || []).length;
  }

  function updateWordCount() {
    const text = textarea.value;
    const n = countWords(text);
    wordCountEl.textContent = n;
    wordCountEl.classList.remove('in-range', 'below', 'above');

    if (n === 0) {
      wordWarnEl.textContent = '';
    } else if (n < 50) {
      wordCountEl.classList.add('below');
      wordWarnEl.textContent = `Below the 50-word minimum. Submissions under 50 words are rejected.`;
      wordWarnEl.style.color = 'var(--red)';
    } else if (n < topic.min_words) {
      wordCountEl.classList.add('below');
      wordWarnEl.textContent = `${topic.min_words - n} words below target.`;
      wordWarnEl.style.color = 'var(--text-muted)';
    } else if (n > topic.max_words) {
      wordCountEl.classList.add('above');
      wordWarnEl.textContent = `${n - topic.max_words} words over target — still accepted.`;
      wordWarnEl.style.color = 'var(--orange)';
    } else {
      wordCountEl.classList.add('in-range');
      wordWarnEl.textContent = '✓ within target range';
      wordWarnEl.style.color = 'var(--green)';
    }

    Store.set('writingEssay', text);
  }
  updateWordCount();
  textarea.addEventListener('input', updateWordCount);

  // ---- Timer ----
  // Persist deadline so navigating Back to reading and returning doesn't reset it.
  const timerEl = document.getElementById('timer');
  let deadline = Store.get('writingDeadline');
  if (typeof deadline !== 'number') {
    deadline = Date.now() + content.duration_writing_seconds * 1000;
    Store.set('writingDeadline', deadline);
  }
  const initialRemaining = Math.max(0, Math.floor((deadline - Date.now()) / 1000));
  if (initialRemaining === 0) {
    Store.set('writingTimeUp', true);
    window.location.href = 'speaking.html';
    return;
  }
  const ticker = startCountdown(initialRemaining,
    (rem) => {
      timerEl.textContent = `⏱  ${formatTime(rem)}`;
      timerEl.classList.toggle('warning', rem <= 60 && rem > 15);
      timerEl.classList.toggle('danger', rem <= 15);
    },
    () => {
      Store.set('writingTimeUp', true);
      window.location.href = 'speaking.html';
    }
  );

  // ---- Navigation ----
  document.getElementById('backBtn').addEventListener('click', async () => {
    const ok = await Modal.confirm(
      'Going back to Reading will not stop the writing timer. The countdown keeps running. Continue?',
      { okText: 'Go Back', cancelText: 'Stay' }
    );
    if (ok) window.location.href = 'reading.html';
  });

  document.getElementById('nextBtn').addEventListener('click', async () => {
    const n = countWords(textarea.value);
    if (n < 50) {
      await Modal.alert(
        `You've written ${n} word${n === 1 ? '' : 's'}. The minimum is 50. Please write more before continuing.`,
        { title: 'Essay too short' }
      );
      return;
    }
    if (n < topic.min_words) {
      const ok = await Modal.confirm(
        `You're ${topic.min_words - n} words below the target range. Continue to the Speaking section anyway?`,
        { okText: 'Continue', cancelText: 'Keep Writing', dangerous: true }
      );
      if (!ok) return;
    }
    ticker.stop();
    window.location.href = 'speaking.html';
  });

  window.addEventListener('beforeunload', e => {
    e.preventDefault();
    e.returnValue = '';
  });
})();
