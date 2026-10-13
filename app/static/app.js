/**
 * GrantLoom — Frontend Application Logic (Vanilla JS / ES Modules)
 *
 * Implements:
 * - IndexedDB storage for credentials and settings
 * - Live validation probe & interaction lockout
 * - Ingestion file upload with outbox polling progress
 * - SSE streaming query runner with reasoning trail
 * - Interactive slide-over citation drawer with keyword highlighting
 * - Compliance Matrix auditor & Excel export download
 * - Server-mode / data-egress display, model diagnostics, and demo dataset generator
 */

// ==============================================================================
// 1. IndexedDB Persistence Layer
// ==============================================================================

const DB_NAME = "grantloom_db";
const DB_VERSION = 1;

function openDatabase() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = (event) => {
      const db = event.target.result;
      if (!db.objectStoreNames.contains("credentials")) {
        db.createObjectStore("credentials", { keyPath: "id" });
      }
      if (!db.objectStoreNames.contains("settings")) {
        db.createObjectStore("settings", { keyPath: "id" });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function getStoredCredential(key) {
  try {
    const db = await openDatabase();
    return new Promise((resolve) => {
      const tx = db.transaction("credentials", "readonly");
      const store = tx.objectStore("credentials");
      const req = store.get(key);
      req.onsuccess = () => resolve(req.result ? req.result.value : null);
      req.onerror = () => resolve(null);
    });
  } catch (err) {
    console.error("IndexedDB read error:", err);
    return null;
  }
}

async function setStoredCredential(key, value) {
  try {
    const db = await openDatabase();
    return new Promise((resolve, reject) => {
      const tx = db.transaction("credentials", "readwrite");
      const store = tx.objectStore("credentials");
      const req = store.put({ id: key, value });
      req.onsuccess = () => resolve();
      req.onerror = () => reject(req.error);
    });
  } catch (err) {
    console.error("IndexedDB write error:", err);
  }
}


// ==============================================================================
// 2. Application State, DOM References & Authenticated Fetch
// ==============================================================================

const state = {
  apiKey: "", // OpenRouter BYOK key (sent as X-OpenRouter-Key)
  masterKey: "", // server access key (sent as X-API-Key)
  info: null, // GET /api/v1/info: server mode, provider, egress destination
  documents: [],
  activeSolicitationId: null,
  activeQueryKeywords: [],
  lastCitations: {},
};

const elements = {
  providerBadge: document.getElementById("provider-badge"),
  providerStatusText: document.getElementById("provider-status-text"),
  lockoutBanner: document.getElementById("lockout-banner"),
  btnBannerSettings: document.getElementById("btn-banner-settings"),
  btnOpenSettings: document.getElementById("btn-open-settings"),
  btnCloseSettings: document.getElementById("btn-close-settings"),
  btnCancelSettings: document.getElementById("btn-cancel-settings"),
  btnSaveSettings: document.getElementById("btn-save-settings"),
  settingsModal: document.getElementById("settings-modal"),

  tabDocuments: document.getElementById("tab-documents"),
  tabQa: document.getElementById("tab-qa"),
  tabCompliance: document.getElementById("tab-compliance"),
  viewDocuments: document.getElementById("view-documents"),
  viewQa: document.getElementById("view-qa"),
  viewCompliance: document.getElementById("view-compliance"),
  docCountBadge: document.getElementById("doc-count-badge"),

  dropzone: document.getElementById("dropzone"),
  fileInput: document.getElementById("file-input"),
  progressContainer: document.getElementById("ingest-progress-container"),
  progressBar: document.getElementById("ingest-progress-bar"),
  progressStageText: document.getElementById("progress-stage-text"),
  progressPercentText: document.getElementById("progress-percent-text"),
  documentsTableBody: document.getElementById("documents-table-body"),
  btnRefreshDocs: document.getElementById("btn-refresh-docs"),

  chatForm: document.getElementById("chat-form"),
  chatInput: document.getElementById("chat-input"),
  btnSubmitAsk: document.getElementById("btn-submit-ask"),
  chatTranscript: document.getElementById("chat-transcript"),
  reasoningTrail: document.getElementById("reasoning-trail"),
  starterPromptsList: document.getElementById("starter-prompts-list"),

  citationDrawer: document.getElementById("citation-drawer"),
  drawerBackdrop: document.getElementById("drawer-backdrop"),
  btnCloseDrawer: document.getElementById("btn-close-drawer"),
  drawerFilename: document.getElementById("drawer-filename"),
  drawerCoord: document.getElementById("drawer-coord"),
  drawerChunkId: document.getElementById("drawer-chunk-id"),
  drawerExcerptText: document.getElementById("drawer-excerpt-text"),

  selectSolicitation: document.getElementById("select-solicitation"),
  btnRunCompliance: document.getElementById("btn-run-compliance"),
  complianceProgressContainer: document.getElementById("compliance-progress-container"),
  complianceProgressBar: document.getElementById("compliance-progress-bar"),
  complianceProgressText: document.getElementById("compliance-progress-text"),
  complianceProgressPct: document.getElementById("compliance-progress-pct"),
  btnExportExcel: document.getElementById("btn-export-excel"),
  readinessCard: document.getElementById("readiness-card"),
  readinessScore: document.getElementById("readiness-score"),
  readinessHeadline: document.getElementById("readiness-headline"),
  readinessBreakdown: document.getElementById("readiness-breakdown"),
  complianceTableBody: document.getElementById("compliance-table-body"),

  btnGenerateDemo: document.getElementById("btn-generate-demo"),
  btnModalDemoData: document.getElementById("btn-modal-demo-data"),

  serverModeInfo: document.getElementById("server-mode-info"),
  btnCheckModels: document.getElementById("btn-check-models"),
  modelsProbeResult: document.getElementById("models-probe-result"),
  inputMasterKey: document.getElementById("input-master-key"),
  panelOpenRouter: document.getElementById("panel-openrouter"),
  inputOpenRouterKey: document.getElementById("input-openrouter-key"),
  btnToggleKeyVisibility: document.getElementById("btn-toggle-key-visibility"),
  btnTestKey: document.getElementById("btn-test-key"),
  keyProbeResult: document.getElementById("key-probe-result"),
};

/** fetch() with the server access key and (when present) the BYOK key attached. */
function apiFetch(url, options = {}) {
  const headers = new Headers(options.headers || {});
  if (state.masterKey) headers.set("X-API-Key", state.masterKey);
  if (state.apiKey) headers.set("X-OpenRouter-Key", state.apiKey);
  return fetch(url, { ...options, headers });
}

async function problemDetail(res) {
  try {
    const body = await res.json();
    return body.detail || body.title || `HTTP ${res.status}`;
  } catch (e) {
    return `HTTP ${res.status}`;
  }
}

// ==============================================================================
// 3. Credentials, Server Mode & Interaction Lockout
// ==============================================================================

async function initCredentials() {
  state.apiKey = (await getStoredCredential("openrouter_api_key")) || "";
  state.masterKey = (await getStoredCredential("master_api_key")) || "";
  elements.inputOpenRouterKey.value = state.apiKey;
  elements.inputMasterKey.value = state.masterKey;
  await loadServerInfo();
}

async function loadServerInfo() {
  try {
    const res = await apiFetch("/api/v1/info");
    if (res.status === 401) {
      state.info = null;
      setBadge("chip-warning", "🔒 Server access key required");
      elements.serverModeInfo.textContent = await problemDetail(res);
      setLockout(true);
      return;
    }
    state.info = await res.json();
  } catch (err) {
    setBadge("chip-warning", "⚠️ Server unreachable");
    setLockout(true);
    return;
  }

  const info = state.info;
  elements.panelOpenRouter.classList.toggle("hidden", !info.requires_byok_key);
  elements.serverModeInfo.textContent =
    `Mode: ${info.mode === "private" ? "private (local models)" : "BYOK (cloud)"} · ` +
    `writer ${info.writer_model} · judge ${info.judge_model} via ${info.provider} (${info.destination}) · ` +
    `decisions: ${info.decision_backend}${info.decision_fallback ? " → " + info.decision_fallback : ""} · ` +
    `embeddings: ${info.embeddings}`;

  const needsKey = info.requires_byok_key && !(state.apiKey && state.apiKey.trim().length > 10);
  if (needsKey) {
    setBadge("chip-warning", "⚠️ OpenRouter key required");
  } else if (info.data_leaves_machine) {
    setBadge("chip-warning", `☁️ Data egress: ${info.destination}`);
  } else {
    setBadge("chip-success", `🟢 Local only (${info.destination})`);
  }
  setLockout(needsKey);
}

function setBadge(chipClass, text) {
  elements.providerBadge.className = `status-chip ${chipClass}`;
  elements.providerStatusText.textContent = text;
}

function setLockout(locked) {
  elements.lockoutBanner.classList.toggle("hidden", !locked);
  elements.dropzone.classList.toggle("disabled", locked);
  elements.fileInput.disabled = locked;
  elements.chatInput.disabled = locked;
  elements.btnSubmitAsk.disabled = locked;
  elements.chatInput.placeholder = locked
    ? "Configure the required keys in Settings to ask questions..."
    : "Ask a grant question (e.g. 'What is the total travel budget?')";
}

// ==============================================================================
// 4. View / Tab Navigation
// ==============================================================================

function switchTab(tabName) {
  const tabs = [
    { name: "documents", btn: elements.tabDocuments, view: elements.viewDocuments },
    { name: "qa", btn: elements.tabQa, view: elements.viewQa },
    { name: "compliance", btn: elements.tabCompliance, view: elements.viewCompliance },
  ];
  tabs.forEach((t) => {
    const active = t.name === tabName;
    t.btn.classList.toggle("active", active);
    t.btn.setAttribute("aria-selected", String(active));
    t.view.classList.toggle("hidden", !active);
  });
}

// ==============================================================================
// 5. Document Ingestion & Repository
// ==============================================================================

const PROCESSING_STATES = ["uploaded", "extracting", "chunking", "scanning", "embedding", "indexing", "deleting"];

async function fetchDocuments() {
  try {
    const res = await apiFetch("/api/v1/documents");
    if (!res.ok) return;
    const data = await res.json();
    state.documents = data.documents;
    renderDocumentsTable(data.documents);
    updateSolicitationDropdown(data.documents);
    elements.docCountBadge.textContent = data.total;
  } catch (err) {
    console.error("Failed to fetch documents:", err);
  }
}

function renderDocumentsTable(docs) {
  if (!docs || docs.length === 0) {
    elements.documentsTableBody.innerHTML = `
      <tr><td colspan="6" class="empty-state">No documents indexed yet. Upload a document or click "Generate Demo Data" above.</td></tr>`;
    return;
  }
  elements.documentsTableBody.innerHTML = docs
    .map((doc) => {
      let statusClass = "badge-active";
      if (PROCESSING_STATES.includes(doc.state)) statusClass = "badge-processing";
      else if (doc.state === "quarantined" || doc.state === "failed") statusClass = "badge-quarantined";
      const progress = PROCESSING_STATES.includes(doc.state) && doc.progress_pct != null ? ` ${doc.progress_pct}%` : "";
      return `
        <tr>
          <td><strong>${escapeHtml(doc.filename)}</strong>${doc.version > 1 ? ` <span class="badge">v${doc.version}</span>` : ""}</td>
          <td><span class="badge">${escapeHtml(doc.file_type.toUpperCase())}</span></td>
          <td>${doc.page_count || 1} pages</td>
          <td><span class="badge-status ${statusClass}">${escapeHtml(doc.stage || doc.state)}${progress}</span></td>
          <td>${doc.created_at ? new Date(doc.created_at).toLocaleDateString() : "--"}</td>
          <td>
            <button class="btn btn-sm btn-secondary" onclick="window.viewDocDetails('${doc.doc_id}')">Details</button>
            <button class="btn btn-sm btn-secondary" onclick="window.deleteDocument('${doc.doc_id}')">Delete</button>
          </td>
        </tr>`;
    })
    .join("");
}

window.viewDocDetails = function (docId) {
  const doc = state.documents.find((d) => d.doc_id === docId);
  if (!doc) return;
  const scan = (doc.metadata && doc.metadata.scan) || {};
  const lines = [
    `${doc.filename} (version ${doc.version}) — ${doc.state}`,
    `Chunks: ${doc.metadata.chunk_count ?? "--"} · Pages: ${doc.page_count}`,
    `Injection scan: ${(scan.tainted_chunks || []).length} tainted chunk(s); Tier 2: ${scan.tier2_status || "n/a"}`,
  ];
  if (doc.error_message) lines.push(`Error: ${doc.error_message}`);
  alert(lines.join("\n"));
};

window.deleteDocument = async function (docId) {
  if (!confirm("Delete this document and all of its index entries?")) return;
  const res = await apiFetch(`/api/v1/documents/${docId}`, { method: "DELETE" });
  if (!res.ok) alert(`Delete failed: ${await problemDetail(res)}`);
  await fetchDocuments();
};

function updateSolicitationDropdown(docs) {
  const activeDocs = docs.filter((d) => d.state === "active");
  elements.selectSolicitation.innerHTML = `
    <option value="">-- Choose active solicitation --</option>
    ${activeDocs.map((d) => `<option value="${d.doc_id}">${escapeHtml(d.filename)}</option>`).join("")}`;
}

async function uploadFiles(files) {
  if (!files || files.length === 0) return;
  for (const file of files) {
    elements.progressContainer.classList.remove("hidden");
    elements.progressStageText.textContent = `Uploading ${file.name}...`;
    elements.progressBar.style.width = "5%";
    const formData = new FormData();
    formData.append("file", file);
    try {
      const res = await apiFetch("/api/v1/documents", { method: "POST", body: formData });
      if (!res.ok) {
        alert(`Upload error: ${await problemDetail(res)}`);
        elements.progressContainer.classList.add("hidden");
        continue;
      }
      const uploadResult = await res.json();
      await pollDocumentStatus(uploadResult.doc_id, file.name);
    } catch (err) {
      console.error("Upload error:", err);
      elements.progressContainer.classList.add("hidden");
    }
  }
  await fetchDocuments();
}

async function pollDocumentStatus(docId, filename) {
  const terminal = ["active", "quarantined", "failed"];
  for (let attempt = 0; attempt < 200; attempt++) {
    await new Promise((r) => setTimeout(r, 700));
    const res = await apiFetch(`/api/v1/documents/${docId}`);
    if (!res.ok) break;
    const doc = await res.json();
    const pct = doc.progress_pct ?? 0;
    elements.progressBar.style.width = `${pct}%`;
    elements.progressPercentText.textContent = `${pct}%`;
    elements.progressStageText.textContent = `${filename}: ${doc.stage || doc.state}`;
    if (terminal.includes(doc.state)) {
      elements.progressStageText.textContent = `${filename}: ${doc.state.toUpperCase()}${doc.error_message ? " — " + doc.error_message : ""}`;
      setTimeout(() => elements.progressContainer.classList.add("hidden"), 2500);
      break;
    }
  }
}

// ==============================================================================
// 6. Research Q&A & SSE Event Stream
// ==============================================================================

async function handleAskSubmit(event) {
  if (event) event.preventDefault();
  const query = elements.chatInput.value.trim();
  if (!query) return;

  state.activeQueryKeywords = query.toLowerCase().replace(/[^\w\s]/g, "").split(/\s+/).filter((w) => w.length > 2);
  appendUserMessage(query);
  elements.chatInput.value = "";

  const botMessageId = "msg-" + Date.now();
  appendBotMessageSkeleton(botMessageId);
  elements.reasoningTrail.classList.remove("hidden");
  resetReasoningTrail();

  try {
    const res = await apiFetch("/api/v1/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query }),
    });
    if (!res.ok) {
      updateBotMessage(botMessageId, `⚠️ Error: ${await problemDetail(res)}`);
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";
    const draft = { text: "" };

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const blocks = buffer.split("\n\n");
      buffer = blocks.pop() || "";
      for (const block of blocks) {
        const eventMatch = block.match(/^event:\s*(.+)$/m);
        const dataMatch = block.match(/^data:\s*(.+)$/m);
        if (!eventMatch || !dataMatch) continue;
        let data = {};
        try {
          data = JSON.parse(dataMatch[1].trim());
        } catch (e) {}
        handleSSEEvent(eventMatch[1].trim(), data, botMessageId, draft);
      }
    }
  } catch (err) {
    console.error("Ask query error:", err);
    updateBotMessage(botMessageId, `⚠️ Connection error: ${err.message}`);
  }
}

function handleSSEEvent(eventType, data, messageId, draft) {
  const pill = (id) => document.getElementById(id);
  const setPill = (id, cls, text) => {
    pill(id).className = `trail-pill ${cls}`;
    if (text) pill(id).querySelector("span").textContent = text;
  };

  if (eventType === "egress") {
    setEgressNote(messageId, data);
  } else if (eventType === "guard") {
    setPill("pill-guard", data.passed ? "done" : "active", `1. Guard: ${data.passed ? "Passed" : "Blocked"} (${data.backend})`);
  } else if (eventType === "route") {
    setPill("pill-route", "done", `2. Route: ${data.strategy}`);
  } else if (eventType === "filter") {
    setPill("pill-retrieve", "done", `3. Context: ${data.chunks_retained} chunk(s)`);
    setPill("pill-generate", "active");
  } else if (eventType === "generate") {
    if (data.token) {
      draft.text += data.token;
      updateBotMessage(messageId, draft.text);
    }
  } else if (eventType === "rewrite") {
    // The validator rejected the draft: discard it before the rewrite streams in.
    if (data.discard_draft) draft.text = "";
    updateBotMessage(messageId, `Rewriting after verification feedback (attempt ${data.attempt + 1})...`);
    setPill("pill-generate", "active", `4. Rewriting (attempt ${data.attempt + 1})`);
  } else if (eventType === "verify") {
    setPill("pill-generate", "done");
    setPill("pill-verify", data.verified ? "done" : "active",
      `5. ${data.verified ? "Verified" : "Not verified"} (${Math.round((data.confidence_score || 0) * 100)}%)`);
  } else if (eventType === "error") {
    updateBotMessage(messageId, `⚠️ ${data.detail || "Pipeline error"}`);
  } else if (eventType === "done") {
    finalizeBotMessage(messageId, data);
  }
}

function setEgressNote(messageId, egress) {
  const msg = document.getElementById(messageId);
  const header = msg && msg.querySelector(".message-header span");
  if (!header || !egress.provider) return;
  header.textContent = egress.data_leaves_machine
    ? `GrantLoom · ☁️ sent to ${egress.destination} (${egress.writer_model})`
    : `GrantLoom · 🖥️ local (${egress.writer_model || egress.provider})`;
}

function resetReasoningTrail() {
  ["pill-guard", "pill-route", "pill-retrieve", "pill-generate", "pill-verify"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.className = "trail-pill";
  });
}

