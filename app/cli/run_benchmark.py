"""Production RAG Benchmark and Dilution Stress Test CLI runner.

Executes the canonical evaluation benchmark across multi-document corpora and
measures the RAG Triad, Text-to-SQL equivalence, and per-stage latency.

Usage:
  python -m app.cli.run_benchmark                               # Standard 10-solicitation benchmark
  python -m app.cli.run_benchmark --solicitation-count 15       # Scale to 15 solicitations
  python -m app.cli.run_benchmark --dilution-test               # Multi-scale stress test (N=1, 5, 10)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Optional

from app.evaluation.harness import BenchmarkHarness, BenchmarkReport
from app.evaluation.synthesizer import QuestionSynthesizer
from app.evaluation.workspace import provision_eval_workspace

logger = logging.getLogger("grantloom.cli.run_benchmark")


def format_table(headers: list[str], rows: list[list[str]]) -> str:
    """Formats a clean monospace ASCII table."""
    col_widths = [len(h) for h in headers]
    for row in rows:
        for idx, val in enumerate(row):
            if idx < len(col_widths):
                col_widths[idx] = max(col_widths[idx], len(str(val)))

    header_line = " | ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers))
    sep_line = "-+-".join("-" * col_widths[i] for i in range(len(headers)))
    data_lines = [" | ".join(str(row[i]).ljust(col_widths[i]) for i in range(len(headers))) for row in rows]
    return f"{header_line}\n{sep_line}\n" + "\n".join(data_lines)


def _fmt(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def ragas_group(report: BenchmarkReport) -> dict:
    """One exportable dataset group: samples plus the models that produced them."""
    return {
        "scale": report.solicitation_count,
        "writer_model": report.writer_model.split(":", 1)[-1],
        "pipeline_judge_model": report.pipeline_judge_model,
        "dataset": [
            {
                "test_id": r.test_id,
                "question": r.question,
                "contexts": r.contexts,
                "answer": r.actual_answer,
                "ground_truth": r.reference or r.expected,
                "abstained": r.abstained,
                "blocked": r.blocked,
            }
            for r in report.results
        ],
    }


def print_report_summary(report: BenchmarkReport) -> None:
    """Displays terminal summary report with Triad scores and latency breakdown."""
    print("\n" + "=" * 90)
    print(f"GRANTLOOM RAG BENCHMARK REPORT (Corpus: {report.solicitation_count} Solicitations, {report.document_count} Total Docs)")
    print(f"Writer: {report.writer_model} | Judge/Evaluator: {report.judge_model}")
    print("=" * 90)

    # 1. Per-test case breakdown
    headers = ["Test ID", "Type", "Status", "Context", "Faithful", "AnsRel", "Resolv", "Lat (ms)"]
    rows = []
    for r in report.results:
        status = "[PASS]" if r.passed else "[FAIL]"
        f_score = _fmt(r.triad.faithfulness)
        a_score = _fmt(r.triad.answer_relevance)
        rows.append([
            r.test_id[:16],
            r.question_type[:10],
            status,
            _fmt(r.triad.context_relevance),
            f_score,
            a_score,
            _fmt(r.triad.citation_resolvability),
            f"{r.timings.get('total_ms', 0):.0f}",
        ])
    print("\n" + format_table(headers, rows))

    # 2. RAG Triad Summary
    print("\n" + "-" * 45 + " RAG TRIAD METRICS " + "-" * 45)
    print(f"  Accuracy Rate:               {report.accuracy_rate * 100:.1f}% ({report.passed_cases}/{report.total_cases})")
    print(f"  Context Relevance:           {report.mean_context_relevance * 100:.1f}%")
    print(f"  Grounded Faithfulness:       {report.mean_faithfulness * 100:.1f}%")
    print(f"  Answer Relevance:            {report.mean_answer_relevance * 100:.1f}%")
    print(f"  Citation Resolvability:      {report.mean_resolvability * 100:.1f}%")

    # 3. Pipeline Latency Breakdown
    st = report.stage_latencies_ms
    print("\n" + "-" * 43 + " PER-STAGE LATENCY PROFILES " + "-" * 43)
    print(f"  Mean Total Latency:          {report.mean_latency_ms:.0f} ms  (P95: {report.p95_latency_ms:.0f} ms)")
    print(f"  |-- Guard (G1):              {st.get('guard_ms', 0):.0f} ms")
    print(f"  |-- Route (G2):              {st.get('route_ms', 0):.0f} ms")
    print(f"  |-- Retrieval (Dense+BM25):  {st.get('retrieve_ms', 0):.0f} ms")
    print(f"  |-- Context Filter (G3):     {st.get('filter_ms', 0):.0f} ms")
    print(f"  |-- Time to First Token:     {st.get('first_token_ms', 0):.0f} ms")
    print(f"  |-- Generation (Writer):     {st.get('generate_ms', 0):.0f} ms")
    print(f"  \\-- Verification (G4):       {st.get('verify_ms', 0):.0f} ms")
    print("=" * 90 + "\n")


async def run_single_benchmark(
    solicitation_count: int,
    writer_provider: str,
    writer_model: Optional[str] = None,
    judge_model: Optional[str] = None,
    evaluator_model: Optional[str] = None,
) -> BenchmarkReport:
    """Provisions a workspace with N solicitations and runs the benchmark suite."""
    print(f"\n[1/3] Provisioning isolated workspace with {solicitation_count} solicitations...", flush=True)
    workspace = await provision_eval_workspace(
        solicitation_count=solicitation_count,
        writer_provider=writer_provider,
        writer_model=writer_model,
        judge_model=judge_model,
        evaluator_model=evaluator_model,
    )
    try:
        print("[2/3] Loading canonical benchmark test suite...", flush=True)
        synthesizer = QuestionSynthesizer()
        suite = synthesizer.get_canonical_suite()

        print(f"[3/3] Running benchmark across {len(suite)} test cases...", flush=True)
        harness = BenchmarkHarness(workspace)
        return await harness.run_benchmark(suite)
    finally:
        workspace.cleanup()


async def run_dilution_study(
    writer_provider: str,
    writer_model: Optional[str] = None,
    judge_model: Optional[str] = None,
    evaluator_model: Optional[str] = None,
    scales: tuple[int, ...] = (1, 5, 10),
) -> list[BenchmarkReport]:
    """Runs the benchmark across increasing corpus sizes to measure dilution resistance."""
    reports: list[BenchmarkReport] = []
    for count in scales:
        print(f"\n>>> EXECUTING CORPUS DILUTION RUN: N={count} Solicitations <<<")
        rep = await run_single_benchmark(
            solicitation_count=count,
            writer_provider=writer_provider,
            writer_model=writer_model,
            judge_model=judge_model,
            evaluator_model=evaluator_model,
        )
        reports.append(rep)
        print_report_summary(rep)

    # Print Dilution Comparison Matrix
    print("\n" + "=" * 80)
    print("CORPUS DILUTION STRESS TEST COMPARISON")
    print("=" * 80)
    headers = ["Scale (N)", "Docs", "Accuracy", "ContextRel", "Faithful", "AnsRel", "MeanLat (ms)"]
    rows = []
    for r in reports:
        rows.append([
            f"N={r.solicitation_count}",
            str(r.document_count),
            f"{r.accuracy_rate * 100:.1f}%",
            f"{r.mean_context_relevance * 100:.1f}%",
            f"{r.mean_faithfulness * 100:.1f}%",
            f"{r.mean_answer_relevance * 100:.1f}%",
            f"{r.mean_latency_ms:.0f}",
        ])
    print(format_table(headers, rows))
    print("=" * 80 + "\n")
    return reports


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="GrantLoom RAG Evaluation & Dilution Benchmark")
    parser.add_argument("--solicitation-count", type=int, default=10, help="Number of solicitations (default: 10)")
    parser.add_argument("--dilution-test", action="store_true", help="Run dilution study across N=1, 5, 10 solicitations")
    parser.add_argument("--provider", choices=["openrouter", "ollama"], default="openrouter")
    parser.add_argument("--writer-model", default=None)
    parser.add_argument("--judge-model", default=None)
    parser.add_argument("--evaluator-model", default=None)
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--format", choices=["grantloom", "ragas"], default="grantloom", help="Output format for --json-out (grantloom or ragas)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)

    if args.dilution_test:
        reports = asyncio.run(run_dilution_study(
            writer_provider=args.provider,
            writer_model=args.writer_model,
            judge_model=args.judge_model,
            evaluator_model=args.evaluator_model,
        ))
        if args.json_out:
            if args.format == "ragas":
                data = [ragas_group(r) for r in reports]
            else:
                data = [{"solicitation_count": r.solicitation_count, "accuracy": r.accuracy_rate, "results": [r.__dict__ for r in r.results]} for r in reports]
            args.json_out.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
            print(f"Benchmark results written to {args.json_out} (format: {args.format})")
    else:
        report = asyncio.run(run_single_benchmark(
            solicitation_count=args.solicitation_count,
            writer_provider=args.provider,
            writer_model=args.writer_model,
            judge_model=args.judge_model,
            evaluator_model=args.evaluator_model,
        ))
        print_report_summary(report)
        if args.json_out:
            if args.format == "ragas":
                data = ragas_group(report)
            else:
                data = {
                    "solicitation_count": report.solicitation_count,
                    "accuracy": report.accuracy_rate,
                    "latencies": report.stage_latencies_ms,
                    "results": [r.__dict__ for r in report.results],
                }
            args.json_out.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
            print(f"Benchmark results written to {args.json_out} (format: {args.format})")


if __name__ == "__main__":
    main()
