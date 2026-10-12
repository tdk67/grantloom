# GrantLoom — Frontend Design & User Interface Specification

**Document Version:** 1.1  
**Status:** Approved UI/UX Specification  
**Date:** 2026-10-13  
**Target Stack:** HTML5 / Semantic Modern CSS / Vanilla JavaScript (ES Modules)  
**Storage Engine:** Browser IndexedDB (Credentials, cached settings, offline UI state)  
**Build Tooling:** Zero Node.js build tools; served statically by FastAPI from `app/static/`  
**Companion Documents:** [PRD.md](PRD.md) | [implementation-plan.md](implementation-plan.md) | [decisions-log.md](decisions-log.md)

---

## 1. Design Philosophy, A11y & SEO Standards

### 1.1 Academic Rigor & Accessibility (WCAG 2.1 AA Compliant)
Researchers and grant evaluators require high contrast, clean typography, and completely predictable keyboard interactions:
* **Keyboard-Only Operation:** Every interactive element is reachable via logical `Tab` sequence.
  * `Skip to main content` link at top of DOM for screen readers and keyboard users.
  * Modals and slide-over drawers implement strict **Focus Traps** (`Esc` to dismiss; focus returns to the triggering button).
  * Clear visual focus ring (`outline: 2px solid var(--accent-blue); outline-offset: 2px`) across all interactive elements.
* **ARIA & Screen Reader Semantics:**
  * Real-time SSE streaming status updates use `aria-live="polite"` and `role="status"`.
  * Accordions and drawers use `aria-expanded` and `aria-controls`.
  * Modals use `role="dialog"` with `aria-modal="true"` and `aria-labelledby`.

### 1.2 SEO, Crawler Headers & AI Indexing (`robots.txt`, `sitemap.xml`, `llms.txt`)
Even as a self-hosted single-tenant tool, the public landing interface includes complete metadata for institutional discovery and AI crawlers:
* **Semantic `<header>` and Meta Tags:**
  * `<title>GrantLoom — Verifiable RAG & Grant Compliance Engine</title>`
  * `<meta name="description" content="Self-hosted RAG appliance for academic research labs. Cross-references funding solicitations with institutional documents using verifiable citations and compliance matrices.">`
  * `<meta name="keywords" content="academic RAG, grant compliance, NOFO analysis, NIH R01 audit, verifiable citations, Kuzu graph, ChromaDB">`
  * OpenGraph / Twitter card metadata with canonical link tag.
* **Crawler Artifacts Served at Root:**
  * `/robots.txt`: Defines crawler directives allowing discovery of public docs and disallowing `/api/`.
  * `/sitemap.xml`: XML sitemap indexing landing page and documentation.
  * `/llms.txt`: Structured plain-text index for AI assistants describing the project, capabilities, endpoints, and architectural principles.

---

## 2. Storage & Credential Security (Browser IndexedDB)

All persistent client-side state is managed through the browser's native **IndexedDB** (`grantloom_db`), avoiding the plain-text string limitations of `localStorage`.

### 2.1 Object Stores
1. `credentials`: Stores `openrouter_api_key`, `server_api_key`, `provider_type` (`openrouter` | `ollama`), and `ollama_endpoint`.
2. `settings`: Stores theme preference, active local model, and custom retrieval top-k values.
3. `active_session`: Ephemeral query drafts and active solicitation selection.

### 2.2 Live Credential Validation & Interaction Lockout
* When the user enters their OpenRouter API key, the UI immediately invokes `GET https://openrouter.ai/api/v1/auth/key`:
  * **Valid with balance:** Key saved to IndexedDB; status chip updates to `🟢 OpenRouter Connected ($X.XX balance)`.
  * **Invalid or zero balance:** Displays error banner (`401 Invalid Key` or `402 Insufficient Balance`).
* **Interaction Lockout:** Without a verified API key (or active local Ollama connection):
  * The upload dropzone is disabled with a prominent banner: *"Enter a verified OpenRouter Key or connect Ollama in Settings to enable ingestion"*.
  * The Q&A prompt textarea is disabled with a button: *"⚙️ Configure API Key to Ask Questions"*.

