// app.js — DOM rendering + event wiring for the credit-risk assessment workspace.
//
// Renders the same view app_streamlit/ui.py does, from the same API response shape
// (credit_risk/api/schemas.py's AssessmentResponse, whose `assessment` field is
// CreditAssessment.model_dump(mode="json") — plain JSON, enums already serialized to
// their string value). All dynamic text is escaped before insertion: assessment content
// ultimately originates from uploaded documents and public web search results, neither
// of which this app controls, so treating it as untrusted (not raw HTML) is mandatory,
// not a stylistic choice.

let currentVersionId = null;
let currentResult = null;

// --------------------------------------------------------------------------- //
// Small DOM/formatting helpers
// --------------------------------------------------------------------------- //
function escapeHtml(value) {
  const div = document.createElement('div');
  div.textContent = value === null || value === undefined ? '' : String(value);
  return div.innerHTML;
}

function fmtNum(value, digits = 2) {
  return value === null || value === undefined ? '—' : Number(value).toFixed(digits);
}

function fmtPct(value, digits = 1) {
  return value === null || value === undefined ? '—' : `${(Number(value) * 100).toFixed(digits)}%`;
}

function titleCase(value) {
  return String(value || '').replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

function table(columns, rows) {
  if (!rows.length) return '<p class="hint">No data.</p>';
  const head = columns.map((c) => `<th>${escapeHtml(c.label)}</th>`).join('');
  const body = rows
    .map((row) => `<tr>${columns.map((c) => `<td>${c.render ? c.render(row) : escapeHtml(row[c.key])}</td>`).join('')}</tr>`)
    .join('');
  return `<div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

function list(items) {
  if (!items || !items.length) return '<ul class="plain"><li>—</li></ul>';
  return `<ul class="plain">${items.map((i) => `<li>${escapeHtml(i)}</li>`).join('')}</ul>`;
}

function bandBadgeClass(band) {
  if (band.includes('investment_grade')) return 'badge-green';
  if (band === 'sub_investment_grade') return 'badge-amber';
  return 'badge-red';
}

const DECISION_LABEL = {
  approve: 'Approve',
  approve_with_conditions: 'Approve — with conditions',
  refer_credit_committee: 'Refer to credit committee',
  decline: 'Decline',
};
const CLASS_LABEL = {
  above_75th: '🟢 Top quartile', above_median: '🟢 Above median',
  median: '⚪ Median', below_median: '🟡 Below median',
  below_25th: '🔴 Bottom quartile', not_available: '—',
};
const DIR_ICON = { improving: '📈', deteriorating: '📉', stable: '➡️', volatile: '🔀' };
const SENT_ICON = { positive: '🟢', neutral: '⚪', mixed: '🟡', negative: '🔴' };

// --------------------------------------------------------------------------- //
// Headline
// --------------------------------------------------------------------------- //
function renderHeadline(result) {
  const a = result.assessment;
  const sc = a.scorecard;
  const hardStop = sc.hard_stop_reason
    ? `<div class="alert alert-error"><strong>HARD STOP</strong> — ${escapeHtml(sc.hard_stop_reason)}</div>`
    : '';
  const parentLine = result.parent_version_id
    ? ` (rerun of <code>${escapeHtml(result.parent_version_id.slice(0, 8))}</code>)`
    : '';
  document.getElementById('headline').innerHTML = `
    ${hardStop}
    <div class="metric-row">
      <div class="metric"><div class="label">Rating grade</div><div class="value">${escapeHtml(sc.grade)}</div></div>
      <div class="metric"><div class="label">Risk band</div><div class="value"><span class="badge ${bandBadgeClass(sc.band)}">${escapeHtml(titleCase(sc.band))}</span></div></div>
      <div class="metric"><div class="label">Score</div><div class="value">${fmtNum(sc.normalized_score, 0)} / 100</div></div>
      <div class="metric"><div class="label">Decision</div><div class="value">${escapeHtml(DECISION_LABEL[sc.decision] || sc.decision)}</div></div>
      <div class="metric"><div class="label">Expected loss</div><div class="value">${fmtPct(sc.expected_loss_pct, 2)}</div></div>
    </div>
    <div class="caption">
      <strong>Status:</strong> <code>${escapeHtml(result.status)}</code>${parentLine} |
      <strong>Version:</strong> <code>${escapeHtml(result.version_id.slice(0, 8))}</code> |
      <strong>Company:</strong> ${escapeHtml(a.company.company_name || '—')} |
      <strong>Industry:</strong> ${escapeHtml(a.company.industry || '—')} (benchmarked as <code>${escapeHtml(a.benchmark.industry_used)}</code>) |
      <strong>Periods:</strong> ${escapeHtml((a.periods || []).join(', ') || '—')} |
      <strong>PD:</strong> ${fmtPct(sc.probability_of_default, 2)} | <strong>LGD:</strong> ${fmtPct(sc.loss_given_default, 0)} |
      <strong>Pricing:</strong> ${escapeHtml(sc.suggested_pricing || '—')} |
      <strong>LLM calls:</strong> ${escapeHtml(result.llm_call_count)} | <strong>Latency:</strong> ${escapeHtml(result.latency_ms)} ms
    </div>`;
}

// --------------------------------------------------------------------------- //
// Tabs
// --------------------------------------------------------------------------- //
function renderDecisionTab(a) {
  const sc = a.scorecard;
  const rows = sc.factors.map((f) => ({
    factor: titleCase(f.factor),
    present: f.present ? '✅' : '—',
    severity: f.present ? f.severity : '—',
    weight: f.weight,
    contribution: f.contribution,
    evidence: (f.evidence || []).join('; ') || '—',
  }));
  document.getElementById('tab-decision').innerHTML = `
    <div class="card">
      <h3>Credit scorecard</h3>
      ${table(
        [
          { key: 'factor', label: 'Factor' }, { key: 'present', label: 'Present' },
          { key: 'severity', label: 'Severity' }, { key: 'weight', label: 'Weight' },
          { key: 'contribution', label: 'Contribution' }, { key: 'evidence', label: 'Evidence' },
        ],
        rows,
      )}
      <div class="metric-row" style="margin-top:14px">
        <div>
          <h4>Recommended conditions</h4>
          ${list(sc.conditions)}
        </div>
        <div>
          <h4>Rating mechanics</h4>
          <p>Raw score: ${fmtNum(sc.raw_score)} → normalized ${fmtNum(sc.normalized_score, 0)}/100</p>
          <p>Grade <strong>${escapeHtml(sc.grade)}</strong> → band <strong>${escapeHtml(titleCase(sc.band))}</strong></p>
          <p>PD ${fmtPct(sc.probability_of_default, 2)} × LGD ${fmtPct(sc.loss_given_default, 0)} = EL ${fmtPct(sc.expected_loss_pct, 2)}</p>
        </div>
      </div>
    </div>`;
}

function renderRatiosTab(a) {
  const periods = a.ratios_by_period || [];
  if (!periods.length) {
    document.getElementById('tab-ratios').innerHTML = '<div class="card"><p class="hint">No ratios computed.</p></div>';
    return;
  }
  const latest = periods[periods.length - 1];
  const names = Object.keys(latest.ratios);
  const columns = [
    { key: 'name', label: 'Ratio' }, { key: 'category', label: 'Category' },
    ...periods.map((p) => ({ key: p.period, label: p.period })),
    { key: 'missing', label: 'Missing inputs' },
  ];
  const rows = names.map((name) => {
    const row = { name: titleCase(name), category: latest.ratios[name].category };
    for (const p of periods) {
      const rv = p.ratios[name];
      row[p.period] = rv && rv.value !== null ? fmtNum(rv.value) : '—';
    }
    row.missing = (latest.ratios[name].missing_inputs || []).join(', ') || '—';
    return row;
  });
  document.getElementById('tab-ratios').innerHTML = `
    <div class="card"><h3>Computed ratios by period (deterministic)</h3>${table(columns, rows)}</div>`;
}

function renderBenchmarkTab(a) {
  const b = a.benchmark;
  const gaps = b.material_gaps && b.material_gaps.length
    ? `<div class="alert alert-warning">Material gaps (bottom-quartile on key ratios): ${escapeHtml(b.material_gaps.join(', '))}</div>`
    : '';
  const rows = b.comparisons.map((c) => ({
    ratio: titleCase(c.ratio_name),
    company: fmtNum(c.company_value),
    p25: fmtNum(c.sector_p25),
    median: fmtNum(c.sector_median),
    p75: fmtNum(c.sector_p75),
    position: CLASS_LABEL[c.classification] || c.classification,
    better: c.higher_is_better ? '↑' : '↓',
  }));
  document.getElementById('tab-benchmark').innerHTML = `
    <div class="card">
      <h3>Sector benchmark — ${escapeHtml(b.industry_used)} (overall: ${escapeHtml(b.overall_position)})</h3>
      ${gaps}
      ${table(
        [
          { key: 'ratio', label: 'Ratio' }, { key: 'company', label: 'Company' },
          { key: 'p25', label: 'Sector p25' }, { key: 'median', label: 'Sector median' },
          { key: 'p75', label: 'Sector p75' }, { key: 'position', label: 'Position' },
          { key: 'better', label: 'Higher better' },
        ],
        rows,
      )}
    </div>`;
}

function renderTrendTab(a) {
  const t = a.trend;
  const notes = (t.notes || []).map((n) => `<div class="alert alert-info">${escapeHtml(n)}</div>`).join('');
  const rows = (t.trends || []).map((tr) => {
    const row = {
      ratio: titleCase(tr.ratio_name),
      direction: `${DIR_ICON[tr.direction] || ''} ${tr.direction}`,
      magnitude: tr.magnitude,
      change: tr.pct_change !== null && tr.pct_change !== undefined ? `${(tr.pct_change * 100).toFixed(1)}%` : '—',
    };
    tr.periods.forEach((p, i) => { row[p] = tr.series[i] !== null ? fmtNum(tr.series[i]) : '—'; });
    return row;
  });
  const periodCols = (t.trends || []).length
    ? t.trends[0].periods.map((p) => ({ key: p, label: p }))
    : [];
  document.getElementById('tab-trend').innerHTML = `
    <div class="card">
      <h3>Multi-period trend — overall trajectory: ${escapeHtml(t.overall_trajectory)}</h3>
      <p class="caption">Management-commentary signal (deterministic keyword read): <strong>${escapeHtml(t.commentary_signal)}</strong></p>
      ${notes}
      ${table(
        [
          { key: 'ratio', label: 'Ratio' }, { key: 'direction', label: 'Direction' },
          { key: 'magnitude', label: 'Magnitude' }, { key: 'change', label: 'Change' }, ...periodCols,
        ],
        rows,
      )}
    </div>`;
}

function renderSentimentTab(a) {
  const ws = a.web_sentiment;
  const el = document.getElementById('tab-sentiment');
  if (!ws) {
    el.innerHTML = `<div class="card"><div class="alert alert-info">No web-sentiment search ran. Add a company name in the sidebar and enable the search to include public web/news signals in the rating.</div></div>`;
    return;
  }
  const lowConfidence = ws.disambiguation_confidence === 'low'
    ? `<div class="alert alert-warning">Low confidence that these web results are about the same company — treat the sentiment signal with caution. The scorecard already discounts low-confidence web evidence.</div>`
    : '';
  const findingsRows = (ws.adverse_findings || []).map((f) => ({
    category: f.category, severity: f.severity, summary: f.summary, source: f.url || '—',
  }));
  const evidenceRows = (ws.evidence || []).map((e) => ({
    angle: e.dimension, source: e.source, title: e.title || '—', url: e.url || '—', published: e.published || '—',
  }));
  el.innerHTML = `
    <div class="card">
      <div class="metric-row">
        <div class="metric"><div class="label">Overall sentiment</div><div class="value">${SENT_ICON[ws.overall_sentiment] || ''} ${escapeHtml(titleCase(ws.overall_sentiment))}</div></div>
        <div class="metric"><div class="label">Right-company confidence</div><div class="value">${escapeHtml(titleCase(ws.disambiguation_confidence))}</div></div>
        <div class="metric"><div class="label">Evidence collected</div><div class="value">${(ws.evidence || []).length}</div></div>
      </div>
      ${lowConfidence}
      <h4>Summary</h4>
      <p>${escapeHtml(ws.sentiment_summary || '—')}</p>
      <h4>Adverse findings</h4>
      ${table([{ key: 'category', label: 'Category' }, { key: 'severity', label: 'Severity' }, { key: 'summary', label: 'Summary' }, { key: 'source', label: 'Source' }], findingsRows)}
      ${ws.positive_highlights && ws.positive_highlights.length ? `<h4>Positive highlights</h4>${list(ws.positive_highlights)}` : ''}
      <h4>Evidence trail (${(ws.evidence || []).length} items)</h4>
      ${table([{ key: 'angle', label: 'Angle' }, { key: 'source', label: 'Source' }, { key: 'title', label: 'Title' }, { key: 'url', label: 'URL' }, { key: 'published', label: 'Published' }], evidenceRows)}
      ${ws.data_gaps && ws.data_gaps.length ? `<p class="caption">Data gaps: ${escapeHtml(ws.data_gaps.join('; '))}</p>` : ''}
    </div>`;
}

function renderMemoTab(a, result) {
  const m = a.memo;
  const answerBlock = result.answer
    ? `<h4>Answer to your question</h4><p>${escapeHtml(result.answer)}</p>`
    : '';
  document.getElementById('tab-memo').innerHTML = `
    <div class="card">
      <h3>Credit memo (grounded)</h3>
      <h4>Executive summary</h4><p>${escapeHtml(m.executive_summary || '—')}</p>
      <h4>Financial analysis</h4><p>${escapeHtml(m.financial_analysis || '—')}</p>
      <div class="metric-row">
        <div><h4>Key strengths</h4>${list(m.key_strengths)}<h4>Mitigants</h4>${list(m.mitigants)}</div>
        <div><h4>Key risks</h4>${list(m.key_risks)}<h4>Monitoring triggers</h4>${list(m.monitoring_triggers)}</div>
      </div>
      <h4>Recommended covenants</h4>${list(m.recommended_covenants)}
      ${answerBlock}
      <h4>Ask a follow-up question</h4>
      <form id="ask-question-form" class="inline-form">
        <input type="text" id="follow-up-input" placeholder="Question">
        <button type="submit" class="secondary">Ask</button>
      </form>
    </div>`;
  document.getElementById('ask-question-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const input = document.getElementById('follow-up-input');
    const question = input.value.trim();
    if (!question) return;
    setSidebarStatus('Answering...', false);
    try {
      const answer = await apiAskQuestion(currentVersionId, question);
      currentResult.answer = answer;
      renderMemoTab(currentResult.assessment, currentResult);
      setSidebarStatus('', false, true);
    } catch (err) {
      setSidebarStatus(`Question failed: ${err.message}`, true);
    }
  });
}

function sourceLabel(li) {
  if ((li.source_snippet || '').startsWith('yfinance:')) return `🌐 ${li.source_snippet}`;
  return li.page ? `📄 p.${li.page}` : '📄 doc';
}

function renderDataTab(a) {
  const items = a.line_items || [];
  const nYf = items.filter((li) => (li.source_snippet || '').startsWith('yfinance:')).length;
  const rows = items.map((li) => ({
    label: li.label, standardised: li.standardised_label, value: li.value, unit: li.unit,
    currency: li.currency || '—', period: li.period,
    period_end: li.period_end_date || '—', length: li.period_length_months || '—',
    statement: li.statement_type, source: sourceLabel(li),
  }));
  const notesRows = (a.notes || []).map((n) => ({
    category: n.category, description: n.description, amount: n.amount, currency: n.currency || '—', page: n.page,
  }));
  const facility = a.facility;
  const facilityBlock = facility.facility_type || facility.amount
    ? `<h4>Facility request</h4><p>${escapeHtml(facility.facility_type || '—')} — ${escapeHtml(facility.amount || '—')} ${escapeHtml(facility.currency || '')} — ${escapeHtml(facility.purpose || '—')} (${escapeHtml(facility.tenor || 'tenor n/a')})</p>`
    : '';
  const rec = a.reconciliation;
  let reconBlock = '';
  if (rec && rec.yfinance_attempted) {
    reconBlock = `
      <div class="card">
        <h3>🔗 Data reconciliation (yfinance fallback)</h3>
        <div class="metric-row">
          <div class="metric"><div class="label">Filled from yfinance</div><div class="value">${rec.cells_filled_from_yfinance}</div></div>
          <div class="metric"><div class="label">Doc vs yfinance mismatches</div><div class="value">${(rec.discrepancies || []).length}</div></div>
          <div class="metric"><div class="label">Ratios still missing inputs</div><div class="value">${(rec.ratio_gaps || []).length}</div></div>
        </div>
        ${(rec.notes || []).map((n) => `<div class="alert alert-warning">${escapeHtml(n)}</div>`).join('')}
        ${rec.discrepancies && rec.discrepancies.length ? `<h4>Discrepancies (document value kept; flagged for review)</h4>${list(rec.discrepancies)}` : ''}
        ${rec.ratio_gaps && rec.ratio_gaps.length ? `<h4>Ratios that could not be computed</h4>${list(rec.ratio_gaps)}` : ''}
      </div>`;
  }
  document.getElementById('tab-data').innerHTML = `
    <div class="card">
      <h3>Extracted line items</h3>
      ${nYf ? `<p class="caption">${nYf} line item(s) were filled from the yfinance fallback (🌐); the rest came from the uploaded documents (📄).</p>` : ''}
      ${table(
        [
          { key: 'label', label: 'Label' }, { key: 'standardised', label: 'Standardised' },
          { key: 'value', label: 'Value' }, { key: 'unit', label: 'Unit' }, { key: 'currency', label: 'Currency' },
          { key: 'period', label: 'Period' }, { key: 'period_end', label: 'Period end' }, { key: 'length', label: 'Length (mo.)' },
          { key: 'statement', label: 'Statement' }, { key: 'source', label: 'Source' },
        ],
        rows,
      )}
      <h4>Off-balance-sheet / contingencies</h4>
      ${table([{ key: 'category', label: 'Category' }, { key: 'description', label: 'Description' }, { key: 'amount', label: 'Amount' }, { key: 'currency', label: 'Currency' }, { key: 'page', label: 'Page' }], notesRows)}
      ${facilityBlock}
    </div>
    ${reconBlock}`;
}

function renderTraceTab(result) {
  const steps = result.reasoning || [];
  const html = steps.length
    ? steps.map((step, i) => `<div class="trace-step"><div class="step-label">Step ${i + 1}</div><div>${escapeHtml(step)}</div></div>`).join('')
    : '<p class="hint">No trace recorded.</p>';
  document.getElementById('tab-trace').innerHTML = `<div class="card">${html}</div>`;
}

function renderReviewTab(a, result) {
  document.getElementById('tab-review').innerHTML = `
    <div class="card">
      <h3>Analyst / credit-committee review</h3>
      <p class="hint">FR-8.2: a draft stays a draft until an authorized reviewer approves it. A correction is recorded alongside the original — it doesn't silently replace history — and only takes effect once you rerun the case.</p>
      <form id="review-form" class="inline-form">
        <input type="text" id="reviewer-input" placeholder="Reviewer name" value="local-analyst">
        <select id="review-action">
          <option value="approved">approved</option>
          <option value="corrected">corrected</option>
          <option value="rejected">rejected</option>
        </select>
        <textarea id="review-reason" placeholder="Reason / notes"></textarea>
        <button type="submit" class="secondary">Submit review</button>
      </form>
    </div>
    <div class="card">
      <h3>Rerun with a correction</h3>
      <p class="hint">Reprocesses this case's own documents with an updated instruction — creates a NEW version; this one is never overwritten.</p>
      <form id="rerun-form" class="inline-form">
        <textarea id="rerun-instruction" placeholder="What changed / what to correct"></textarea>
        <label class="checkbox-row">
          <input type="checkbox" id="rerun-web-checkbox" ${a.web_sentiment ? 'checked' : ''}>
          Re-run web-sentiment search too
        </label>
        <button type="submit" class="primary">Rerun assessment</button>
      </form>
    </div>`;

  document.getElementById('review-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const reviewer = document.getElementById('reviewer-input').value.trim() || 'anonymous';
    const action = document.getElementById('review-action').value;
    const reason = document.getElementById('review-reason').value.trim();
    try {
      await apiSubmitReview(currentVersionId, reviewer, action, reason);
      setSidebarStatus(`Review recorded: ${action}.`, false, true);
      currentResult = await apiGetAssessment(currentVersionId);
      renderResult(currentResult);
    } catch (err) {
      setSidebarStatus(`Review failed: ${err.message}`, true);
    }
  });

  document.getElementById('rerun-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const instruction = document.getElementById('rerun-instruction').value.trim();
    const rerunWeb = document.getElementById('rerun-web-checkbox').checked;
    try {
      const created = await apiRerunAssessment(currentVersionId, instruction, rerunWeb);
      currentVersionId = created.version_id;
      await runWithPolling(async () => created, 'Rerunning...');
    } catch (err) {
      setSidebarStatus(`Rerun failed: ${err.message}`, true);
    }
  });
}

async function renderAuditTab(versionId) {
  const el = document.getElementById('tab-audit');
  el.innerHTML = '<div class="card"><p class="hint">Loading...</p></div>';
  try {
    const audit = await apiGetAuditTrace(versionId);
    const docRows = audit.documents.map((d) => ({ filename: d.filename, sha256: d.sha256_hash, pages: d.page_count }));
    const factorRows = audit.rating_factors.map((f) => ({
      factor: titleCase(f.factor_type), present: f.present ? '✅' : '—', severity: f.severity,
      weight: f.weight, contribution: f.contribution, evidence: (f.evidence || []).join('; ') || '—',
    }));
    const reviewRows = audit.reviews.map((r) => ({
      reviewer: r.reviewer, action: r.action, reason: r.reason || '—', reviewed_at: r.reviewed_at,
    }));
    el.innerHTML = `
      <div class="card">
        <h3>Audit trail</h3>
        <p class="caption">
          <strong>Model:</strong> ${escapeHtml(audit.model_name || '—')} |
          <strong>Embedding model:</strong> ${escapeHtml(audit.embed_model_name || '—')} |
          <strong>Requested by:</strong> ${escapeHtml(audit.requested_by)} |
          <strong>Requested at:</strong> ${escapeHtml(audit.requested_at)}
        </p>
        <h4>Source documents</h4>
        ${table([{ key: 'filename', label: 'Filename' }, { key: 'sha256', label: 'SHA-256' }, { key: 'pages', label: 'Pages' }], docRows)}
        <h4>Rating factors</h4>
        ${table([{ key: 'factor', label: 'Factor' }, { key: 'present', label: 'Present' }, { key: 'severity', label: 'Severity' }, { key: 'weight', label: 'Weight' }, { key: 'contribution', label: 'Contribution' }, { key: 'evidence', label: 'Evidence' }], factorRows)}
        <h4>Review history</h4>
        ${table([{ key: 'reviewer', label: 'Reviewer' }, { key: 'action', label: 'Action' }, { key: 'reason', label: 'Reason' }, { key: 'reviewed_at', label: 'Reviewed at' }], reviewRows)}
      </div>`;
  } catch (err) {
    el.innerHTML = `<div class="card"><div class="alert alert-error">Can't load audit trace: ${escapeHtml(err.message)}</div></div>`;
  }
}