function appendUserMessage(text) {
  const welcome = elements.chatTranscript.querySelector(".chat-welcome");
  if (welcome) welcome.remove();
  const msg = document.createElement("div");
  msg.className = "message-item message-user";
  msg.innerHTML = `
    <div class="message-header"><span>Researcher Query</span></div>
    <div class="message-body">${escapeHtml(text)}</div>`;
  elements.chatTranscript.appendChild(msg);
  msg.scrollIntoView({ behavior: "smooth" });
}

function appendBotMessageSkeleton(messageId) {
  const msg = document.createElement("div");
  msg.id = messageId;
  msg.className = "message-item message-bot";
  msg.innerHTML = `
    <div class="message-header">
      <span>GrantLoom Reasoning Engine</span>
      <span class="confidence-container"></span>
    </div>
    <div class="message-body">Thinking...</div>
    <div class="message-warning hidden" role="alert"></div>`;
  elements.chatTranscript.appendChild(msg);
  msg.scrollIntoView({ behavior: "smooth" });
}

function updateBotMessage(messageId, text) {
  const body = document.querySelector(`#${messageId} .message-body`);
  if (body) body.innerHTML = formatTextWithCitations(text);
}

function finalizeBotMessage(messageId, data) {
  const msg = document.getElementById(messageId);
  if (!msg) return;

  const confContainer = msg.querySelector(".confidence-container");
  if (data.refusal) {
    confContainer.innerHTML = `<span class="confidence-badge confidence-low">● Declined by input guard</span>`;
  } else if (data.abstention) {
    confContainer.innerHTML = `<span class="confidence-badge confidence-mid">● Not found in documents</span>`;
  } else {
    const confPct = Math.round((data.confidence_score || 0) * 100);
    const confClass = !data.verified ? "confidence-low" : confPct < 85 ? "confidence-mid" : "confidence-high";
    confContainer.innerHTML = `<span class="confidence-badge ${confClass}">● ${data.verified ? "Verified" : "UNVERIFIED"} · ${confPct}%</span>`;
  }

  for (const c of data.citations || []) state.lastCitations[c.chunk_id] = c;
  msg.querySelector(".message-body").innerHTML = formatTextWithCitations(data.answer);

  const warning = msg.querySelector(".message-warning");
  if (data.warning) {
    const claims = (data.unverified_claims || []).map((c) => `<li>${escapeHtml(c)}</li>`).join("");
    warning.innerHTML = `⚠️ ${escapeHtml(data.warning)}${claims ? `<ul>${claims}</ul>` : ""}`;
    warning.classList.remove("hidden");
  }
}

