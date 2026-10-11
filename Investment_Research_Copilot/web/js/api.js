const API_BASE_URL =
  document.querySelector('meta[name="api-base-url"]')?.content || '';

async function apiRequest(path, options = {}) {
  const response = await fetch(`${API_BASE_URL}${path}`, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `${response.status} ${response.statusText}`);
  }
  return response.json();
}

async function apiCreatePortfolio(holdingsFile, transactionsFile, statementFile, params) {
  const form = new FormData();
  form.append('portfolio_name', params.portfolioName);
  form.append('client_name', params.clientName);
  form.append('mandate', params.mandate);
  form.append('mode', params.mode);
  form.append('enable_news', String(params.enableNews));
  form.append('requested_by', params.requestedBy);
  if (holdingsFile) form.append('holdings_files', holdingsFile, holdingsFile.name);
  if (transactionsFile) form.append('transactions_file', transactionsFile, transactionsFile.name);
  if (statementFile) form.append('statement_file', statementFile, statementFile.name);
  // Builds a portfolio from scratch server-side when no holdings/statement is attached — see
  // ConstructionRequest in portfolio_monitor/schemas.py for every field this JSON accepts.
  if (params.constructionParams) form.append('construction_params', params.constructionParams);
  // A genuine from-scratch build: keeps the full ConstructionResult (rationale, pros/cons,
  // feasibility, country research) instead of flattening it into a monitored book, which
  // discards all of that — see api/routes.py::create_portfolio's construct_only param.
  if (params.constructOnly) form.append('construct_only', 'true');
  return apiRequest('/portfolios', { method: 'POST', body: form });
}

function apiGetPortfolio(portfolioId) {
  return apiRequest(`/portfolios/${portfolioId}`);
}

function apiGetStatus(portfolioId) {
  return apiRequest(`/portfolios/${portfolioId}/status`);
}

function apiListPortfolios() {
  return apiRequest('/portfolios');
}

async function apiAskQuestion(portfolioId, question) {
  const data = await apiRequest(`/portfolios/${portfolioId}/question`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question }),
  });
  return data.answer;
}

function apiSubmitReview(portfolioId, reviewer, action, reason, correctedFields) {
  return apiRequest(`/portfolios/${portfolioId}/review`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      reviewer,
      action,
      reason: reason || null,
      corrected_fields: correctedFields || null,
    }),
  });
}

function apiGetAuditTrace(portfolioId) {
  return apiRequest(`/portfolios/${portfolioId}/audit`);
}

function apiRerunPortfolio(portfolioId, { question, mandate, enableNews }) {
  return apiRequest(`/portfolios/${portfolioId}/rerun`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question: question || null, mandate: mandate || null, enable_news: enableNews }),
  });
}
