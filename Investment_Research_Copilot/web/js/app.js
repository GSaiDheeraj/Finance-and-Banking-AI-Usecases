let currentPortfolioId = null;

function setStatus(msg, isError = false) {
  const el = document.getElementById('sidebar-status');
  el.hidden = false;
  el.textContent = msg;
  el.classList.toggle('error', isError);
}

function bandBadge(band) {
  const b = (band || '').toLowerCase();
  if (b === 'healthy') return 'badge-green';
  if (b === 'watch') return 'badge-amber';
  return 'badge-red';
}

function toneBadge(tone) {
  const t = (tone || 'normal').toLowerCase();
  if (t === 'stressed') return 'badge-red';
  if (t === 'elevated') return 'badge-amber';
  return 'badge-green';
}

function sevBadge(sev) {
  const s = (sev || '').toLowerCase();
  if (s === 'high' || s === 'critical') return 'badge-red';
  if (s === 'medium') return 'badge-amber';
  return 'badge-green';
}

function renderCountryResearch(countryResearch) {
  const countries = Object.keys(countryResearch || {});
  if (!countries.length) {
    return '<div class="card">No country research was run — enable it and set a country '
      + 'list to see geopolitical, sectoral, bond, FD-rate, and market findings here.</div>';
  }
  return countries.map((country) => {
    const p = countryResearch[country];
    const findingsRows = (p.findings || []).map((f) =>
      `<tr><td>${f.category}</td><td><span class="badge ${sevBadge(f.severity)}">${f.severity}</span></td><td>${f.summary || ''}</td></tr>`
    ).join('');
    const gaps = (p.data_gaps || []).map((g) => `<div class="hint">Data gap: ${g}</div>`).join('');
    return `
      <div class="card">
        <h3>${country} <span class="badge ${toneBadge(p.overall_risk_tone)}">${p.overall_risk_tone}</span></h3>
        <p>
          ${p.fd_rate_pct != null ? `FD/deposit rate: <strong>${p.fd_rate_pct.toFixed(2)}%</strong> (${p.fd_rate_basis || 'approximate'})<br>` : ''}
          ${p.bond_credit_rating ? `Bond credit rating (proxy): <strong>${p.bond_credit_rating}</strong> (${p.bond_credit_rating_basis || 'approximate'})` : ''}
        </p>
        <table><thead><tr><th>Category</th><th>Severity</th><th>Summary</th></tr></thead>
          <tbody>${findingsRows || '<tr><td colspan="3">No findings.</td></tr>'}</tbody></table>
        ${gaps}
      </div>`;
  }).join('');
}

// Tab buttons/panels are tagged data-result-type="monitoring" when they render a concept
// (allocation, alerts, rebalancing, briefing, audit) that only exists on a MonitoringResult;
// untagged tabs (overview, country research, trace) and the construction-only "positions" tab
// render either way. Switches away from a tab that's about to be hidden so the active panel
// is never one that just disappeared.
function applyTabVisibility(resultType) {
  document.querySelectorAll('.tab-btn').forEach((btn) => {
    const only = btn.dataset.resultType;
    const visible = !only || only === resultType;
    btn.hidden = !visible;
    if (!visible && btn.classList.contains('active')) {
      btn.classList.remove('active');
      document.getElementById(`tab-${btn.dataset.tab}`).classList.remove('active');
      const firstVisible = document.querySelector('.tab-btn:not([hidden])');
      if (firstVisible) {
        firstVisible.classList.add('active');
        document.getElementById(`tab-${firstVisible.dataset.tab}`).classList.add('active');
      }
    }
  });
}

function renderPositionsTable(positions) {
  const rows = (positions || []).map((p) => `
    <tr>
      <td>${p.symbol}</td>
      <td>${p.sector || p.asset_class}</td>
      <td>${p.country || '—'}</td>
      <td>${(p.target_weight * 100).toFixed(1)}%</td>
      <td>${(p.amount || 0).toLocaleString()}</td>
      <td>${p.expected_return != null ? (p.expected_return * 100).toFixed(1) + '%' : '—'}</td>
      <td>${p.rationale || ''}</td>
    </tr>`).join('');
  return `<table><thead><tr><th>Symbol</th><th>Category / Sector</th><th>Country</th><th>Weight</th><th>Amount</th><th>Exp. return</th><th>Reason</th></tr></thead>
    <tbody>${rows || '<tr><td colspan="7">No positions.</td></tr>'}</tbody></table>`;
}

