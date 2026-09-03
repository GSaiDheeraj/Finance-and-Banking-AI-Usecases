// api.js — thin fetch() wrappers, one per backend endpoint.
//
// Mirrors app_streamlit/ui.py's api_* functions deliberately: both clients talk to the
// exact same FastAPI contract (credit_risk/api/routes.py), so keeping the two call sites
// symmetric means a backend contract change is easy to apply consistently to both.

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

async function apiCreateAssessment(files, params) {
  const { ticker, companyName, question, searchRegion, enableWebSentiment, requestedBy } = params;
  const form = new FormData();
  form.append('ticker', ticker);
  form.append('company_name', companyName);
  form.append('question', question);
  if (searchRegion) form.append('search_region', searchRegion);
  form.append('enable_web_sentiment', String(enableWebSentiment));
  form.append('requested_by', requestedBy);
  for (const file of files) form.append('files', file, file.name);
  return apiRequest('/assessments', { method: 'POST', body: form });
}

function apiGetAssessment(versionId) {
  return apiRequest(`/assessments/${versionId}`);
}

function apiGetStatus(versionId) {
  return apiRequest(`/assessments/${versionId}/status`);
}

function apiListAssessments() {
  return apiRequest('/assessments');
}

async function apiAskQuestion(versionId, question) {
  const data = await apiRequest(`/assessments/${versionId}/question`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question }),
  });
  return data.answer;
}

function apiSubmitReview(versionId, reviewer, action, reason) {
  return apiRequest(`/assessments/${versionId}/review`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reviewer, action, reason: reason || null }),
  });
}

function apiRerunAssessment(versionId, instruction, enableWebSentiment) {
  return apiRequest(`/assessments/${versionId}/rerun`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ instruction: instruction || null, enable_web_sentiment: enableWebSentiment }),
  });
}

function apiGetAuditTrace(versionId) {
  return apiRequest(`/assessments/${versionId}/audit`);
}