---

## 3. Local Model Integration (Ollama Mode)

While OpenRouter is the default, the UI supports a seamless toggle to **Local Ollama**:

### 3.1 Model Enumeration & Tool-Call Capability Probe
1. **Model Discovery:** When the user selects "Local Ollama" in Settings, the frontend requests `GET {ollama_endpoint}/api/tags` to populate a `<select>` dropdown with locally installed models (e.g. `llama3.1:8b`, `mistral:7b`, `qwen2.5:7b`).
2. **Short Tool-Calling Probe:** Upon selecting a local model, the UI triggers a quick diagnostic probe via backend (`POST /api/v1/admin/test-model-capabilities`):
   * Tests whether the local model correctly returns valid JSON function calls / structured outputs.
   * **Pass:** Displays `🟢 Tool calling supported`.
   * **Fail:** Displays `⚠️ Model lacks structured output support — using extractive fallback mode`.
3. **Dynamic Context Window Adaptation:**
   * Local models operate with smaller context windows (typically 4k–8k tokens vs 128k cloud).
   * When Ollama is active, the frontend surfaces a context indicator (`Context Budget: 4,096 tokens`) and the backend automatically reduces chunk retrieval budget to prevent context overflow.

---

## 4. UI Architecture & The 5 Workspace Views

```
+----------------------------------------------------------------------------------------------------+
| [Logo] GrantLoom 🧶    Workspace: Default    [Provider: OpenRouter 🟢]    [Demo Data 🪄]   [Settings ⚙️] |
+----------------------+-----------------------------------------------------------------------------+
| NAVIGATION (Tabs)    | MAIN WORKSPACE                                                              |
|                      |                                                                             |
| [1. 📄 Documents]    | (Switches between:                                                          |
| [2. 💬 Research Q&A] |  View 1: Document Ingestion, Polling & Quarantine                          |
| [3. 📊 Compliance]   |  View 2: Citation-Verified Q&A + Confidence Score                          |
| ──────────────────── |  View 3: NOFO Compliance Matrix & Excel Export                             |
| STARTER PROMPTS      |  View 4: Slide-over Citation Excerpt Drawer                                 |
| • Cost match rule    |  View 5: BYOK & Ollama Settings Modal)                                     |
| • Facilities match   |                                                                             |
| • Travel budget sum  |                                                                             |
+----------------------+-----------------------------------------------------------------------------+
```

---

### View 1: Document Ingestion, Polling & Quarantine (`#view-documents`)
* **Dropzone:** Accessible drag-and-drop zone (`role="region"` `aria-label="File Upload Dropzone"`). Supports keyboard file trigger via `Enter`/`Space`.
* **Real-Time Polling Progress:** Dynamic progress bar reflecting the SQLite outbox pipeline (`extracting` → `chunking` → `indexing`) with current page counts.
* **Document Table:**
  * Columns: File Name, Type, Size, Pages, Version, Status, Actions.
  * Status chips: `Active` 🟢, `Processing` 🔵, `Quarantined` 🔴.
* **Quarantine Review Drawer:** Inspects tainted documents, highlights flagged prompt-injection text spans, and offers a manual "Release Document" or "Permanently Delete" button.

---

### View 2: Citation-Verified Q&A with Confidence Score (`#view-qa`)
* **Status Trail (SSE Header):** Live stage pills displaying canonical pipeline events: `[✓ Guard] [✓ Route: Hybrid] [✓ Retrieve: 6 chunks] [✓ Verify Citations]`.
* **Confidence Level Badge:** Every answer includes an explicit confidence metric:
  * `🟢 High Confidence (94%)` — All claims grounded in retrieved chunks.
  * `🟡 Moderate Confidence (76%)` — Minor claim required rewrite self-correction.
  * `🔴 Low / Warning (52%)` — One or more claims unverified against source context.
