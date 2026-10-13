# GrantLoom E2E — live deployment workflow tests + submission evidence

Playwright (headless Chromium) suite that exercises the **live public deployment**
(https://grantloom.taskmind-ai.com) exactly like a visitor would, plus the host
observability stack. This is milestone **M10** (demo rehearsal + capstone deliverables).

## What it covers

| # | Flow | Proves |
|---|------|--------|
| 1 | Landing + starter prompts | UI renders, `/api/v1/examples` served (was a 500: Docker packaging bug) |
| 2 | BYOK key validation | D28 validate-once-on-save (server-side probe, 🟢 feedback, no per-request probing) |
| 3 | Document repository | 6-document synthetic corpus active |
| 4 | Live ingestion | Upload → outbox stages → ACTIVE with real progress bar |
| 5 | Budget query | G1 llm_judge pass, `structured` route, SQL tool, G4 100% verified, citation drawer with sheet/row coordinates |
| 6 | Injection refusal | G1 blocks canonical injection **and the refusal actually renders** (regression: a `CancelledError` in the parallel G1∥G2 path once killed the SSE stream after `guard` — caught by this suite, fixed, unit-tested) |
| 7 | Ingestion→retrieval loop | Question about a fact from the doc uploaded minutes ago is answered with a citation into it |
| 8 | Compliance report | D29 streaming progress per requirement, Satisfied/Partial/Gap matrix, readiness score, .xlsx export |
| 9 | Grafana dashboard | `grantloom-obs` dashboard renders (10 panels) from host Prometheus |
| 10 | Deletion saga | 202 → purge across every store |
| 11 | Prometheus target | `up{job="grantloom"} = 1` |

## Run

```bash
python3 e2e/run_e2e.py          # ~6-8 min; screenshots + results.json into e2e/artifacts/
python3 e2e/make_submission_pdf.py   # -> e2e/GRANTLOOM_SUBMISSION.pdf (self-contained)
python3 e2e/setup_grafana_dashboard.py  # idempotent dashboard (re)creation
```

Secrets are read at runtime, never stored in this repo:
- OpenRouter BYOK key: `/root/.pi/agent/auth.json` → `/openrouter/key`
- Grafana admin: `docker inspect castops-grafana` env

Notes:
- Run **one instance at a time** — two concurrent runs dedupe each other's uploads and
  delete each other's test document.
- PNG/xlsx artifacts are gitignored (regenerable); `results.json` and the PDF are committed.