// --------------------------------------------------------------------------- //
// Top-level result rendering + tab switching
// --------------------------------------------------------------------------- //
function renderResult(result) {
  currentResult = result;
  currentVersionId = result.version_id;
  document.getElementById('empty-state').hidden = true;
  document.getElementById('result-view').hidden = false;

  if (!result.assessment) {
    document.getElementById('result-view').innerHTML =
      `<div class="alert alert-error">Assessment ${escapeHtml(result.version_id)} ended with status
       <code>${escapeHtml(result.status)}</code> and produced no result — check the API server log.</div>`;
    return;
  }

  const a = result.assessment;
  renderHeadline(result);
  renderDecisionTab(a);
  renderRatiosTab(a);
  renderBenchmarkTab(a);
  renderTrendTab(a);
  renderSentimentTab(a);
  renderMemoTab(a, result);
  renderDataTab(a);
  renderTraceTab(result);
  renderReviewTab(a, result);
  renderAuditTab(result.version_id);
}

function switchTab(tabName) {
  document.querySelectorAll('.tab-btn').forEach((btn) => btn.classList.toggle('active', btn.dataset.tab === tabName));
  document.querySelectorAll('.tab-panel').forEach((panel) => panel.classList.toggle('active', panel.id === `tab-${tabName}`));
}

