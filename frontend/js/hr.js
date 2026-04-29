/* =========================================================================
   hr.js — renders the HR dashboard with mock data.
   When the backend is built, replace MOCK_RESULTS with a fetch('/api/results').
   ========================================================================= */

const MOCK_RESULTS = [
  {
    id: 'EPT-2026-0428-JD-7F3A', name: 'Jane Doe', email: 'jane.d@x.com',
    date: 'Apr 28', reading: 38, readingMax: 40, speaking: 42, speakingMax: 60,
    status: 'Reviewed',
    readingDetail: ['Q1 ✓','Q2 ✓','Q3 ✗','Q4 ✓','Q5 ✓','Q6 ✓','Q7 ✓','Q8 ✓'],
    timeTaken: '21:14',
    speakingScores: { Fluency: 9, Pronunciation: 8, Grammar: 7, Vocabulary: 8, Coherence: 10 },
    feedback: 'Strong vocabulary range and natural pacing. Minor tense errors in past-perfect constructions. Topic was addressed clearly with two supporting examples. Recommend follow-up interview.',
  },
  {
    id: 'EPT-2026-0428-AP-2B1C', name: 'Arjun Patel', email: 'arjun.p@x.com',
    date: 'Apr 28', reading: 32, readingMax: 40, speaking: 48, speakingMax: 60,
    status: 'New',
    readingDetail: ['Q1 ✓','Q2 ✗','Q3 ✓','Q4 ✓','Q5 ✗','Q6 ✓','Q7 ✓','Q8 ✓'],
    timeTaken: '23:02',
    speakingScores: { Fluency: 8, Pronunciation: 9, Grammar: 8, Vocabulary: 9, Coherence: 14 },
    feedback: 'Excellent fluency and clear coherence. Vocabulary is varied and accurate. Some minor article-usage issues. Strong overall.',
  },
  {
    id: 'EPT-2026-0427-MG-9D4E', name: 'Maria Gomez', email: 'maria.g@x.com',
    date: 'Apr 27', reading: 40, readingMax: 40, speaking: 55, speakingMax: 60,
    status: 'Reviewed',
    readingDetail: Array(8).fill('✓').map((m,i)=>`Q${i+1} ${m}`),
    timeTaken: '18:42',
    speakingScores: { Fluency: 10, Pronunciation: 9, Grammar: 9, Vocabulary: 10, Coherence: 17 },
    feedback: 'Outstanding response. Native-like fluency, sophisticated vocabulary, well-structured argument with clear examples. Top-tier candidate.',
  },
  {
    id: 'EPT-2026-0427-LW-5C8F', name: 'Liu Wei', email: 'liu.w@x.com',
    date: 'Apr 27', reading: 26, readingMax: 40, speaking: 30, speakingMax: 60,
    status: 'Flagged',
    readingDetail: ['Q1 ✓','Q2 ✗','Q3 ✗','Q4 ✓','Q5 ✗','Q6 ✓','Q7 ✗','Q8 ✓'],
    timeTaken: '24:55',
    speakingScores: { Fluency: 5, Pronunciation: 6, Grammar: 5, Vocabulary: 6, Coherence: 8 },
    feedback: 'Hesitations and frequent self-corrections affected fluency. Vocabulary is limited; argument structure was unclear. Consider language-support program before next round.',
  },
  {
    id: 'EPT-2026-0426-SK-3A7B', name: 'Sara Khan', email: 'sara.k@x.com',
    date: 'Apr 26', reading: 35, readingMax: 40, speaking: 50, speakingMax: 60,
    status: 'Reviewed',
    readingDetail: ['Q1 ✓','Q2 ✓','Q3 ✓','Q4 ✗','Q5 ✓','Q6 ✓','Q7 ✓','Q8 ✗'],
    timeTaken: '20:11',
    speakingScores: { Fluency: 8, Pronunciation: 8, Grammar: 8, Vocabulary: 9, Coherence: 17 },
    feedback: 'Well-organised response with strong examples. Grammar accurate, occasional informal phrasing. Solid candidate.',
  },
  {
    id: 'EPT-2026-0426-TB-6E2D', name: 'Tom Becker', email: 'tom.b@x.com',
    date: 'Apr 26', reading: 28, readingMax: 40, speaking: 35, speakingMax: 60,
    status: 'New',
    readingDetail: ['Q1 ✓','Q2 ✓','Q3 ✗','Q4 ✗','Q5 ✓','Q6 ✗','Q7 ✓','Q8 ✓'],
    timeTaken: '22:34',
    speakingScores: { Fluency: 6, Pronunciation: 7, Grammar: 6, Vocabulary: 6, Coherence: 10 },
    feedback: 'Adequate response with simple structure. Limited vocabulary range; occasional grammar slips. Borderline pass — recommend follow-up interview.',
  },
];

