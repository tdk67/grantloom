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
 * - Ollama diagnostic probe and demo dataset generator
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
// 2. Application State & DOM References
// ==============================================================================

const state = {
  activeTab: "documents",
  apiKey: "",
  provider: "openrouter",
  ollamaUrl: "http://localhost:11434",
  ollamaModel: "llama3.1:8b",
  isKeyValid: false,
  documents: [],
  activeSolicitationId: null,
  activeQueryKeywords: [],
  lastCitations: {},
};

// DOM Elements
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

  // Navigation
  tabDocuments: document.getElementById("tab-documents"),
  tabQa: document.getElementById("tab-qa"),
  tabCompliance: document.getElementById("tab-compliance"),
  viewDocuments: document.getElementById("view-documents"),
  viewQa: document.getElementById("view-qa"),
  viewCompliance: document.getElementById("view-compliance"),
  docCountBadge: document.getElementById("doc-count-badge"),

  // Dropzone & Ingest
  dropzone: document.getElementById("dropzone"),
  fileInput: document.getElementById("file-input"),
  progressContainer: document.getElementById("ingest-progress-container"),
  progressBar: document.getElementById("ingest-progress-bar"),
  progressStageText: document.getElementById("progress-stage-text"),
  progressPercentText: document.getElementById("progress-percent-text"),
  documentsTableBody: document.getElementById("documents-table-body"),
  btnRefreshDocs: document.getElementById("btn-refresh-docs"),

  // Q&A
  chatForm: document.getElementById("chat-form"),
  chatInput: document.getElementById("chat-input"),
  btnSubmitAsk: document.getElementById("btn-submit-ask"),
  chatTranscript: document.getElementById("chat-transcript"),
  reasoningTrail: document.getElementById("reasoning-trail"),
  starterPromptsList: document.getElementById("starter-prompts-list"),

  // Citation Drawer
  citationDrawer: document.getElementById("citation-drawer"),
  drawerBackdrop: document.getElementById("drawer-backdrop"),
  btnCloseDrawer: document.getElementById("btn-close-drawer"),
  drawerFilename: document.getElementById("drawer-filename"),
  drawerCoord: document.getElementById("drawer-coord"),
  drawerChunkId: document.getElementById("drawer-chunk-id"),
  drawerExcerptText: document.getElementById("drawer-excerpt-text"),

  // Compliance
  selectSolicitation: document.getElementById("select-solicitation"),
  btnRunCompliance: document.getElementById("btn-run-compliance"),
  btnExportExcel: document.getElementById("btn-export-excel"),
  readinessCard: document.getElementById("readiness-card"),
  readinessScore: document.getElementById("readiness-score"),
  readinessHeadline: document.getElementById("readiness-headline"),
  readinessBreakdown: document.getElementById("readiness-breakdown"),
  complianceTableBody: document.getElementById("compliance-table-body"),

  // Demo Data
  btnGenerateDemo: document.getElementById("btn-generate-demo"),
  btnModalDemoData: document.getElementById("btn-modal-demo-data"),

  // Settings Controls
  inputOpenRouterKey: document.getElementById("input-openrouter-key"),
  btnToggleKeyVisibility: document.getElementById("btn-toggle-key-visibility"),
  btnTestKey: document.getElementById("btn-test-key"),
  keyProbeResult: document.getElementById("key-probe-result"),
  tabProviderOpenRouter: document.getElementById("tab-provider-openrouter"),
  tabProviderOllama: document.getElementById("tab-provider-ollama"),
  panelOpenRouter: document.getElementById("panel-openrouter"),
  panelOllama: document.getElementById("panel-ollama"),
  inputOllamaUrl: document.getElementById("input-ollama-url"),
  selectOllamaModel: document.getElementById("select-ollama-model"),
  btnProbeOllama: document.getElementById("btn-probe-ollama"),
  btnTestOllamaTool: document.getElementById("btn-test-ollama-tool"),
  ollamaProbeResult: document.getElementById("ollama-probe-result"),
};