function renderProsCons(positions) {
  const withReasons = (positions || []).filter((p) => (p.pros || []).length || (p.cons || []).length);
  if (!withReasons.length) return '';
  const cards = withReasons.map((p) => `
    <div class="card">
      <h4>${p.symbol}${p.name ? ' — ' + p.name : ''}</h4>
      <div class="pros-cons">
        <div><strong>Pros</strong><ul>${(p.pros || ['—']).map((x) => `<li>${x}</li>`).join('')}</ul></div>
        <div><strong>Cons</strong><ul>${(p.cons || ['—']).map((x) => `<li>${x}</li>`).join('')}</ul></div>
      </div>
    </div>`).join('');
  return `<details class="advanced-details"><summary>Per-position pros &amp; cons</summary>${cards}</details>`;
}

function renderConstruction(data) {
  const result = data.result || {};
  const cp = result.deterministic || result.llm || {};
  const fb = cp.feasibility;

  document.getElementById('headline').innerHTML = `
    <div class="metric-row">
      <div class="metric"><div class="label">Portfolio</div><div class="value">${data.portfolio_name}</div></div>
      <div class="metric"><div class="label">Method</div><div class="value">${cp.method || '—'}</div></div>
      <div class="metric"><div class="label">Invested</div><div class="value">${(cp.total_invested || 0).toLocaleString()} ${data.base_currency}</div></div>
      <div class="metric"><div class="label">Status</div><div class="value">${data.status}</div></div>
    </div>`;

  document.getElementById('tab-overview').innerHTML = `
    <div class="card">
      <h3>Summary</h3>
      <p>Mandate: <strong>${data.mandate}</strong> · Style: <strong>${cp.style || '—'}</strong> ·
         Weighting: <strong>${cp.weighting_method || '—'}</strong></p>
      <p>${cp.narrative || ''}</p>
      ${fb ? `<p class="${fb.realistic ? '' : 'error-text'}">${fb.message || ''}</p>` : ''}
    </div>`;

  document.getElementById('tab-positions').innerHTML = `
    <div class="card"><h3>Positions</h3>${renderPositionsTable(cp.positions)}</div>
    ${renderProsCons(cp.positions)}`;

  document.getElementById('tab-country-research').innerHTML = renderCountryResearch(result.country_research);
  document.getElementById('tab-trace').innerHTML = (data.reasoning || [])
    .map((step) => `<div class="trace-step">${step.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')}</div>`).join('') || '<div class="card">No trace.</div>';
}