function setSidebarStatus(message, isError, autoHide = false) {
  const banner = document.getElementById('sidebar-status');
  if (!message) { banner.hidden = true; return; }
  banner.hidden = false;
  banner.textContent = message;
  banner.classList.toggle('error', !!isError);
  if (autoHide) setTimeout(() => { banner.hidden = true; }, 4000);
}

// --------------------------------------------------------------------------- //
// Case history (reopen a past assessment)
// --------------------------------------------------------------------------- //
async function loadCaseHistory() {
  const select = document.getElementById('case-history');
  try {
    const cases = await apiListAssessments();
    select.innerHTML = '<option value="">(new assessment)</option>' + cases.map((c) => {
      const label = `${c.company_name || c.ticker || 'Unnamed'} — ${c.status} (${c.version_id.slice(0, 8)})`;
      return `<option value="${escapeHtml(c.version_id)}">${escapeHtml(label)}</option>`;
    }).join('');
  } catch (err) {
    const errorEl = document.getElementById('history-error');
    errorEl.hidden = false;
    errorEl.textContent = `Can't reach the API: ${err.message}`;
  }
}

// --------------------------------------------------------------------------- //
// Running / polling
// --------------------------------------------------------------------------- //
async function runWithPolling(startFn, initialMessage) {
  const runButton = document.getElementById('run-btn');
  runButton.disabled = true;
  setSidebarStatus(initialMessage, false);
  try {
    const created = await startFn();
    currentVersionId = created.version_id;
    const finalResult = await pollUntilDone(created.version_id, (label) => setSidebarStatus(`⏳ ${label}...`, false));
    setSidebarStatus('', false);
    renderResult(finalResult);
  } catch (err) {
    setSidebarStatus(`Failed: ${err.message}`, true);
  } finally {
    runButton.disabled = false;
  }
}