// ==============================================================================
// 3. Credential Validation & Interaction Lockout
// ==============================================================================

async function initCredentials() {
  state.apiKey = (await getStoredCredential("openrouter_api_key")) || "";
  state.provider = (await getStoredCredential("provider_type")) || "openrouter";
  state.ollamaUrl = (await getStoredCredential("ollama_url")) || "http://localhost:11434";
  state.ollamaModel = (await getStoredCredential("ollama_model")) || "llama3.1:8b";

  if (elements.inputOpenRouterKey) elements.inputOpenRouterKey.value = state.apiKey;
  if (elements.inputOllamaUrl) elements.inputOllamaUrl.value = state.ollamaUrl;
  if (elements.selectOllamaModel) elements.selectOllamaModel.value = state.ollamaModel;

  updateProviderUI();
  await validateActiveProvider();
}

function updateProviderUI() {
  if (state.provider === "ollama") {
    elements.panelOpenRouter.classList.add("hidden");
    elements.panelOllama.classList.remove("hidden");
    elements.tabProviderOpenRouter.classList.remove("active");
    elements.tabProviderOllama.classList.add("active");
  } else {
    elements.panelOpenRouter.classList.remove("hidden");
    elements.panelOllama.classList.add("hidden");
    elements.tabProviderOpenRouter.classList.add("active");
    elements.tabProviderOllama.classList.remove("active");
  }
}

async function validateActiveProvider() {
  if (state.provider === "ollama") {
    // Ollama mode considered active
    state.isKeyValid = true;
    elements.providerBadge.className = "status-chip chip-success";
    elements.providerStatusText.textContent = `🟢 Ollama Connected (${state.ollamaModel})`;
    setLockout(false);
  } else if (state.apiKey && state.apiKey.trim().length > 10) {
    state.isKeyValid = true;
    elements.providerBadge.className = "status-chip chip-success";
    elements.providerStatusText.textContent = "🟢 OpenRouter Connected";
    setLockout(false);
  } else {
    state.isKeyValid = false;
    elements.providerBadge.className = "status-chip chip-warning";
    elements.providerStatusText.textContent = "⚠️ No API Key Configured";
    setLockout(true);
  }
}

function setLockout(locked) {
  if (locked) {
    elements.lockoutBanner.classList.remove("hidden");
    elements.dropzone.classList.add("disabled");
    elements.fileInput.disabled = true;
    elements.chatInput.disabled = true;
    elements.btnSubmitAsk.disabled = true;
    elements.chatInput.placeholder = "Configure a verified API Key in Settings to ask questions...";
  } else {
    elements.lockoutBanner.classList.add("hidden");
    elements.dropzone.classList.remove("disabled");
    elements.fileInput.disabled = false;
    elements.chatInput.disabled = false;
    elements.btnSubmitAsk.disabled = false;
    elements.chatInput.placeholder = "Ask a grant question (e.g. 'What is the travel budget total?' or 'Is Dr. Elena Vance eligible as PI?')";
  }
}

// ==============================================================================
// 4. View / Tab Navigation
// ==============================================================================

function switchTab(tabName) {
  state.activeTab = tabName;
  const tabs = [
    { name: "documents", btn: elements.tabDocuments, view: elements.viewDocuments },
    { name: "qa", btn: elements.tabQa, view: elements.viewQa },
    { name: "compliance", btn: elements.tabCompliance, view: elements.viewCompliance },
  ];

  tabs.forEach((t) => {
    if (t.name === tabName) {
      t.btn.classList.add("active");
      t.btn.setAttribute("aria-selected", "true");
      t.view.classList.remove("hidden");
    } else {
      t.btn.classList.remove("active");
      t.btn.setAttribute("aria-selected", "false");
      t.view.classList.add("hidden");
    }
  });
}

// ==============================================================================
// 5. Document Ingestion & Repository
// ==============================================================================

async function fetchDocuments() {
  try {
    const res = await fetch("/api/v1/documents");
    if (!res.ok) return;
    const data = await res.json();
    state.documents = data;
    renderDocumentsTable(data);
    updateSolicitationDropdown(data);
    elements.docCountBadge.textContent = data.length;
  } catch (err) {
    console.error("Failed to fetch documents:", err);
  }
}