function renderPortfolio(data) {
  document.getElementById('empty-state').hidden = true;
  document.getElementById('result-view').hidden = false;
  currentPortfolioId = data.portfolio_id;
  applyTabVisibility(data.result_type || 'monitoring');

  if (data.result_type === 'construction') {
    renderConstruction(data);
    return;
  }

  const score = data.health_score != null ? Math.round(data.health_score) : '—';
  document.getElementById('headline').innerHTML = `
    <div class="metric-row">
      <div class="metric"><div class="label">Portfolio</div><div class="value">${data.portfolio_name}</div></div>
      <div class="metric"><div class="label">Health</div><div class="value"><span class="badge ${bandBadge(data.health_band)}">${(data.health_band || data.status).toUpperCase()}</span></div></div>
      <div class="metric"><div class="label">Score</div><div class="value">${score}/100</div></div>
      <div class="metric"><div class="label">Status</div><div class="value">${data.status}</div></div>
    </div>`;

  const result = data.result || {};
  const alloc = result.allocation || {};
  const drift = result.drift || {};
  const alerts = result.alerts || [];
  const rebalance = result.rebalance || {};
  const briefing = result.briefing_llm || result.briefing_deterministic || {};

  document.getElementById('tab-overview').innerHTML = `
    <div class="card">
      <h3>Summary</h3>
      <p>Mandate: <strong>${data.mandate}</strong> · Market value: <strong>${(alloc.total_market_value || 0).toLocaleString()} ${alloc.base_currency || data.base_currency || 'USD'}</strong></p>
      <p>Drift breaches: <strong>${drift.n_breaches || 0}</strong> · Alerts: <strong>${alerts.length}</strong></p>
    </div>`;

  const driftLines = (drift.lines || []).map((l) =>
    `<tr><td>${l.bucket}</td><td>${(l.actual_weight * 100).toFixed(1)}%</td><td>${(l.target_weight * 100).toFixed(1)}%</td><td>${l.status}</td></tr>`
  ).join('');
  document.getElementById('tab-allocation').innerHTML = `
    <div class="card"><h3>Allocation vs IPS</h3>
    <table><thead><tr><th>Sleeve</th><th>Actual</th><th>Target</th><th>Status</th></tr></thead><tbody>${driftLines || '<tr><td colspan="4">No data</td></tr>'}</tbody></table></div>`;

  document.getElementById('tab-alerts').innerHTML = alerts.length
    ? alerts.map((a) => `<div class="card"><strong>[${a.severity}]</strong> ${a.title}<br><span class="hint">${a.recommended_action || ''}</span></div>`).join('')
    : '<div class="card">No alerts.</div>';

  const trades = (rebalance.trades || []).map((t) =>
    `<tr><td>${t.action}</td><td>${t.bucket}</td><td>${t.trade_value.toLocaleString()}</td><td>${t.rationale || ''}</td></tr>`
  ).join('');
  document.getElementById('tab-rebalance').innerHTML = `
    <div class="card"><h3>Rebalancing plan</h3><p>${rebalance.summary || ''}</p>
    <table><thead><tr><th>Action</th><th>Sleeve</th><th>Value</th><th>Rationale</th></tr></thead><tbody>${trades || '<tr><td colspan="4">No trades</td></tr>'}</tbody></table></div>`;

  document.getElementById('tab-briefing').innerHTML = `
    <div class="card"><h3>Briefing (${briefing.method || 'n/a'})</h3><p>${briefing.executive_summary || 'No briefing yet.'}</p></div>`;

  document.getElementById('tab-country-research').innerHTML = renderCountryResearch(result.country_research);

  document.getElementById('tab-trace').innerHTML = (data.reasoning || [])
    .map((step) => `<div class="trace-step">${step.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')}</div>`).join('') || '<div class="card">No trace.</div>';

  if (data.answer) {
    const box = document.getElementById('answer-box');
    box.hidden = false;
    box.textContent = data.answer;
  }
}

async function loadHistory() {
  const select = document.getElementById('case-history');
  try {
    const rows = await apiListPortfolios();
    select.innerHTML = '<option value="">(new portfolio)</option>';
    for (const row of rows) {
      const opt = document.createElement('option');
      opt.value = row.portfolio_id;
      const detail = row.result_type === 'construction' ? 'construction' : (row.health_band || 'pending');
      opt.textContent = `${row.portfolio_name} — ${row.status} (${detail})`;
      select.appendChild(opt);
    }
  } catch (e) {
    document.getElementById('history-error').hidden = false;
    document.getElementById('history-error').textContent = e.message;
  }
}