function formatTextWithCitations(rawText) {
  if (!rawText) return "";
  const pattern = /\[([a-zA-Z0-9_\-:]+)\]/g;
  return escapeHtml(rawText).replace(pattern, (match, chunkId) => {
    return `<button class="citation-badge" onclick="window.openCitationDrawer('${chunkId}')" title="Inspect coordinate">${chunkId}</button>`;
  });
}

// ==============================================================================
// 7. Interactive Slide-Over Citation Drawer with Keyword Highlighting
// ==============================================================================

window.openCitationDrawer = function (chunkId) {
  const coord = state.lastCitations[chunkId];
  elements.drawerChunkId.textContent = chunkId;
  if (coord) {
    elements.drawerFilename.textContent = coord.filename || "Indexed Document";
    elements.drawerCoord.textContent = coord.sheet ? `Sheet ${coord.sheet}, rows ${coord.row}` : `Page ${coord.page}`;
    elements.drawerExcerptText.innerHTML = highlightKeywords(coord.excerpt || "");
  } else {
    elements.drawerFilename.textContent = "Unverified citation";
    elements.drawerCoord.textContent = "--";
    elements.drawerExcerptText.textContent = "This marker did not resolve to a verified source.";
  }
  elements.citationDrawer.classList.remove("hidden");
  elements.drawerBackdrop.classList.remove("hidden");
  elements.citationDrawer.focus();
};