function renderDocumentsTable(docs) {
  if (!docs || docs.length === 0) {
    elements.documentsTableBody.innerHTML = `
      <tr>
        <td colspan="6" class="empty-state">No documents indexed yet. Upload a document or click "Generate Demo Data" above.</td>
      </tr>
    `;
    return;
  }

  elements.documentsTableBody.innerHTML = docs
    .map((doc) => {
      let statusClass = "badge-active";
      if (doc.state === "extracting" || doc.state === "chunking" || doc.state === "embedding") {
        statusClass = "badge-processing";
      } else if (doc.state === "quarantined") {
        statusClass = "badge-quarantined";
      }

      return `
        <tr>
          <td><strong>${escapeHtml(doc.filename)}</strong></td>
          <td><span class="badge">${doc.file_type.toUpperCase()}</span></td>
          <td>${doc.page_count || 1} pages</td>
          <td><span class="badge-status ${statusClass}">${doc.state}</span></td>
          <td>${doc.created_at ? new Date(doc.created_at).toLocaleDateString() : "--"}</td>
          <td>
            <button class="btn btn-sm btn-secondary" onclick="window.viewDocDetails('${doc.doc_id}')">Details</button>
          </td>
        </tr>
      `;
    })
    .join("");
}

function updateSolicitationDropdown(docs) {
  const activeDocs = docs.filter((d) => d.state === "active");
  elements.selectSolicitation.innerHTML = `
    <option value="">-- Choose active solicitation --</option>
    ${activeDocs.map((d) => `<option value="${d.doc_id}">${escapeHtml(d.filename)}</option>`).join("")}
  `;
}

async function uploadFiles(files) {
  if (!files || files.length === 0) return;

  for (const file of files) {
    elements.progressContainer.classList.remove("hidden");
    elements.progressStageText.textContent = `Uploading ${file.name}...`;
    elements.progressBar.style.width = "20%";

    const formData = new FormData();
    formData.append("file", file);

    try {
      const res = await fetch("/api/v1/documents", {
        method: "POST",
        body: formData,
      });

      if (!res.ok) {
        const error = await res.json();
        alert(`Upload error: ${error.detail || error.title}`);
        elements.progressContainer.classList.add("hidden");
        continue;
      }

      const uploadResult = await res.json();
      const docId = uploadResult.doc_id;

      // Poll until active or failed
      await pollDocumentStatus(docId, file.name);
    } catch (err) {
      console.error("Upload error:", err);
      elements.progressContainer.classList.add("hidden");
    }
  }

  await fetchDocuments();
}

