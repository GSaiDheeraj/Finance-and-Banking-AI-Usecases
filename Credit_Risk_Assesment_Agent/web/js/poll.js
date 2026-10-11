// poll.js — poll the cheap /status endpoint until a background job reaches a terminal
// state. Mirrors app_streamlit/ui.py's poll_until_done: the wall-clock wait for the
// analyst is unchanged, but no single HTTP request stays open longer than one poll tick,
// which is what actually avoids browser/proxy/gateway idle timeouts on a job that can
// run for minutes (see credit_risk/api/routes.py's module docstring for why).

const TERMINAL_STATUSES = new Set(['awaiting_review', 'validation_review', 'approved', 'rejected']);

const PHASE_LABEL = {
  queued: 'Queued',
  indexing_documents: 'Indexing documents',
  extracting_financials: 'Extracting financials',
  enriching_and_scoring: 'Enriching data and scoring',
  writing_memo: 'Writing the credit memo',
};

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function pollUntilDone(versionId, onProgress) {
  for (;;) {
    const status = await apiGetStatus(versionId);
    if (TERMINAL_STATUSES.has(status.status)) {
      return apiGetAssessment(versionId);
    }
    if (onProgress) {
      onProgress(PHASE_LABEL[status.phase] || status.status);
    }
    await sleep(2000);
  }
}
