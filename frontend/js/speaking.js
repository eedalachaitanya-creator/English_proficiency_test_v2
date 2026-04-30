/* =========================================================================
   speaking.js — fetches the assigned 3 topics, walks the candidate through
   them one at a time, captures audio per topic, stores blobs in memory
   until final submit (Day 2).
   ========================================================================= */

(async function () {
  const content = await loadTestContent();
  if (!content) return;

  document.getElementById('candidateMeta').textContent =
    `${content.candidate_name}  |  ${content.difficulty}`;

  const topics = content.speaking_topics;
  if (!topics || topics.length === 0) {
    await Modal.alert('No speaking topics were assigned. Please contact your HR manager.', { title: 'Setup error' });
    return;
  }

  // Per-topic max time (total budget / number of topics)
  const PER_TOPIC_SECONDS = Math.floor(content.duration_speaking_seconds / topics.length);

  // ---- Recording state ----
  const recordings = [];           // [{topic_id, blob, mime}]
  let currentTopicIdx = 0;
  let mediaRecorder = null;
  let audioStream = null;
  let audioContext = null;
  let analyser = null;
  let waveformAnimId = null;
  let chunks = [];
  let recTimer = null;

  const startBtn = document.getElementById('startBtn');
  const stopBtn = document.getElementById('stopBtn');
  const finishBtn = document.getElementById('finishBtn');
  const recStatus = document.getElementById('recStatus');
  const micIcon = document.getElementById('micIcon');
  const playback = document.getElementById('playback');
  const timerEl = document.getElementById('timer');
  const topicTextEl = document.getElementById('topicText');

  const waveformEl = document.getElementById('waveform');
  const BAR_COUNT = 40;
  for (let i = 0; i < BAR_COUNT; i++) waveformEl.appendChild(document.createElement('span'));
  const bars = waveformEl.querySelectorAll('span');

  function showCurrentTopic() {
    const t = topics[currentTopicIdx];
    topicTextEl.textContent = `Question ${currentTopicIdx + 1} of ${topics.length}: ${t.prompt_text}`;
    timerEl.textContent = `⏱  ${formatTime(PER_TOPIC_SECONDS)}`;
    recStatus.textContent = 'Ready to record';
    recStatus.classList.add('idle');
    recStatus.style.color = '';
    startBtn.disabled = false;
    stopBtn.disabled = true;
    playback.classList.add('hidden');
  }

  showCurrentTopic();

  function animateWaveform() {
    const data = new Uint8Array(analyser.frequencyBinCount);
    const draw = () => {
      analyser.getByteFrequencyData(data);
      const step = Math.floor(data.length / BAR_COUNT);
      for (let i = 0; i < BAR_COUNT; i++) {
        const v = data[i * step] / 255;
        bars[i].style.height = `${4 + v * 56}px`;
      }
      waveformAnimId = requestAnimationFrame(draw);
    };
    draw();
  }

  startBtn.addEventListener('click', async () => {
    try {
      audioStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      recStatus.textContent = '✗ Microphone permission denied';
      recStatus.classList.remove('idle');
      recStatus.style.color = 'var(--red)';
      return;
    }

    audioContext = new (window.AudioContext || window.webkitAudioContext)();
    const source = audioContext.createMediaStreamSource(audioStream);
    analyser = audioContext.createAnalyser();
    analyser.fftSize = 128;
    source.connect(analyser);
    waveformEl.classList.add('active');
    animateWaveform();

    chunks = [];
    mediaRecorder = new MediaRecorder(audioStream);
    mediaRecorder.ondataavailable = e => { if (e.data.size > 0) chunks.push(e.data); };
    mediaRecorder.onstop = handleStopped;
    mediaRecorder.start();

    micIcon.classList.add('recording');
    recStatus.classList.remove('idle');
    recStatus.style.color = 'var(--red)';
    recStatus.textContent = `RECORDING  •  00:00 / ${formatTime(PER_TOPIC_SECONDS)}`;
    startBtn.disabled = true;
    stopBtn.disabled = false;

    recTimer = startCountdown(PER_TOPIC_SECONDS,
      (rem) => {
        const elapsed = PER_TOPIC_SECONDS - rem;
        timerEl.textContent = `⏱  ${formatTime(rem)}`;
        recStatus.textContent =
          `RECORDING  •  ${formatTime(elapsed)} / ${formatTime(PER_TOPIC_SECONDS)}`;
        timerEl.classList.toggle('warning', rem <= 30 && rem > 10);
        timerEl.classList.toggle('danger',  rem <= 10);
      },
      stopRecording
    );
  });

  function stopRecording() {
    // Idempotent: callable from the Stop button, the timer's onExpire, AND the
    // Back button. Null out each resource once cleaned up so a second call
    // doesn't re-close an already-closed AudioContext (which rejects).
    if (mediaRecorder && mediaRecorder.state !== 'inactive') mediaRecorder.stop();
    if (recTimer) { recTimer.stop(); recTimer = null; }
    if (audioStream) {
      audioStream.getTracks().forEach(t => t.stop());
      audioStream = null;
    }
    if (audioContext) {
      audioContext.close().catch(() => {});
      audioContext = null;
    }
    if (waveformAnimId) {
      cancelAnimationFrame(waveformAnimId);
      waveformAnimId = null;
    }
    waveformEl.classList.remove('active');
    bars.forEach(b => b.style.height = '8px');
    micIcon.classList.remove('recording');
    startBtn.disabled = true;
    stopBtn.disabled = true;
  }
  stopBtn.addEventListener('click', stopRecording);

  function handleStopped() {
    const blob = new Blob(chunks, { type: 'audio/webm' });
    recordings.push({
      topic_id: topics[currentTopicIdx].id,
      blob,
      mime: blob.type,
    });
    const url = URL.createObjectURL(blob);
    playback.src = url;
    playback.classList.remove('hidden');
    recStatus.textContent = `Recorded for question ${currentTopicIdx + 1} (${(blob.size/1024).toFixed(1)} KB)`;
    recStatus.style.color = 'var(--green)';

    // Move to next topic, or enable submit if last
    if (currentTopicIdx < topics.length - 1) {
      const nextBtn = document.createElement('button');
      nextBtn.className = 'btn btn-primary mt-4';
      nextBtn.textContent = `NEXT QUESTION (${currentTopicIdx + 2} of ${topics.length})  →`;
      nextBtn.addEventListener('click', () => {
        nextBtn.remove();
        currentTopicIdx += 1;
        showCurrentTopic();
      });
      document.querySelector('.recorder').appendChild(nextBtn);
    } else {
      finishBtn.disabled = false;
    }
  }

  finishBtn.addEventListener('click', async () => {
    if (recordings.length < topics.length) {
      const ok = await Modal.confirm(
        `You only recorded ${recordings.length} of ${topics.length} questions. Submit anyway?`,
        { okText: 'Submit Anyway', cancelText: 'Keep Recording', dangerous: true }
      );
      if (!ok) return;
    }
    const ok = await Modal.confirm(
      'Once you submit, your test is final. You cannot re-record.',
      { okText: 'Submit Test', cancelText: 'Wait', dangerous: true, title: 'Confirm submission' }
    );
    if (!ok) return;

    finishBtn.disabled = true;
    finishBtn.textContent = 'Submitting…';

    // ---- Real submission ----
    // Build a multipart form: MCQ answers as JSON, the speaking topic IDs we recorded
    // for, plus one audio blob per question. The server validates everything and stores
    // it; we never trust the client to score itself.
    try {
      const fd = new FormData();
      fd.append('answers', JSON.stringify(Store.get('readingAnswers', {})));
      fd.append('topic_ids', JSON.stringify(recordings.map(r => r.topic_id)));
      recordings.forEach((r, i) => {
        fd.append(`audio_${i}`, r.blob, `q${i}.webm`);
      });

      const res = await api('/api/submit', { method: 'POST', body: fd });
      Store.set('refId', res.ref_id);
      window.location.href = 'submitted.html';
    } catch (err) {
      finishBtn.disabled = false;
      finishBtn.textContent = 'FINISH & SUBMIT TEST  →';
      await Modal.alert(
        `Could not submit your test: ${err.message}\n\nPlease check your connection and try again.`,
        { title: 'Submission failed' }
      );
    }
  });

  document.getElementById('backBtn').addEventListener('click', async () => {
    const ok = await Modal.confirm(
      'Going back to Reading will discard any recordings you made. Continue?',
      { okText: 'Discard & Go Back', cancelText: 'Stay Here', dangerous: true }
    );
    if (!ok) return;
    stopRecording();
    window.location.href = 'reading.html';
  });

  window.addEventListener('beforeunload', e => {
    e.preventDefault();
    e.returnValue = '';
  });
})();
