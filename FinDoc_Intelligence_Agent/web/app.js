/* FinDoc workspace client — upload filings, poll a project, browse facts.
 * Talks to the same routes as before: /health, /projects, /documents.
 * Fact text comes from model-read PDFs, so every interpolated string is escaped.
 */
"use strict";

const API = window.location.origin;

const els = {
  healthDot: document.getElementById("healthDot"),
  healthText: document.getElementById("healthText"),
  statDocs: document.getElementById("statDocs"),
  statFacts: document.getElementById("statFacts"),
  statFlagged: document.getElementById("statFlagged"),
  statPages: document.getElementById("statPages"),
  tabs: document.getElementById("tabs"),
  dropzone: document.getElementById("dropzone"),
  filePicker: document.getElementById("filePicker"),
  pickBtn: document.getElementById("pickBtn"),
  projectName: document.getElementById("projectName"),
  fileChips: document.getElementById("fileChips"),
  startBtn: document.getElementById("startBtn"),
  progress: document.getElementById("progress"),
  progressNote: document.getElementById("progressNote"),
  progressFill: document.getElementById("progressFill"),
  uploadNotice: document.getElementById("uploadNotice"),
  openLibraryBtn: document.getElementById("openLibraryBtn"),
  retryBtn: document.getElementById("retryBtn"),
  docSearch: document.getElementById("docSearch"),
  docList: document.getElementById("docList"),
  detail: document.getElementById("detail"),
  factSearch: document.getElementById("factSearch"),
  flaggedOnly: document.getElementById("flaggedOnly"),
  statementChips: document.getElementById("statementChips"),
};

const STATEMENT_TITLES = {
  balance_sheet: "Balance sheet",
  income_statement: "Income statement",
  cash_flow: "Cash flow",
  changes_in_equity: "Changes in equity",
  other: "Other & ratios",
};
const STATEMENT_KEYS = Object.keys(STATEMENT_TITLES);

const state = {
  picked: [],
  lastUpload: [],
  activeId: null,
  activeDoc: null,
  statementFilter: "all",
};

function escapeHtml(raw) {
  return String(raw ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[ch]);
}

function showNotice(kind, text) {
  els.uploadNotice.className = `notice ${kind}`;
  els.uploadNotice.textContent = text;
  els.uploadNotice.classList.remove("hidden");
}

function hideNotice() {
  els.uploadNotice.classList.add("hidden");
}

/* ---------- health + headline stats ---------- */

async function refreshHealth() {
  try {
    const res = await fetch(`${API}/health`);
    const ok = res.ok;
    els.healthDot.className = `health-dot ${ok ? "live" : "dead"}`;
    els.healthText.textContent = ok ? "API connected" : "API unreachable";
  } catch {
    els.healthDot.className = "health-dot dead";
    els.healthText.textContent = "API unreachable";
  }
}

function paintStats(documents) {
  const totalFacts = documents.reduce((sum, d) => sum + (d.line_item_count || 0), 0);
  const totalFlagged = documents.reduce((sum, d) => sum + (d.flagged_line_item_count || 0), 0);
  els.statDocs.textContent = String(documents.length);
  els.statFacts.textContent = totalFacts.toLocaleString();
  els.statFlagged.textContent = totalFlagged.toLocaleString();
}

/* ---------- tabs ---------- */

function switchTab(name) {
  document.querySelectorAll(".tab-btn").forEach((btn) =>
    btn.classList.toggle("active", btn.dataset.tab === name),
  );
  document.querySelectorAll(".tab-panel").forEach((panel) =>
    panel.classList.toggle("active", panel.id === `panel-${name}`),
  );
  if (name === "library") void loadLibrary();
}

els.tabs.addEventListener("click", (event) => {
  const btn = event.target.closest(".tab-btn");
  if (btn) switchTab(btn.dataset.tab);
});

/* ---------- upload ---------- */

function paintPicked() {
  els.fileChips.innerHTML = state.picked
    .map(
      (file, idx) => `
      <div class="file-chip">
        <span>${escapeHtml(file.name)} · ${(file.size / 1024 / 1024).toFixed(2)} MB</span>
        <button data-remove="${idx}" aria-label="Remove file">✕</button>
      </div>`,
    )
    .join("");
  els.startBtn.disabled = state.picked.length === 0;
}

function acceptFiles(files) {
  const pdfs = Array.from(files).filter((f) => f.name.toLowerCase().endsWith(".pdf"));
  if (pdfs.length !== Array.from(files).length) showNotice("err", "Only PDF filings are accepted; other files were skipped.");
  else hideNotice();
  state.picked = [...state.picked, ...pdfs];
  paintPicked();
}

els.pickBtn.addEventListener("click", () => els.filePicker.click());
els.filePicker.addEventListener("change", (e) => acceptFiles(e.target.files));
els.fileChips.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-remove]");
  if (btn) {
    state.picked.splice(Number(btn.dataset.remove), 1);
    paintPicked();
  }
});
["dragover", "dragenter"].forEach((evt) =>
  els.dropzone.addEventListener(evt, (e) => {
    e.preventDefault();
    els.dropzone.classList.add("over");
  }),
);
["dragleave", "drop"].forEach((evt) =>
  els.dropzone.addEventListener(evt, (e) => {
    e.preventDefault();
    els.dropzone.classList.remove("over");
  }),
);
els.dropzone.addEventListener("drop", (e) => acceptFiles(e.dataTransfer.files));