// Restores the sidebar form to what was actually submitted for a past case. File
// inputs are the one exception — browsers don't allow JS to populate a <input
// type="file">'s selection (a deliberate security restriction, not an oversight here),
// so the original filenames are shown as read-only text instead of re-attached.
function restoreSidebarInputs(result) {
  document.getElementById('ticker-input').value = result.ticker || '';
  document.getElementById('company-input').value = result.company_name || '';
  document.getElementById('region-input').value = result.search_region || '';
  document.getElementById('web-sentiment-checkbox').checked = result.enable_web_sentiment;
  document.getElementById('requested-by-input').value = result.requested_by || 'local-analyst';

  const note = document.getElementById('previous-documents-note');
  if (result.documents && result.documents.length) {
    note.hidden = false;
    note.textContent = `Previously submitted: ${result.documents.join(', ')} — ` +
      `re-upload only if rerunning with different files (browsers can't refill a file picker).`;
  } else {
    note.hidden = true;
  }
}

async function loadExistingAssessment(versionId) {
  setSidebarStatus('Loading...', false);
  try {
    let result = await apiGetAssessment(versionId);
    if (result.status === 'created' || result.status === 'processing') {
      result = await pollUntilDone(versionId, (label) => setSidebarStatus(`⏳ ${label}...`, false));
    }
    setSidebarStatus('', false);
    restoreSidebarInputs(result);
    renderResult(result);
  } catch (err) {
    setSidebarStatus(`Can't load assessment: ${err.message}`, true);
  }
}