function closeCitationDrawer() {
  elements.citationDrawer.classList.add("hidden");
  elements.drawerBackdrop.classList.add("hidden");
}

function highlightKeywords(text) {
  let highlighted = escapeHtml(text || "");
  for (const kw of state.activeQueryKeywords || []) {
    if (kw.length < 3) continue;
    const safe = kw.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    highlighted = highlighted.replace(new RegExp(`(${safe})`, "gi"), '<mark class="kw-highlight">$1</mark>');
  }
  return highlighted;
}

// ==============================================================================
// 8. Compliance Matrix & Excel Export
// ==============================================================================

async function* sseEvents(res) {
  // Shared fetch-based SSE reader (EventSource cannot send POST bodies/headers)
  const reader = res.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const blocks = buffer.split("\n\n");
    buffer = blocks.pop() || "";
    for (const block of blocks) {
      const eventMatch = block.match(/^event:\s*(.+)$/m);
      const dataMatch = block.match(/^data:\s*(.+)$/m);
      if (!eventMatch || !dataMatch) continue;
      let data = {};
      try {
        data = JSON.parse(dataMatch[1].trim());
      } catch (e) {}
      yield [eventMatch[1].trim(), data];
    }
  }
}

async function runComplianceAudit() {
  const solicitationId = elements.selectSolicitation.value;
  if (!solicitationId) {
    alert("Please select a target solicitation first.");
    return;
  }
  elements.btnRunCompliance.disabled = true;
  elements.complianceProgressContainer.classList.remove("hidden");
  elements.complianceProgressBar.style.width = "3%";
  elements.complianceProgressPct.textContent = "...";
  elements.complianceProgressText.textContent = "Validating key and extracting requirements...";
  elements.complianceTableBody.innerHTML = `<tr><td colspan="5" class="empty-state">Audit running — progress above.</td></tr>`;
  try {
    const res = await apiFetch(`/api/v1/compliance/${solicitationId}/stream`, { method: "POST" });
    if (!res.ok) {
      alert(`Audit error: ${await problemDetail(res)}`);
      return;
    }
    for await (const [event, data] of sseEvents(res)) {
      if (event === "start") {
        elements.complianceProgressText.textContent =
          `Found ${data.total_requirements} requirement(s) in ${data.filename}; assessing against ${data.lab_documents} lab document(s)...`;
      } else if (event === "progress") {
        const pct = data.total ? Math.round((data.evaluated / data.total) * 100) : 0;
        elements.complianceProgressBar.style.width = `${pct}%`;
        elements.complianceProgressPct.textContent = `${data.evaluated}/${data.total}`;
        elements.complianceProgressText.textContent = `${data.req_id} · ${data.category}: ${data.requirement_text}`;
      } else if (event === "done") {
        renderComplianceMatrix(data);
        elements.complianceProgressBar.style.width = "100%";
        elements.complianceProgressPct.textContent = "Done";
        elements.complianceProgressText.textContent = `Audit complete — ${data.total_requirements} requirement(s), readiness ${data.readiness_score_pct}%.`;
        setTimeout(() => elements.complianceProgressContainer.classList.add("hidden"), 4000);
      } else if (event === "error") {
        alert(`Audit error: ${data.detail || "evaluation failed"}`);
        elements.complianceProgressText.textContent = "Audit failed — see error.";
      }
    }
  } catch (err) {
    console.error("Compliance error:", err);
    elements.complianceProgressText.textContent = `Connection error: ${err.message}`;
  } finally {
    elements.btnRunCompliance.disabled = false;
  }
}