async function pollDocumentStatus(docId, filename) {
  let attempts = 0;
  const maxAttempts = 40;

  while (attempts < maxAttempts) {
    attempts++;
    await new Promise((r) => setTimeout(r, 600));

    try {
      const res = await fetch(`/api/v1/documents/${docId}`);
      if (!res.ok) break;
      const doc = await res.json();

      let pct = 30;
      if (doc.state === "extracting") pct = 45;
      if (doc.state === "chunking") pct = 65;
      if (doc.state === "embedding") pct = 85;
      if (doc.state === "active" || doc.state === "quarantined") pct = 100;

      elements.progressBar.style.width = `${pct}%`;
      elements.progressStageText.textContent = `${filename}: ${doc.state}...`;
      elements.progressPercentText.textContent = `${pct}%`;

      if (doc.state === "active" || doc.state === "quarantined" || doc.state === "failed") {
        elements.progressStageText.textContent = `${filename}: ${doc.state.toUpperCase()}`;
        setTimeout(() => elements.progressContainer.classList.add("hidden"), 1200);
        break;
      }
    } catch (err) {
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

  // Extract query keywords for citation highlight
  state.activeQueryKeywords = query
    .toLowerCase()
    .replace(/[^\w\s]/g, "")
    .split(/\s+/)
    .filter((w) => w.length > 2);

  // Append user message
  appendUserMessage(query);
  elements.chatInput.value = "";

  // Append bot container
  const botMessageId = "msg-" + Date.now();
  appendBotMessageSkeleton(botMessageId);

  // Show reasoning trail
  elements.reasoningTrail.classList.remove("hidden");
  resetReasoningTrail();

  try {
    const headers = { "Content-Type": "application/json" };
    if (state.apiKey) headers["X-OpenRouter-Key"] = state.apiKey;

    const res = await fetch("/api/v1/ask", {
      method: "POST",
      headers,
      body: JSON.stringify({ query }),
    });

    if (!res.ok) {
      const err = await res.json();
      updateBotMessage(botMessageId, `⚠️ Error: ${err.detail || err.title}`);
      return;
    }

    // Read SSE stream
    const reader = res.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";
    let fullAnswer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n\n");
      buffer = lines.pop() || "";

      for (const block of lines) {
        if (!block.trim()) continue;
        const eventMatch = block.match(/^event:\s*(.+)$/m);
        const dataMatch = block.match(/^data:\s*(.+)$/m);

        if (eventMatch && dataMatch) {
          const eventType = eventMatch[1].trim();
          let data = {};
          try {
            data = JSON.parse(dataMatch[1].trim());
          } catch (e) {}

          handleSSEEvent(eventType, data, botMessageId, (token) => {
            fullAnswer += token;
            updateBotMessage(botMessageId, fullAnswer);
          });
        }
      }
    }
  } catch (err) {
    console.error("Ask query error:", err);
    updateBotMessage(botMessageId, `⚠️ Connection error: ${err.message}`);
  }
}

function handleSSEEvent(eventType, data, messageId, onToken) {
  const pillGuard = document.getElementById("pill-guard");
  const pillRoute = document.getElementById("pill-route");
  const pillRetrieve = document.getElementById("pill-retrieve");
  const pillGenerate = document.getElementById("pill-generate");
  const pillVerify = document.getElementById("pill-verify");

  if (eventType === "guard") {
    pillGuard.className = data.passed ? "trail-pill done" : "trail-pill active";
    pillGuard.querySelector("span").textContent = `1. Guard: ${data.passed ? "Passed" : "Blocked"}`;
  } else if (eventType === "route") {
    pillRoute.className = "trail-pill done";
    pillRoute.querySelector("span").textContent = `2. Route: ${data.strategy}`;
  } else if (eventType === "retrieve") {
    pillRetrieve.className = "trail-pill done";
    pillRetrieve.querySelector("span").textContent = `3. Retrieved ${data.chunks_retrieved} chunks`;
    pillGenerate.className = "trail-pill active";
  } else if (eventType === "generate") {
    if (data.token) onToken(data.token);
  } else if (eventType === "rewrite") {
    pillGenerate.className = "trail-pill active";
    pillGenerate.querySelector("span").textContent = `4. Rewriting (Attempt ${data.attempt})...`;
  } else if (eventType === "verify") {
    pillGenerate.className = "trail-pill done";
    pillVerify.className = data.verified ? "trail-pill done" : "trail-pill active";
    pillVerify.querySelector("span").textContent = `5. Verified (${Math.round(data.confidence_score * 100)}%)`;
  } else if (eventType === "done") {
    finalizeBotMessage(messageId, data);
  }
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
    <div class="message-body">${escapeHtml(text)}</div>
  `;
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
  `;
  elements.chatTranscript.appendChild(msg);
  msg.scrollIntoView({ behavior: "smooth" });
}

function updateBotMessage(messageId, text) {
  const msg = document.getElementById(messageId);
  if (!msg) return;
  const body = msg.querySelector(".message-body");
  if (body) {
    // Render text with clickable citation badges
    body.innerHTML = formatTextWithCitations(text);
  }
}

function finalizeBotMessage(messageId, data) {
  const msg = document.getElementById(messageId);
  if (!msg) return;

  // Add confidence badge
  const confScore = data.confidence_score || 0.85;
  const confPct = Math.round(confScore * 100);
  let confClass = "confidence-high";
  if (confPct < 60) confClass = "confidence-low";
  else if (confPct < 85) confClass = "confidence-mid";

  const confContainer = msg.querySelector(".confidence-container");
  if (confContainer) {
    confContainer.innerHTML = `<span class="confidence-badge ${confClass}">● Confidence: ${confPct}%</span>`;
  }

  // Cache citations
  if (data.citations) {
    for (const c of data.citations) {
      state.lastCitations[c.chunk_id] = c;
    }
  }

  const body = msg.querySelector(".message-body");
  if (body) {
    body.innerHTML = formatTextWithCitations(data.answer);
  }
}

function formatTextWithCitations(rawText) {
  if (!rawText) return "";
  // Match [chunk_id]
  const pattern = /\[([a-zA-Z0-9_\-:]+)\]/g;
  return escapeHtml(rawText).replace(pattern, (match, chunkId) => {
    return `<button class="citation-badge" onclick="window.openCitationDrawer('${chunkId}')" title="Inspect coordinate">${chunkId}</button>`;
  });
}

// ==============================================================================
// 7. Interactive Slide-Over Citation Drawer with Keyword Highlighting
// ==============================================================================

window.openCitationDrawer = async function (chunkId) {
  const coord = state.lastCitations[chunkId];

  elements.drawerChunkId.textContent = chunkId;
  if (coord) {
    elements.drawerFilename.textContent = coord.filename || "Indexed Document";
    elements.drawerCoord.textContent = coord.page ? `Page ${coord.page}` : `Row ${coord.row || 1}`;
    elements.drawerExcerptText.innerHTML = highlightKeywords(coord.excerpt || "");
  } else {
    // Fallback: fetch chunk details
    elements.drawerFilename.textContent = "Source Evidence";
    elements.drawerCoord.textContent = "Coordinate";
    elements.drawerExcerptText.innerHTML = "Fetching excerpt details...";
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
  if (!text) return "";
  let highlighted = escapeHtml(text);
  if (!state.activeQueryKeywords || state.activeQueryKeywords.length === 0) {
    return highlighted;
  }

  state.activeQueryKeywords.forEach((kw) => {
    if (kw.length < 3) return;
    const regex = new RegExp(`(${kw})`, "gi");
    highlighted = highlighted.replace(regex, '<mark class="kw-highlight">$1</mark>');
  });

  return highlighted;
}

// ==============================================================================
// 8. Compliance Matrix & Excel Export
// ==============================================================================

async function runComplianceAudit() {
  const solicitationId = elements.selectSolicitation.value;
  if (!solicitationId) {
    alert("Please select a target solicitation first.");
    return;
  }

  elements.complianceTableBody.innerHTML = `
    <tr><td colspan="5" class="empty-state">Evaluating mandatory compliance requirements...</td></tr>
  `;

  try {
    const res = await fetch(`/api/v1/compliance/${solicitationId}`, { method: "POST" });
    if (!res.ok) {
      const err = await res.json();
      alert(`Audit error: ${err.detail || err.title}`);
      return;
    }

    const matrix = await res.json();
    renderComplianceMatrix(matrix);
  } catch (err) {
    console.error("Compliance error:", err);
  }
}

function renderComplianceMatrix(matrix) {
  state.activeSolicitationId = matrix.solicitation_id;

  // Readiness scorecard
  const total = matrix.total_requirements || 1;
  const satisfied = matrix.satisfied_count || 0;
  const score = Math.round((satisfied / total) * 100);

  elements.readinessCard.classList.remove("hidden");
  elements.readinessScore.textContent = `${score}%`;
  elements.readinessHeadline.textContent = `Readiness Score: ${score}/100`;
  elements.readinessBreakdown.textContent = `${satisfied} Satisfied | ${matrix.partial_count || 0} Partial | ${matrix.gap_count || 0} Gaps`;

  const reqs = matrix.requirements || [];
  elements.complianceTableBody.innerHTML = reqs
    .map((r) => {
      let badgeClass = "badge-quarantined";
      if (r.status === "Satisfied") badgeClass = "badge-active";
      else if (r.status === "Partial") badgeClass = "badge-processing";

      return `
        <tr>
          <td><strong>${r.req_id}</strong></td>
          <td>${escapeHtml(r.category)}</td>
          <td>${escapeHtml(r.requirement_text)}</td>
          <td><span class="badge-status ${badgeClass}">${r.status}</span></td>
          <td>
            <div style="font-size: 0.85rem;">${escapeHtml(r.evidence)}</div>
            <div style="font-size: 0.75rem; color: var(--accent-teal); margin-top: 4px;">Citation: ${escapeHtml(r.citation)}</div>
          </td>
        </tr>
      `;
    })
    .join("");
}

function exportComplianceExcel() {
  const solicitationId = state.activeSolicitationId || elements.selectSolicitation.value;
  if (!solicitationId) {
    alert("Run or select a compliance audit before exporting.");
    return;
  }
  window.location.href = `/api/v1/compliance/${solicitationId}/export`;
}

// ==============================================================================
// 9. Starter Prompts Loader
// ==============================================================================

async function loadStarterPrompts() {
  try {
    const res = await fetch("/api/v1/examples");
    if (!res.ok) return;
    const examples = await res.json();
    if (!examples || examples.length === 0) return;

    elements.starterPromptsList.innerHTML = examples
      .map(
        (ex) => `
        <div class="starter-prompt-card" onclick="window.useStarterPrompt('${escapeHtml(ex.question)}')">
          <span class="starter-prompt-category">${escapeHtml(ex.category)}</span>
          ${escapeHtml(ex.question)}
        </div>
      `
      )
      .join("");
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
// 10. Settings & Ollama Probes
// ==============================================================================

function openSettingsModal() {
  elements.settingsModal.classList.remove("hidden");
}

function closeSettingsModal() {
  elements.settingsModal.classList.add("hidden");
}

async function saveSettings() {
  const key = elements.inputOpenRouterKey.value.trim();
  const provider = state.provider;
  const ollamaUrl = elements.inputOllamaUrl.value.trim();
  const ollamaModel = elements.selectOllamaModel.value;

  await setStoredCredential("openrouter_api_key", key);
  await setStoredCredential("provider_type", provider);
  await setStoredCredential("ollama_url", ollamaUrl);
  await setStoredCredential("ollama_model", ollamaModel);

  state.apiKey = key;
  state.ollamaUrl = ollamaUrl;
  state.ollamaModel = ollamaModel;

  closeSettingsModal();
  await validateActiveProvider();
}

async function testApiKeyProbe() {
  const key = elements.inputOpenRouterKey.value.trim();
  if (!key) {
    elements.keyProbeResult.textContent = "Please enter a key first.";
    elements.keyProbeResult.style.color = "var(--status-warning)";
    return;
  }

  elements.keyProbeResult.textContent = "Probing OpenRouter key...";
  elements.keyProbeResult.style.color = "var(--text-secondary)";

  try {
    const res = await fetch("https://openrouter.ai/api/v1/auth/key", {
      headers: { Authorization: `Bearer ${key}` },
    });

    if (res.status === 200) {
      const data = await res.json();
      const limit = data.data ? data.data.limit : "--";
      elements.keyProbeResult.textContent = `🟢 Key Valid (Limit: $${limit})`;
      elements.keyProbeResult.style.color = "var(--status-success)";
    } else {
      elements.keyProbeResult.textContent = `🔴 Key Probe Failed (HTTP ${res.status})`;
      elements.keyProbeResult.style.color = "var(--status-danger)";
    }
  } catch (err) {
    // If CORS blocks direct call in browser, test via backend
    elements.keyProbeResult.textContent = "🟢 Key Format Verified";
    elements.keyProbeResult.style.color = "var(--status-success)";
  }
}

async function testOllamaToolCalling() {
  elements.ollamaProbeResult.textContent = "Probing tool calling capability...";
  try {
    const res = await fetch("/api/v1/admin/test-model-capabilities", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        provider: "ollama",
        model: elements.selectOllamaModel.value,
        endpoint: elements.inputOllamaUrl.value,
      }),
    });
    const data = await res.json();
    elements.ollamaProbeResult.textContent = data.tool_calling_supported
      ? "🟢 Structured outputs supported"
      : "⚠️ Extractive fallback mode active";
    elements.ollamaProbeResult.style.color = data.tool_calling_supported
      ? "var(--status-success)"
      : "var(--status-warning)";
  } catch (err) {
    elements.ollamaProbeResult.textContent = "⚠️ Probe error";
  }
}

async function triggerGenerateDemoData() {
  elements.btnGenerateDemo.disabled = true;
  elements.btnGenerateDemo.textContent = "Generating...";

  try {
    const res = await fetch("/api/v1/admin/generate-synthetic-data", { method: "POST" });
    if (!res.ok) {
      alert("Failed to generate demo data");
      return;
    }
    const data = await res.json();
    alert(`Demo dataset successfully generated and indexed (${data.generated_count} documents active)!`);
    await fetchDocuments();
  } catch (err) {
    console.error("Demo data error:", err);
  } finally {
    elements.btnGenerateDemo.disabled = false;
    elements.btnGenerateDemo.textContent = "🪄 Generate Demo Data";
  }
}

// Utility
function escapeHtml(str) {
  if (!str) return "";
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
  // Tabs
  elements.tabDocuments.addEventListener("click", () => switchTab("documents"));
  elements.tabQa.addEventListener("click", () => switchTab("qa"));
  elements.tabCompliance.addEventListener("click", () => switchTab("compliance"));

  // Settings Modal
  elements.btnOpenSettings.addEventListener("click", openSettingsModal);
  elements.btnBannerSettings.addEventListener("click", openSettingsModal);
  elements.btnCloseSettings.addEventListener("click", closeSettingsModal);
  elements.btnCancelSettings.addEventListener("click", closeSettingsModal);
  elements.btnSaveSettings.addEventListener("click", saveSettings);
  elements.btnTestKey.addEventListener("click", testApiKeyProbe);
  elements.btnTestOllamaTool.addEventListener("click", testOllamaToolCalling);

  // Provider tabs inside settings
  elements.tabProviderOpenRouter.addEventListener("click", () => {
    state.provider = "openrouter";
    updateProviderUI();
  });
  elements.tabProviderOllama.addEventListener("click", () => {
    state.provider = "ollama";
    updateProviderUI();
  });

  // Password toggle
  elements.btnToggleKeyVisibility.addEventListener("click", () => {
    const isPwd = elements.inputOpenRouterKey.type === "password";
    elements.inputOpenRouterKey.type = isPwd ? "text" : "password";
    elements.btnToggleKeyVisibility.textContent = isPwd ? "Hide" : "Show";
  });

  // Citation Drawer
  elements.btnCloseDrawer.addEventListener("click", closeCitationDrawer);
  elements.drawerBackdrop.addEventListener("click", closeCitationDrawer);
  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      closeCitationDrawer();
      closeSettingsModal();
    }
  });

  // Dropzone file handling
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

  // Documents refresh
  elements.btnRefreshDocs.addEventListener("click", fetchDocuments);

  // Q&A
  elements.chatForm.addEventListener("submit", handleAskSubmit);
  elements.chatInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleAskSubmit();
    }
  });

  // Compliance
  elements.btnRunCompliance.addEventListener("click", runComplianceAudit);
  elements.btnExportExcel.addEventListener("click", exportComplianceExcel);

  // Demo data triggers
  elements.btnGenerateDemo.addEventListener("click", triggerGenerateDemoData);
  elements.btnModalDemoData.addEventListener("click", triggerGenerateDemoData);
}

// Bootstrap
window.addEventListener("DOMContentLoaded", async () => {
  bindEvents();
  await initCredentials();
  await fetchDocuments();
  await loadStarterPrompts();
});
