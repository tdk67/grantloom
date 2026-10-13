#!/usr/bin/env python3
"""Create/update the GrantLoom observability dashboard on the host Grafana (M8 hook).

Credentials are read from the castops-grafana container env at runtime (never stored in the repo).
Panels use the existing "Prometheus (Latch)" datasource, which scrapes grantloom at 127.0.0.1:8092.
"""
import json
import subprocess
import sys
import urllib.request

GRAFANA = "http://127.0.0.1:3004"
DATASOURCE = {"type": "prometheus", "uid": "cg05pbjlebny8f"}
DASH_UID = "grantloom-obs"


def grafana_creds() -> tuple[str, str]:
    env = subprocess.check_output(
        ["docker", "inspect", "castops-grafana", "--format", "{{range .Config.Env}}{{println .}}{{end}}"],
        text=True,
    )
    user = password = None
    for line in env.splitlines():
        if line.startswith("GF_SECURITY_ADMIN_USER="):
            user = line.split("=", 1)[1]
        elif line.startswith("GF_SECURITY_ADMIN_PASSWORD="):
            password = line.split("=", 1)[1]
    if not user or not password:
        sys.exit("Grafana admin credentials not found in container env")
    return user, password


def api(user: str, password: str, method: str, path: str, payload: dict | None = None):
    req = urllib.request.Request(
        GRAFANA + path,
        method=method,
        data=json.dumps(payload).encode() if payload else None,
        headers={"Content-Type": "application/json"},
    )
    import base64
    req.add_header("Authorization", "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode())
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)


def panel(pid, title, ptype, targets, x, y, w, h, **extra):
    p = {
        "id": pid, "title": title, "type": ptype, "datasource": DATASOURCE,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "targets": [{"refId": chr(65 + i), "expr": t, "datasource": DATASOURCE} for i, t in enumerate(targets)],
        "fieldConfig": {"defaults": {"unit": extra.pop("unit", "short")}, "overrides": []},
    }
    p.update(extra)
    return p


def build_dashboard() -> dict:
    panels = [
        panel(1, "Documents by state", "stat", ["sum by (state) (grantloom_documents)"], 0, 0, 6, 5),
        panel(2, "Indexed chunks", "stat", ["grantloom_chunks"], 6, 0, 4, 5),
        panel(3, "Dense vectors", "stat", ["grantloom_vectors"], 10, 0, 4, 5),
        panel(4, "Decision fallbacks (judge → heuristic)", "stat",
              ["sum by (operation) (grantloom_decision_fallbacks_total)"], 14, 0, 5, 5),
        panel(5, "Guardrail verdicts by gate/outcome", "timeseries",
              ["sum by (gate, outcome) (grantloom_guard_verdicts_total)"], 19, 0, 5, 5),
        panel(6, "HTTP request rate by route/status", "timeseries",
              ["sum by (route, status) (rate(grantloom_http_request_duration_seconds_count[5m]))"],
              0, 5, 12, 8, unit="reqps"),
        panel(7, "p95 HTTP latency by route", "timeseries",
              ["histogram_quantile(0.95, sum by (le, route) (rate(grantloom_http_request_duration_seconds_bucket[15m])))"],
              12, 5, 12, 8, unit="s"),
        panel(8, "G2 router strategies", "timeseries",
              ["sum by (strategy) (grantloom_router_decisions_total)"], 0, 13, 8, 7),
        panel(9, "Retriever tool invocations", "timeseries",
              ["sum by (tool, outcome) (grantloom_tool_invocations_total)"], 8, 13, 8, 7),
        panel(10, "Ingestion job outcomes", "timeseries",
              ["sum by (outcome) (grantloom_ingestion_jobs_total)"], 16, 13, 8, 7),
    ]
    return {
        "dashboard": {
            "uid": DASH_UID,
            "title": "GrantLoom — RAG Appliance Observability",
            "tags": ["grantloom", "capstone", "rag"],
            "timezone": "browser",
            "refresh": "30s",
            "time": {"from": "now-3h", "to": "now"},
            "panels": panels,
        },
        "overwrite": True,
    }


if __name__ == "__main__":
    user, password = grafana_creds()
    result = api(user, password, "POST", "/api/dashboards/db", build_dashboard())
    print(json.dumps(result, indent=2))