function renderComplianceMatrix(matrix) {
  state.activeSolicitationId = matrix.solicitation_id;
  const score = Math.round(matrix.readiness_score_pct);
  elements.readinessCard.classList.remove("hidden");
  elements.readinessScore.textContent = `${score}%`;
  elements.readinessHeadline.textContent = `Readiness Estimate: ${score}/100`;
  elements.readinessBreakdown.textContent =
    `${matrix.satisfied_count} Satisfied | ${matrix.partial_count} Partial | ${matrix.gap_count} Gaps — ${matrix.disclaimer}`;

  elements.complianceTableBody.innerHTML = (matrix.requirements || [])
    .map((r) => {
      let badgeClass = "badge-quarantined";
      if (r.status === "Satisfied") badgeClass = "badge-active";
      else if (r.status === "Partial") badgeClass = "badge-processing";
      return `
        <tr>
          <td><strong>${escapeHtml(r.req_id)}</strong></td>
          <td>${escapeHtml(r.category)}</td>
          <td>${escapeHtml(r.requirement_text)}<div style="font-size: 0.75rem; opacity: 0.7;">${escapeHtml(r.solicitation_source)}</div></td>
          <td><span class="badge-status ${badgeClass}">${escapeHtml(r.status)}</span></td>
          <td>
            <div style="font-size: 0.85rem;">${escapeHtml(r.evidence)}</div>
            <div style="font-size: 0.8rem; margin-top: 4px;">${escapeHtml(r.rationale)}</div>
            <div style="font-size: 0.75rem; color: var(--accent-teal); margin-top: 4px;">Citation: ${escapeHtml(r.citation)} · ${escapeHtml(r.decision_backend)}</div>
          </td>
        </tr>`;
    })
    .join("");
}