// ---- KPIs ----
function renderKPIs(rows) {
  document.getElementById('kpiTotal').textContent = rows.length;
  if (rows.length) {
    const avg = rows.reduce((s, r) =>
      s + Math.round(((r.reading + r.speaking) / (r.readingMax + r.speakingMax)) * 100), 0
    ) / rows.length;
    document.getElementById('kpiAvg').textContent = Math.round(avg) + ' / 100';
  } else {
    document.getElementById('kpiAvg').textContent = '—';
  }
  document.getElementById('kpiPending').textContent =
    rows.filter(r => r.status === 'New' || r.status === 'Flagged').length;
  document.getElementById('kpiWeek').textContent = '+' + rows.length;
}

// ---- Table ----
function renderTable(rows) {
  const tbody = document.getElementById('resultsTbody');
  tbody.innerHTML = '';
  rows.forEach(r => {
    const tr = document.createElement('tr');
    const totalPct = Math.round(((r.reading + r.speaking) / (r.readingMax + r.speakingMax)) * 100);
    tr.innerHTML = `
      <td><strong>${r.name}</strong></td>
      <td class="text-muted">${r.email}</td>
      <td>${r.date}</td>
      <td class="text-mono">${r.reading} / ${r.readingMax}</td>
      <td class="text-mono">${r.speaking} / ${r.speakingMax}</td>
      <td><strong>${totalPct}</strong></td>
      <td><span class="badge ${r.status.toLowerCase()}">${r.status}</span></td>
      <td>▶ Play</td>
      <td style="color: var(--orange); font-weight: bold;">View report</td>
    `;
    tr.addEventListener('click', () => showDetail(r));
    tbody.appendChild(tr);
  });
}

// ---- Detail panel ----
function showDetail(r) {
  document.getElementById('detailTitle').textContent =
    `Candidate Detail  —  ${r.name}  •  ${r.id}`;

  document.getElementById('readingDetail').innerHTML = `
    ${r.readingDetail.join('   ')}<br><br>
    <strong>Score: ${r.reading} / ${r.readingMax}</strong><br>
    Time taken: ${r.timeTaken}
  `;

  document.getElementById('speakingDetail').innerHTML =
    Object.entries(r.speakingScores).map(([k, v]) => {
      const max = k === 'Coherence' ? 20 : 10;
      return `${k.padEnd(15)} ${v} / ${max}`;
    }).join('<br>') + `<br><br><strong>Score: ${r.speaking} / ${r.speakingMax}</strong>`;

  document.getElementById('feedback').textContent = r.feedback;
}

// ---- Filters ----
function applyFilters() {
  const q = document.getElementById('search').value.trim().toLowerCase();
  const status = document.getElementById('statusFilter').value;
  const filtered = MOCK_RESULTS.filter(r => {
    const matchesQ = !q || r.name.toLowerCase().includes(q) || r.email.toLowerCase().includes(q);
    const matchesS = !status || r.status === status;
    return matchesQ && matchesS;
  });
  renderTable(filtered);
}

// ---- Init ----
renderKPIs(MOCK_RESULTS);
renderTable(MOCK_RESULTS);
showDetail(MOCK_RESULTS[0]);
document.getElementById('search').addEventListener('input', applyFilters);
document.getElementById('statusFilter').addEventListener('change', applyFilters);
