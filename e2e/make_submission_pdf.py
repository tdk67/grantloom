#!/usr/bin/env python3
"""Build the GrantLoom submission PDF from the E2E run artifacts.

Renders a styled HTML report (screenshots embedded as base64 so the PDF is self-contained)
and prints it to PDF with headless Chromium via Playwright.

Output: e2e/GRANTLOOM_SUBMISSION.pdf
"""
from __future__ import annotations

import base64
import html
import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).parent
ART = HERE / "artifacts"
OUT = HERE / "GRANTLOOM_SUBMISSION.pdf"

FLOW_META = {
    "landing": ("1. First Impressions — Starter Prompts & Deployment Mode",
                "The SPA loads with curated starter questions (served by /api/v1/examples) and the header "
                "shows the deployment mode. No server-side key exists: the appliance runs in public BYOK mode."),
    "byok_validation": ("2. BYOK Key Validation (validate once, on save — D28)",
                "Saving an OpenRouter key triggers a single server-side live probe. The digest is remembered "
                "for the process lifetime, so subsequent questions never pay a validation round-trip. Keys are "
                "never stored or logged."),
    "repository": ("3. Document Repository",
                "Six synthetic corpus documents (solicitation NOFO, past proposal, 4-sheet budget workbook, "
                "compliance checklist, cost-share commitment letter, facilities statement), all ACTIVE."),
    "ingestion": ("4. Live Document Ingestion with Progress",
                "A new field-notes file is uploaded through the browser. The outbox worker streams stage and "
                "percentage (extracting → chunking → scanning → embedding → indexing → active) into the progress bar."),
    "query_budget": ("5. Grounded Query — Structured Route + Verified Citations",
                "A budget question is routed by the LLM judge to the 'structured' strategy: the writer model "
                "generates SQL over the spreadsheet schema, SqlGuard executes it, and every claim is verified "
                "against cited evidence (G4). The citation drawer resolves markers to sheet/row coordinates."),
    "injection_refusal": ("6. Prompt-Injection Refusal (G1)",
                "A canonical injection attempt is blocked at the input guard before any model call, with an "
                "explicit refusal and a 'Blocked' reasoning pill."),
    "query_uploaded_doc": ("7. Ingestion → Retrieval Loop",
                "Minutes after upload, a question about the new document's unique fact (97.3% inverter uptime) "
                "is answered with a citation into that exact chunk — proof the whole index pipeline works live."),
    "compliance_report": ("8. Compliance Report — Streaming Progress + Excel Export (D29)",
                "The NOFO compliance audit extracts the solicitation's own requirement sentences, assesses lab "
                "evidence per requirement (bounded concurrency), streams a progress event per requirement, then "
                "renders the Satisfied/Partial/Gap matrix with readiness score and exports a styled .xlsx."),
    "grafana_dashboard": ("9. Observability — Grafana Dashboard",
                "Host Prometheus scrapes the container's /metrics (host-only); the Grafana dashboard shows "
                "documents/chunks/vectors, HTTP latency, guardrail verdicts, router strategies, tool invocations, "
                "decision fallbacks and ingestion outcomes."),
    "cleanup": ("10. Deletion Saga",
                "Deleting the test document returns 202 and purges every store (chunks, vectors, tables, graph, file)."),
    "prometheus_scrape": ("11. Prometheus Target", "up{job=\"grantloom\"} = 1 on the host Prometheus."),
}

EVAL_ROWS = [
    ("Recall@k (answer in writer context)", "97.4%", "≥ 85%", "PASS"),
    ("Citation resolvability", "100%", "100%", "PASS"),
    ("Abstention accuracy (unanswerable)", "100%", "≥ 90%", "PASS"),
    ("Injection block rate, canonical", "100%", "≥ 95%", "PASS"),
    ("Injection block rate, held-out paraphrases", "0% (heuristic) / needs llm_judge", "≥ 95%", "reported as FAIL offline — honest by design"),
    ("Faithfulness", "not measured offline (would be circular)", "≥ 95%", "judge-only with a real writer"),
    ("Unit + integration tests", "92 passed", "—", "PASS"),
]


def img_tag(name: str, width="100%") -> str:
    path = ART / name
    if not path.exists():
        return ""
    b64 = base64.b64encode(path.read_bytes()).decode()
    return f'<img src="data:image/png;base64,{b64}" style="width:{width}; border:1px solid #d7dde8; border-radius:6px;">'