async function exportComplianceExcel() {
  const solicitationId = state.activeSolicitationId || elements.selectSolicitation.value;
  if (!solicitationId) {
    alert("Run a compliance audit before exporting.");
    return;
  }
  // Download through fetch so the X-API-Key header is sent
  const res = await apiFetch(`/api/v1/compliance/${solicitationId}/export`);
  if (!res.ok) {
    alert(`Export error: ${await problemDetail(res)}`);
    return;
  }
  const url = URL.createObjectURL(await res.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = `compliance_matrix_${solicitationId}.xlsx`;
  link.click();
  URL.revokeObjectURL(url);
}

// ==============================================================================
// 9. Starter Prompts
// ==============================================================================

async function loadStarterPrompts() {
  try {
    const res = await apiFetch("/api/v1/examples");
    if (!res.ok) return;
    const examples = await res.json();
    elements.starterPromptsList.innerHTML = examples
      .map((ex, idx) => `
        <div class="starter-prompt-card" data-idx="${idx}">
          <span class="starter-prompt-category">${escapeHtml(ex.category)}</span>
          ${escapeHtml(ex.question)}
        </div>`)
      .join("");
    elements.starterPromptsList.querySelectorAll(".starter-prompt-card").forEach((card) => {
      card.addEventListener("click", () => window.useStarterPrompt(examples[Number(card.dataset.idx)].question));
    });
  } catch (err) {
    console.error("Failed to load starter prompts:", err);
  }
}

window.useStarterPrompt = function (text) {
  switchTab("qa");
  elements.chatInput.value = text;
  elements.chatInput.focus();
};

// ==============================================================================
// 10. Settings & Diagnostics
// ==============================================================================

function openSettingsModal() {
  elements.settingsModal.classList.remove("hidden");
}

function closeSettingsModal() {
  elements.settingsModal.classList.add("hidden");
}

async function saveSettings() {
  state.apiKey = elements.inputOpenRouterKey.value.trim();
  state.masterKey = elements.inputMasterKey.value.trim();
  await setStoredCredential("openrouter_api_key", state.apiKey);
  await setStoredCredential("master_api_key", state.masterKey);
  // D28: validate the key server-side once, on save (the server then skips per-request probes)
  if (state.apiKey) {
    elements.keyProbeResult.textContent = "Validating key with the server...";
    await validateKeyOnServer();
  } else {
    elements.keyProbeResult.textContent = "";
  }
  closeSettingsModal();
  await loadServerInfo();
  await fetchDocuments();
  await loadStarterPrompts();
}

async function validateKeyOnServer() {
  try {
    const res = await apiFetch("/api/v1/auth/validate-key", { method: "POST" });
    if (res.ok) {
      const d = await res.json();
      elements.keyProbeResult.textContent = d.mode === "private"
        ? "🟢 Private mode — no key needed (local Ollama)."
        : `🟢 Key valid — remembered by the server (${d.writer_model} writer, ${d.judge_model} judge).`;
      return true;
    }
    elements.keyProbeResult.textContent = `🔴 ${await problemDetail(res)}`;
    return false;
  } catch (err) {
    elements.keyProbeResult.textContent = "⚠️ Could not reach the server to validate the key.";
    return false;
  }
}

async function testApiKeyProbe() {
  const key = elements.inputOpenRouterKey.value.trim();
  if (!key) {
    elements.keyProbeResult.textContent = "Please enter a key first.";
    return;
  }
  state.apiKey = key; // apiFetch attaches the X-OpenRouter-Key header from state
  elements.keyProbeResult.textContent = "Validating key with the server...";
  await validateKeyOnServer();
}

async function checkConfiguredModels() {
  elements.modelsProbeResult.textContent = "Checking writer and judge models...";
  const results = [];
  for (const role of ["writer", "judge"]) {
    const res = await apiFetch("/api/v1/admin/test-model-capabilities", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ role }),
    });
    if (!res.ok) {
      results.push(`${role}: ${await problemDetail(res)}`);
      continue;
    }
    const d = await res.json();
    results.push(`${role} ${d.model}: ${d.status}${d.context_length ? `, ${d.context_length} ctx` : ""}`);
  }
  elements.modelsProbeResult.textContent = results.join(" · ");
}

