const TERMINAL_STATUSES = new Set(['awaiting_review', 'validation_review', 'approved', 'rejected']);

const PHASE_LABEL = {
  queued: 'Queued',
  ingesting_holdings: 'Ingesting holdings',
  persisting_results: 'Saving results',
};

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function pollUntilDone(portfolioId, onProgress) {
  for (;;) {
    const status = await apiGetStatus(portfolioId);
    if (TERMINAL_STATUSES.has(status.status)) {
      return apiGetPortfolio(portfolioId);
    }
    if (onProgress) {
      onProgress(PHASE_LABEL[status.phase] || status.phase || status.status);
    }
    await sleep(2000);
  }
}