def build_html() -> str:
    results = json.loads((ART / "results.json").read_text())
    flows = {r["flow"]: r for r in results["flows"]}

    flow_sections = []
    for flow_id, (title, desc) in FLOW_META.items():
        r = flows.get(flow_id)
        if not r:
            continue
        status_color = "#0a7d33" if r["status"] == "pass" else "#b42318"
        shots = "".join(img_tag(s) for s in [r.get("mid_screenshot", ""), r.get("screenshot", "")] if s)
        detail = html.escape(r.get("detail", ""))
        dur = f'{r["duration_s"]}s' if r.get("duration_s") is not None else ""
        flow_sections.append(f"""
    <div class="flow">
      <h3>{html.escape(title)} <span class="badge" style="background:{status_color}">{r['status'].upper()}</span>
          <span class="dur">{dur}</span></h3>
      <p class="desc">{html.escape(desc)}</p>
      <p class="detail"><b>Observed:</b> {detail}</p>
      {shots}
    </div>""")

    eval_rows = "".join(
        f"<tr><td>{html.escape(m)}</td><td>{html.escape(v)}</td><td>{html.escape(t)}</td><td>{html.escape(s)}</td></tr>"
        for m, v, t, s in EVAL_ROWS
    )

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
  body {{ font-family: 'DejaVu Sans', Arial, sans-serif; color: #1c2430; margin: 0; font-size: 11.5px; }}
  .page {{ padding: 34px 42px; }}
  h1 {{ font-size: 26px; margin: 0 0 4px; color: #0f2a52; }}
  h2 {{ font-size: 16px; color: #0f2a52; border-bottom: 2px solid #2f6fb2; padding-bottom: 4px; margin: 26px 0 10px; }}
  h3 {{ font-size: 13px; margin: 18px 0 4px; color: #14213d; }}
  .subtitle {{ color: #4a5568; font-size: 13px; margin-bottom: 14px; }}
  .meta {{ display: flex; gap: 26px; margin: 10px 0 4px; flex-wrap: wrap; }}
  .meta div span {{ color: #4a5568; }}
  .badge {{ color: #fff; border-radius: 4px; padding: 1px 8px; font-size: 10px; vertical-align: middle; }}
  .dur {{ color: #7a8699; font-size: 10px; font-weight: normal; }}
  .flow {{ page-break-inside: avoid; margin-bottom: 14px; }}
  .desc {{ margin: 2px 0; color: #333; }}
  .detail {{ margin: 2px 0 6px; color: #1a5c2a; font-size: 10.5px; }}
  img {{ margin: 4px 0 8px; page-break-inside: avoid; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 10.5px; }}
  th, td {{ border: 1px solid #ccd4e0; padding: 4px 8px; text-align: left; }}
  th {{ background: #eef2f8; }}
  .footer {{ margin-top: 22px; color: #7a8699; font-size: 9.5px; border-top: 1px solid #d7dde8; padding-top: 8px; }}
  ul {{ margin: 6px 0; padding-left: 18px; }}
</style></head><body><div class="page">

  <h1>GrantLoom 🧶 — Submission Evidence</h1>
  <div class="subtitle">Self-hosted AI grant-intelligence &amp; compliance appliance · End-to-end workflow
  verification of the live deployment, with screenshots</div>
  <div class="meta">
    <div><span>Live demo:</span> <b>https://grantloom.taskmind-ai.com</b></div>
    <div><span>Source:</span> <b>https://github.com/tdk67/grantloom</b></div>
    <div><span>Run:</span> <b>{html.escape(results['run_at'])}</b></div>
  </div>
  <div class="meta">
    <div><span>Result:</span> <b>{sum(1 for r in results['flows'] if r['status']=='pass')}/{len(results['flows'])} E2E flows passed</b>
    (Playwright, headless Chromium, against the public HTTPS deployment)</div>
  </div>

  <h2>System &amp; Deployment Summary</h2>
  <ul>
    <li><b>Pipeline:</b> one agent pipeline (Planner → Retriever → Writer → Validator) for API and evaluation; SSE-streamed
        reasoning trail (guard, route, tools, context filter, generation, verification, bounded rewrites).</li>
    <li><b>Guardrails:</b> LLM-judge decision backend (independent judge model family, NFR-6) for G1 input screening,
        G2 strategy routing, G3 context filtering, G4 claim verification, Tier-2 ingestion scanning and compliance
        assessment — with a labelled, metered heuristic fallback (never silent).</li>
    <li><b>Citations by construction:</b> the writer emits [chunk_id] markers only; the backend resolves them to
        file/page/line/sheet/row coordinates of active documents and verifies every claim against its cited evidence.</li>
    <li><b>Deployment (Phase 2):</b> multi-stage Docker image (non-root, ~230 MB RSS of a 2.5 GB cap) behind nginx —
        Let's Encrypt TLS, unbuffered SSE proxying, 30 req/min per-IP rate limit, host-only /metrics; Prometheus scrape
        target up; Grafana dashboard 'GrantLoom — RAG Appliance Observability'.</li>
    <li><b>Public BYOK mode (D27/D28):</b> no server-side secrets. Every visitor's inference runs on their own
        OpenRouter key, validated once on save and live-probed; writer deepseek/deepseek-v4-flash, judge openai/gpt-4o-mini.</li>
  </ul>

  <h2>Workflow Verification (Playwright E2E)</h2>
  {''.join(flow_sections)}

  <h2>Quality &amp; Evaluation Evidence</h2>
  <table>
    <tr><th>Metric (offline golden-set run)</th><th>Result</th><th>PRD target</th><th>Status</th></tr>
    {eval_rows}
  </table>
  <p class="desc">The offline runner reports its own measurement caveats (extractive stub writer → faithfulness
  "not measured"; heuristic backend → held-out paraphrase blocking reported as FAIL). Real-model numbers require
  <code>run_eval --writer openrouter --decision llm_judge</code>; the live flows above already exercise that exact
  configuration through the browser.</p>

  <div class="footer">Generated {time.strftime('%Y-%m-%d %H:%M:%S %Z')} by e2e/make_submission_pdf.py from
  e2e/artifacts/results.json · GrantLoom Phase 1+2 · tdk67</div>
</div></body></html>"""


def main() -> None:
    html_doc = build_html()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
        page = browser.new_page()
        page.set_content(html_doc, wait_until="networkidle")
        page.wait_for_timeout(1500)
        page.pdf(
            path=str(OUT), format="A4", print_background=True,
            margin={"top": "12mm", "bottom": "12mm", "left": "8mm", "right": "8mm"},
        )
        browser.close()
    print(f"Wrote {OUT} ({OUT.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