async function triggerGenerateDemoData() {
  elements.btnGenerateDemo.disabled = true;
  elements.btnGenerateDemo.textContent = "Generating...";
  try {
    const res = await apiFetch("/api/v1/admin/generate-synthetic-data", { method: "POST" });
    if (!res.ok) {
      alert(`Failed to generate demo data: ${await problemDetail(res)}`);
      return;
    }
    const data = await res.json();
    const active = data.documents.filter((d) => d.state === "active").length;
    alert(`Demo dataset generated: ${active}/${data.generated_count} documents active.`);
    await fetchDocuments();
  } catch (err) {
    console.error("Demo data error:", err);
  } finally {
    elements.btnGenerateDemo.disabled = false;
    elements.btnGenerateDemo.textContent = "🪄 Generate Demo Data";
  }
}

function escapeHtml(str) {
  if (str === null || str === undefined) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

// ==============================================================================
// 11. Event Listeners & Bootstrapping
// ==============================================================================

function bindEvents() {
  elements.tabDocuments.addEventListener("click", () => switchTab("documents"));
  elements.tabQa.addEventListener("click", () => switchTab("qa"));
  elements.tabCompliance.addEventListener("click", () => switchTab("compliance"));

  elements.btnOpenSettings.addEventListener("click", openSettingsModal);
  elements.btnBannerSettings.addEventListener("click", openSettingsModal);
  elements.btnCloseSettings.addEventListener("click", closeSettingsModal);
  elements.btnCancelSettings.addEventListener("click", closeSettingsModal);
  elements.btnSaveSettings.addEventListener("click", saveSettings);
  elements.btnTestKey.addEventListener("click", testApiKeyProbe);
  elements.btnCheckModels.addEventListener("click", checkConfiguredModels);
  elements.btnToggleKeyVisibility.addEventListener("click", () => {
    const isPwd = elements.inputOpenRouterKey.type === "password";
    elements.inputOpenRouterKey.type = isPwd ? "text" : "password";
    elements.btnToggleKeyVisibility.textContent = isPwd ? "Hide" : "Show";
  });

  elements.btnCloseDrawer.addEventListener("click", closeCitationDrawer);
  elements.drawerBackdrop.addEventListener("click", closeCitationDrawer);
  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      closeCitationDrawer();
      closeSettingsModal();
    }
  });

  elements.dropzone.addEventListener("click", () => elements.fileInput.click());
  elements.fileInput.addEventListener("change", (e) => uploadFiles(e.target.files));
  elements.dropzone.addEventListener("dragover", (e) => {
    e.preventDefault();
    elements.dropzone.classList.add("dragover");
  });
  elements.dropzone.addEventListener("dragleave", () => elements.dropzone.classList.remove("dragover"));
  elements.dropzone.addEventListener("drop", (e) => {
    e.preventDefault();
    elements.dropzone.classList.remove("dragover");
    uploadFiles(e.dataTransfer.files);
  });

  elements.btnRefreshDocs.addEventListener("click", fetchDocuments);

  elements.chatForm.addEventListener("submit", handleAskSubmit);
  elements.chatInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleAskSubmit();
    }
  });

  elements.btnRunCompliance.addEventListener("click", runComplianceAudit);
  elements.btnExportExcel.addEventListener("click", exportComplianceExcel);
  elements.btnGenerateDemo.addEventListener("click", triggerGenerateDemoData);
  elements.btnModalDemoData.addEventListener("click", triggerGenerateDemoData);
}

window.addEventListener("DOMContentLoaded", async () => {
  bindEvents();
  await initCredentials();
  await fetchDocuments();
  await loadStarterPrompts();
});