* **Clickable Inline Citation Badges:** Badges rendered as `[DOE_Solar.pdf, p. 14]`. Clicking or pressing `Enter` on any badge opens the **Citation Excerpt Drawer** with matching keywords automatically highlighted.
* **Warning Badges:** Unverified claims are highlighted in amber: `⚠️ Unverified Claim — Source evidence could not be verified`.
* **Abstention Callout:** Prominent slate callout when retrieval is empty: *"This information was not found in your indexed documents"*.

---

### View 3: NOFO Compliance & Gap Matrix (`#view-compliance`)
* **Solicitation Dropdown:** Select any indexed solicitation.
* **Readiness Scorecard:** High-contrast gauge showing overall readiness: `[ 74 / 100 ]` with disclaimer: *"Readiness Estimate based on indexed documents. Not an official legal or funding guarantee."*
* **Interactive Matrix Table:**
  * Columns: Requirement ID, Requirement Text, Lab Evidence Match, Status Badge (`Satisfied` 🟢, `Partial` 🟡, `Gap` 🔴), Citations.
  * Status filter tabs: `All (18)`, `Satisfied (12)`, `Partial (3)`, `Gaps (3)`.
* **Export Action:** Prominent button: `📥 Export to Excel (.xlsx)` triggering instant download of the audit-ready workbook.

---

### View 4: Citation Excerpt Slide-Over Drawer
* **Activation:** Triggered by clicking any citation badge or pressing `Enter` on it.
* **Focus Trap & Keyboard Behavior:** Focus moves immediately to drawer header; `Esc` closes drawer and returns focus to the clicked citation badge.
* **Content:**
  * Header: Document name, page number, and content hash.
  * **Keyword Highlighting:** Text chunk rendered with query keywords wrapped in `<mark class="kw-highlight">` for instant visual verification.
  * Surrounding Context: Shows the full chunk paragraph so researchers can verify surrounding nuances.

---

### View 5: Settings & Credential Modal
* **Provider Switch:** Tabbed toggle between `Cloud (OpenRouter)` and `Local (Ollama)`.
* **OpenRouter Controls:**
  * Password-masked API key field with show/hide toggle.
  * `Test Key` button that verifies balance and key validity live.
* **Ollama Controls:**
  * Endpoint URL field (default: `http://localhost:11434`).
  * `Refresh Models` button querying `/api/tags` into a dropdown.
  * `Test Tool Calling` diagnostic button verifying structured JSON support.
* **Demo Data Generation Button:** One-click button: `🪄 Generate Demo Sample Documents` invoking `/api/v1/admin/generate-synthetic-data` to populate sample NOFOs, past proposals, and budget sheets into the demo workspace on demand.

---

## 5. Semantic Design Tokens & CSS Utilities

```css
:root {
  /* Color Palette - Neutral Slate with Academic Teal */
  --bg-primary: #0f172a;       /* Deep slate base */
  --bg-surface: #1e293b;       /* Panel and card surface */
  --bg-surface-elevated: #334155;
  --border-subtle: #334155;
  --border-focus: #0ea5e9;

  --text-primary: #f8fafc;
  --text-secondary: #94a3b8;
  --text-muted: #64748b;

  --accent-teal: #14b8a6;
  --accent-blue: #0284c7;
  --highlight-bg: #fef08a;     /* Yellow keyword highlight */
  --highlight-text: #854d0e;

  --status-success: #10b981;
  --status-warning: #f59e0b;
  --status-danger: #ef4444;

  /* Typography */
  --font-sans: 'Inter', system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
  --font-mono: 'JetBrains Mono', monospace;

  /* Accessibility & Focus */
  --focus-ring: 2px solid #38bdf8;
  --focus-offset: 2px;
}

/* Accessible focus styling */
:focus-visible {
  outline: var(--focus-ring);
  outline-offset: var(--focus-offset);
}

/* Keyword highlighting inside citation drawer */
mark.kw-highlight {
  background-color: var(--highlight-bg);
  color: var(--highlight-text);
  padding: 0.1em 0.25em;
  border-radius: 2px;
  font-weight: 600;
}
```