els.startBtn.addEventListener("click", async () => {
  if (!state.picked.length) return;
  hideNotice();
  els.progress.classList.remove("hidden");
  els.progressFill.style.width = "8%";
  els.progressNote.textContent = "Uploading filings…";
  els.startBtn.disabled = true;

  try {
    const body = new FormData();
    state.picked.forEach((file) => body.append("files", file));
    body.append("project_name", els.projectName.value.trim() || "FinDoc Analysis");

    const created = await fetch(`${API}/projects`, { method: "POST", body });
    if (!created.ok) throw new Error("The project could not be created.");
    const project = await created.json();
    state.lastUpload = project.documents || [];
    await watchProject(project.project_id);
  } catch (err) {
    els.progress.classList.add("hidden");
    els.startBtn.disabled = false;
    showNotice("err", err.message);
  }
});

els.retryBtn.addEventListener("click", () => {
  els.progress.classList.add("hidden");
  els.startBtn.disabled = state.picked.length === 0;
});

async function watchProject(projectId) {
  const startedAt = Date.now();
  const tickMs = 8000;
  for (let round = 0; round < 450; round += 1) {
    try {
      const res = await fetch(`${API}/projects/${projectId}/status`);
      const probe = await res.json();
      const elapsed = ((Date.now() - startedAt) / 60000).toFixed(1);
      els.progressFill.style.width = `${Math.round((probe.progress || 0) * 100)}%`;
      els.progressNote.textContent = `Status: ${probe.status} · ${elapsed} min elapsed`;

      if (probe.status === "completed") {
        els.progress.classList.add("hidden");
        const names = state.lastUpload.map((d) => d.filename).join(", ");
        showNotice("ok", names ? `Extraction complete — ${names}.` : "Extraction complete.");
        els.openLibraryBtn.classList.remove("hidden");
        return;
      }
      if (probe.status === "failed") throw new Error("Analysis failed — see server logs.");
    } catch (err) {
      if (err.message.startsWith("Analysis failed")) throw err;
      // Transient poll failure: keep waiting.
    }
    await new Promise((resolve) => setTimeout(resolve, tickMs));
  }
  els.progress.classList.add("hidden");
  els.startBtn.disabled = false;
  showNotice("err", "Still running after a long wait — it may finish later. Check the library tab.");
}

els.openLibraryBtn.addEventListener("click", async () => {
  switchTab("library");
  await loadLibrary();
  if (state.lastUpload.length) void openDocument(state.lastUpload[0].document_id);
});

/* ---------- library ---------- */

async function loadLibrary() {
  try {
    const res = await fetch(`${API}/documents`);
    const documents = await res.json();
    paintStats(documents);
    paintDocList(documents);
  } catch (err) {
    els.docList.innerHTML = `<p class="empty">Could not load filings: ${escapeHtml(err.message)}</p>`;
  }
}

function paintDocList(documents) {
  const needle = els.docSearch.value.trim().toLowerCase();
  const visible = documents.filter((d) => {
    if (!needle) return true;
    return `${d.filename} ${d.company_name || ""}`.toLowerCase().includes(needle);
  });
  if (!visible.length) {
    els.docList.innerHTML = '<p class="empty">No filings match.</p>';
    return;
  }
  els.docList.innerHTML = visible
    .map((doc) => {
      const flagged = doc.flagged_line_item_count || 0;
      return `
      <button class="doc-card ${doc.document_id === state.activeId ? "active" : ""}" data-doc="${doc.document_id}">
        <div class="doc-name">${escapeHtml(doc.filename)}</div>
        <div class="doc-sub">${doc.company_name ? escapeHtml(doc.company_name) : doc.line_item_count === 0 ? "Reading…" : "Unknown issuer"}</div>
        <div class="badge-row">
          <span class="pill accent">${doc.line_item_count || 0} facts</span>
          ${flagged ? `<span class="pill bad">${flagged} flagged</span>` : `<span class="pill good">clean</span>`}
        </div>
      </button>`;
    })
    .join("");
}

els.docList.addEventListener("click", (e) => {
  const card = e.target.closest("[data-doc]");
  if (card) void openDocument(card.dataset.doc);
});
els.docSearch.addEventListener("input", () => loadLibrary());

async function openDocument(documentId) {
  state.activeId = documentId;
  document.querySelectorAll(".doc-card").forEach((card) =>
    card.classList.toggle("active", card.dataset.doc === documentId),
  );
  els.detail.innerHTML = '<p class="empty">Loading filing…</p>';
  try {
    const res = await fetch(`${API}/documents/${documentId}`);
    if (!res.ok) throw new Error("filing not found");
    state.activeDoc = await res.json();
    paintDetail();
  } catch (err) {
    els.detail.innerHTML = `<p class="notice err">Could not open filing: ${escapeHtml(err.message)}</p>`;
  }
}