// --------------------------------------------------------------------------- //
// Wiring
// --------------------------------------------------------------------------- //
document.getElementById('tabs').addEventListener('click', (event) => {
  const btn = event.target.closest('.tab-btn');
  if (btn) switchTab(btn.dataset.tab);
});

document.getElementById('load-case-btn').addEventListener('click', () => {
  const versionId = document.getElementById('case-history').value;
  if (versionId) loadExistingAssessment(versionId);
});

document.getElementById('run-btn').addEventListener('click', () => {
  const files = document.getElementById('file-input').files;
  const ticker = document.getElementById('ticker-input').value.trim();
  if (!files.length && !ticker) {
    setSidebarStatus('Upload at least one PDF or enter a ticker.', true);
    return;
  }
  const params = {
    ticker,
    companyName: document.getElementById('company-input').value.trim(),
    // No upfront question: the assessment is comprehensive regardless of one, and
    // there's already a dedicated "ask a follow-up question" form once results are in
    // (see the Memo tab) — asking one before running just added friction for no gain.
    question: '',
    searchRegion: document.getElementById('region-input').value.trim() || null,
    enableWebSentiment: document.getElementById('web-sentiment-checkbox').checked,
    requestedBy: document.getElementById('requested-by-input').value.trim() || 'local-analyst',
  };
  runWithPolling(() => apiCreateAssessment(files, params), 'Starting assessment...');
});

loadCaseHistory();