document.getElementById('tabs').addEventListener('click', (e) => {
  const btn = e.target.closest('.tab-btn');
  if (!btn) return;
  document.querySelectorAll('.tab-btn').forEach((b) => b.classList.remove('active'));
  document.querySelectorAll('.tab-panel').forEach((p) => p.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById(`tab-${btn.dataset.tab}`).classList.add('active');
});

document.getElementById('auto-construct-checkbox').addEventListener('change', (e) => {
  document.getElementById('auto-construct-fields').hidden = !e.target.checked;
});

// Builds the construction_params JSON from the simple fields — the advanced textarea, when
// non-empty, overrides this entirely (it's sent to the server as-is).
function buildConstructionParams() {
  const advanced = document.getElementById('construct-json-textarea').value.trim();
  if (advanced) return advanced;
  const countries = document.getElementById('construct-countries-input').value
    .split(',').map((c) => c.trim()).filter(Boolean);
  return JSON.stringify({
    mandate: document.getElementById('mandate-select').value,
    invest_amount: Number(document.getElementById('construct-amount-input').value) || 1000000,
    base_currency: document.getElementById('construct-currency-input').value || 'USD',
    geography_mode: countries.length > 1 ? 'country_list' : countries.length === 1 ? 'country_specific' : 'global',
    countries,
    style: document.getElementById('construct-style-select').value,
    enable_country_research: document.getElementById('country-research-checkbox').checked,
  });
}

// Shared by "Run" and "Rerun" — both create a run, poll it to completion, then render it.
// Kept as one function rather than duplicating this sequence per trigger.
async function launchRun(createFn, button) {
  button.disabled = true;
  setStatus('Starting monitoring run…');
  try {
    const created = await createFn();
    const done = await pollUntilDone(created.portfolio_id, (phase) => setStatus(phase));
    renderPortfolio(done);
    setStatus('Monitoring complete.');
    await loadHistory();
  } catch (e) {
    setStatus(e.message, true);
  } finally {
    button.disabled = false;
  }
}

document.getElementById('run-btn').addEventListener('click', () => {
  const holdings = document.getElementById('holdings-input').files[0];
  const transactions = document.getElementById('transactions-input').files[0];
  const statement = document.getElementById('statement-input').files[0];
  const autoConstruct = document.getElementById('auto-construct-checkbox').checked;
  if (!holdings && !statement && !autoConstruct) {
    setStatus('Upload a holdings file / PDF statement, or check "build from scratch".', true);
    return;
  }
  const runBtn = document.getElementById('run-btn');
  launchRun(() => apiCreatePortfolio(holdings, transactions, statement, {
    portfolioName: document.getElementById('portfolio-name-input').value,
    clientName: document.getElementById('client-name-input').value,
    mandate: document.getElementById('mandate-select').value,
    mode: document.getElementById('mode-select').value,
    enableNews: document.getElementById('news-checkbox').checked,
    requestedBy: document.getElementById('requested-by-input').value,
    constructionParams: autoConstruct ? buildConstructionParams() : null,
    constructOnly: autoConstruct,
  }), runBtn);
});

document.getElementById('rerun-btn').addEventListener('click', () => {
  if (!currentPortfolioId) return;
  const newsOverride = document.getElementById('rerun-news-select').value;
  const rerunBtn = document.getElementById('rerun-btn');
  launchRun(() => apiRerunPortfolio(currentPortfolioId, {
    question: document.getElementById('rerun-question-input').value.trim(),
    mandate: document.getElementById('rerun-mandate-select').value,
    enableNews: newsOverride ? newsOverride === 'on' : undefined,
  }), rerunBtn);
});

document.getElementById('load-case-btn').addEventListener('click', async () => {
  const id = document.getElementById('case-history').value;
  if (!id) return;
  try {
    renderPortfolio(await apiGetPortfolio(id));
    setStatus('Loaded past run.');
  } catch (e) {
    setStatus(e.message, true);
  }
});

document.getElementById('ask-btn').addEventListener('click', async () => {
  const q = document.getElementById('question-input').value.trim();
  if (!q || !currentPortfolioId) return;
  try {
    const answer = await apiAskQuestion(currentPortfolioId, q);
    const box = document.getElementById('answer-box');
    box.hidden = false;
    box.textContent = answer;
  } catch (e) {
    setStatus(e.message, true);
  }
});

async function submitReview(action, correctedFields) {
  if (!currentPortfolioId) return;
  const reviewer = document.getElementById('reviewer-input').value;
  const reason = document.getElementById('review-reason-input').value.trim();
  try {
    await apiSubmitReview(currentPortfolioId, reviewer, action, reason, correctedFields);
    setStatus(`Review recorded: ${action}`);
  } catch (e) {
    setStatus(e.message, true);
  }
}

document.getElementById('approve-btn').addEventListener('click', () => submitReview('approved'));
document.getElementById('reject-btn').addEventListener('click', () => submitReview('rejected'));

document.getElementById('mark-corrected-btn').addEventListener('click', () => {
  const raw = document.getElementById('corrected-fields-textarea').value.trim();
  let correctedFields = null;
  if (raw) {
    try {
      correctedFields = JSON.parse(raw);
    } catch (e) {
      setStatus(`Corrected fields must be valid JSON: ${e.message}`, true);
      return;
    }
  }
  submitReview('corrected', correctedFields);
});

document.getElementById('tabs').addEventListener('click', async (e) => {
  if (e.target.dataset.tab !== 'audit' || !currentPortfolioId) return;
  try {
    const audit = await apiGetAuditTrace(currentPortfolioId);
    document.getElementById('tab-audit').innerHTML = `<pre>${JSON.stringify(audit, null, 2)}</pre>`;
  } catch (err) {
    document.getElementById('tab-audit').innerHTML = `<div class="card">${err.message}</div>`;
  }
});

loadHistory();