els.factSearch.addEventListener("input", paintDetail);
els.flaggedOnly.addEventListener("change", paintDetail);
els.statementChips.addEventListener("click", (e) => {
  const chip = e.target.closest("[data-filter]");
  if (!chip) return;
  state.statementFilter = chip.dataset.filter;
  document.querySelectorAll("#statementChips .chip").forEach((c) =>
    c.classList.toggle("on", c === chip),
  );
  paintDetail();
});

function filteredFacts() {
  if (!state.activeDoc) return [];
  const needle = els.factSearch.value.trim().toLowerCase();
  const flaggedOnly = els.flaggedOnly.checked;
  return (state.activeDoc.line_items || []).filter((fact) => {
    if (state.statementFilter !== "all" && fact.statement !== state.statementFilter) return false;
    if (flaggedOnly && !(fact.validation_errors || []).length) return false;
    if (!needle) return true;
    return `${fact.label} ${fact.period || ""} ${fact.currency || ""}`.toLowerCase().includes(needle);
  });
}

function paintDetail() {
  const doc = state.activeDoc;
  if (!doc) return;
  const facts = filteredFacts();
  const flaggedTotal = (doc.line_items || []).filter((f) => (f.validation_errors || []).length).length;

  els.detail.innerHTML = `
    <div class="detail-head">
      <div class="detail-title">${escapeHtml(doc.filename)}</div>
      <div class="detail-meta">
        <span><strong>Issuer:</strong> ${escapeHtml(doc.company_name || "—")}</span>
        <span><strong>Period:</strong> ${escapeHtml(doc.period || "—")}</span>
        <span><strong>Type:</strong> ${escapeHtml(doc.document_type || "—")}</span>
        <span><strong>Facts:</strong> ${facts.length} of ${(doc.line_items || []).length}</span>
        <span><strong>Flagged:</strong> ${flaggedTotal}</span>
      </div>
      <div class="toolbar">
        <a class="btn ghost" href="${API}/documents/${doc.document_id}/file" target="_blank" rel="noopener">Open PDF</a>
      </div>
    </div>
    ${renderGroups(facts)}
  `;
}

function factRow(fact) {
  const flagged = (fact.validation_errors || []).length > 0;
  return `
    <tr class="${flagged ? "is-flagged" : ""}">
      <td class="wrap">${escapeHtml(fact.label)}${flagged ? ' <span class="pill bad">review</span>' : ""}</td>
      <td class="num">${Number(fact.value).toLocaleString()}</td>
      <td>${escapeHtml(fact.currency || "")}</td>
      <td>${escapeHtml(fact.unit || "")}</td>
      <td>${fact.scale ? `<span class="pill">${escapeHtml(fact.scale)}</span>` : ""}</td>
      <td>${escapeHtml(fact.period || "")}</td>
      <td class="mono">${escapeHtml(fact.period_end_date || "")}</td>
      <td>${fact.page ?? ""}</td>
    </tr>`;
}

function factTable(rows) {
  return `
    <div class="table-scroll">
      <table class="facts">
        <thead><tr>
          <th>Label</th><th style="text-align:right">Value</th><th>Currency</th>
          <th>Unit</th><th>Scale</th><th>Period</th><th>Ends</th><th>Page</th>
        </tr></thead>
        <tbody>${rows.map(factRow).join("")}</tbody>
      </table>
    </div>`;
}

function renderBranch(node) {
  let html = "";
  if (node.rows.length) html += factTable(node.rows);
  for (const [title, child] of Object.entries(node.children)) {
    html += `<details class="branch" open><summary>${escapeHtml(title)}</summary>${renderBranch(child)}</details>`;
  }
  return html;
}

function renderGroups(facts) {
  if (!facts.length) return '<p class="empty">No facts match the current filters.</p>';
  const buckets = {};
  for (const fact of facts) {
    const key = STATEMENT_TITLES[fact.statement] ? fact.statement : "other";
    (buckets[key] = buckets[key] || []).push(fact);
  }
  return STATEMENT_KEYS.filter((k) => buckets[k]?.length)
    .map((key) => {
      const root = { rows: [], children: {} };
      for (const fact of buckets[key]) {
        let trail = fact.section_path || [];
        if (trail.length && trail[0].trim().toLowerCase() === STATEMENT_TITLES[key].toLowerCase()) {
          trail = trail.slice(1);
        }
        let node = root;
        for (const leg of trail) {
          node.children[leg] = node.children[leg] || { rows: [], children: {} };
          node = node.children[leg];
        }
        node.rows.push(fact);
      }
      return `
        <details class="group" open>
          <summary><span>${STATEMENT_TITLES[key]}</span><span class="pill accent">${buckets[key].length}</span></summary>
          <div class="group-body">${renderBranch(root)}</div>
        </details>`;
    })
    .join("");
}

/* ---------- boot ---------- */

paintPicked();
void refreshHealth();
void loadLibrary();
