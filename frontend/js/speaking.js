/* =========================================================================
   speaking.js — picks a random topic, records audio with MediaRecorder,
   shows a live waveform, runs the 2-minute cap timer, stores the recording
   blob (as base64) in sessionStorage so submitted.html can show ref ID.
   ========================================================================= */

(function () {
  const candidate = requireCandidate();
  if (!candidate) return;

  document.getElementById('candidateMeta').textContent =
    `${candidate.name}  |  ${candidate.email}`;

  const { speaking } = window.TEST_CONTENT;

  // ---- Pick a topic (deterministic per candidate so refresh shows same one) ----
  let topicIndex = Store.get('topicIndex', null);
  if (topicIndex === null) {
    topicIndex = Math.floor(Math.random() * speaking.topics.length);
    Store.set('topicIndex', topicIndex);
  }
  const topic = speaking.topics[topicIndex];
  document.getElementById('topicText').textContent = `"${topic}"`;
  Store.set('speakingTopic', topic);

  // ---- Build static waveform bars (animated when recording) ----
  const waveformEl = document.getElementById('waveform');
  const BAR_COUNT = 40;
  for (let i = 0; i < BAR_COUNT; i++) {
    waveformEl.appendChild(document.createElement('span'));
  }
  const bars = waveformEl.querySelectorAll('span');

  // ---- Recorder state ----
  let mediaRecorder = null;
  let audioStream = null;
  let audioContext = null;
  let analyser = null;
  let waveformAnimId = null;
  let chunks = [];
  let recTimer = null;

  const startBtn   = document.getElementById('startBtn');
  const stopBtn    = document.getElementById('stopBtn');
  const finishBtn  = document.getElementById('finishBtn');
  const recStatus  = document.getElementById('recStatus');
  const micIcon    = document.getElementById('micIcon');
  const playback   = document.getElementById('playback');
  const timerEl    = document.getElementById('timer');

  // Initial timer display (no countdown until recording starts)
  timerEl.textContent = `⏱  ${formatTime(speaking.durationSeconds)}`;

  // ---- Live waveform animation using AnalyserNode ----
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

  // ---- Start recording ----
  startBtn.addEventListener('click', async () => {
    try {
      audioStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      recStatus.textContent = '✗ Microphone permission denied or unavailable';
      recStatus.classList.remove('idle');
      recStatus.style.color = 'var(--red)';
      return;
    }

    // AudioContext for live waveform
    audioContext = new (window.AudioContext || window.webkitAudioContext)();
    const source = audioContext.createMediaStreamSource(audioStream);
    analyser = audioContext.createAnalyser();
    analyser.fftSize = 128;
    source.connect(analyser);
    waveformEl.classList.add('active');
    animateWaveform();

    // MediaRecorder for actual capture
    chunks = [];
    mediaRecorder = new MediaRecorder(audioStream);
    mediaRecorder.ondataavailable = e => {
      if (e.data.size > 0) chunks.push(e.data);
    };
    mediaRecorder.onstop = handleStopped;
    mediaRecorder.start();

    micIcon.classList.add('recording');
    recStatus.classList.remove('idle');
    recStatus.textContent = `RECORDING  •  00:00 / ${formatTime(speaking.durationSeconds)}`;
    startBtn.disabled = true;
    stopBtn.disabled = false;

    // Countdown that also auto-stops at 0
    recTimer = startCountdown(speaking.durationSeconds,
      (rem) => {
        const elapsed = speaking.durationSeconds - rem;
        timerEl.textContent = `⏱  ${formatTime(rem)}`;
        recStatus.textContent =
          `RECORDING  •  ${formatTime(elapsed)} / ${formatTime(speaking.durationSeconds)}`;
        timerEl.classList.toggle('warning', rem <= 30 && rem > 10);
        timerEl.classList.toggle('danger',  rem <= 10);
      },
      () => stopRecording()
    );
  });

  // ---- Stop recording (button or auto on timer expiry) ----
  function stopRecording() {
    if (mediaRecorder && mediaRecorder.state !== 'inactive') {
      mediaRecorder.stop();
    }
    if (recTimer) recTimer.stop();
    if (audioStream) audioStream.getTracks().forEach(t => t.stop());
    if (audioContext) audioContext.close();
    if (waveformAnimId) cancelAnimationFrame(waveformAnimId);
    waveformEl.classList.remove('active');
    bars.forEach(b => b.style.height = '8px');
    micIcon.classList.remove('recording');
    startBtn.disabled = true;
    stopBtn.disabled = true;
  }

  stopBtn.addEventListener('click', stopRecording);

  // ---- After MediaRecorder finalises the blob ----
  function handleStopped() {
    const blob = new Blob(chunks, { type: 'audio/webm' });
    const url = URL.createObjectURL(blob);
    playback.src = url;
    playback.classList.remove('hidden');
    recStatus.textContent = `Recording complete  •  ${(blob.size / 1024).toFixed(1)} KB`;
    recStatus.style.color = 'var(--green)';
    finishBtn.disabled = false;

    // Save blob as base64 in sessionStorage so submitted.html can confirm.
    // (The real backend will receive the blob via FormData; this is a placeholder.)
    const reader = new FileReader();
    reader.onloadend = () => {
      Store.set('speakingAudio', {
        dataUrl: reader.result,
        sizeBytes: blob.size,
        mimeType: blob.type,
      });
    };
    reader.readAsDataURL(blob);
  }

  // ---- Submit final test ----
  finishBtn.addEventListener('click', () => {
    if (!confirm('Submit your test? You cannot re-record.')) return;

    // ---- BACKEND HOOK ----
    // Real flow: build a FormData with answers + audio blob, POST to /api/submit.
    // For now we just generate a reference ID and move on.
    const refId = generateRefId(candidate.name);
    Store.set('refId', refId);
    Store.set('submittedAt', new Date().toISOString());

    window.location.href = 'submitted.html';
  });

  // ---- Back ----
  document.getElementById('backBtn').addEventListener('click', () => {
    if (confirm('Going back will discard your recording (if any). Continue?')) {
      stopRecording();
      window.location.href = 'reading.html';
    }
  });

  // Refresh warning
  window.addEventListener('beforeunload', e => {
    e.preventDefault();
    e.returnValue = '';
  });
})();
