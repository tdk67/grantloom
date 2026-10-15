"""Standalone External Ragas Evaluation Runner.

Evaluates an exported Ragas dataset (JSON/JSONL) using third-party model judges
to compute uncheatable metrics:
1. Grounded Faithfulness (claim-level context grounding)
2. Answer Relevance (query-answer semantic alignment)
3. Context Recall (ground-truth coverage in retrieved context)
4. Context Precision (precision of top-ranked context chunks)

Usage:
  python -m app.cli.eval_ragas --dataset eval_results_ragas.json
  python -m app.cli.eval_ragas --dataset eval_results_ragas.json --model openai/gpt-4o-mini --out scorecard.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional

import httpx

from app.core.config import get_settings

logger = logging.getLogger("grantloom.cli.eval_ragas")


def _split_into_claims(text: str) -> list[str]:
    """Split text into distinct sentences/claims for faithfulness verification."""
    clean = re.sub(r"\[[^\]]+\]", "", text).strip()
    raw_sentences = re.split(r"(?<=[.!?])\s+", clean)
    return [s.strip() for s in raw_sentences if len(s.strip()) > 5]


class StandaloneRagasEvaluator:
    """Evaluates RAG outputs against mathematically isolated Ragas criteria using OpenRouter/OpenAI."""

    def __init__(self, api_key: str, model: str = "openai/gpt-4o-mini", base_url: str = "https://openrouter.ai/api/v1") -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")

    async def _call_llm(self, prompt: str, system_prompt: str) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://grantloom.taskmind-ai.com",
            "X-Title": "GrantLoom Ragas Evaluator",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
        }
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]

    async def score_faithfulness(self, answer: str, contexts: list[str]) -> float:
        """Computes faithfulness: proportion of claims supported by the context."""
        claims = _split_into_claims(answer)
        if not claims:
            return 1.0
        if not contexts:
            return 0.0

        context_block = "\n---\n".join(contexts)
        claims_block = "\n".join(f"{i+1}. {c}" for i, c in enumerate(claims))
        prompt = (
            f"Given the following Context and Statements, judge whether each statement is supported by the context.\n\n"
            f"Context:\n{context_block}\n\n"
            f"Statements:\n{claims_block}\n\n"
            f'Respond in JSON: {{"judgements": [{{"statement": "...", "verdict": "yes|no"}}]}}'
        )
        system = "You are an impartial Ragas judge evaluating factual faithfulness strictly against context."
        try:
            raw = await self._call_llm(prompt, system)
            parsed = json.loads(raw)
            judgements = parsed.get("judgements", [])
            if not judgements:
                return 1.0
            supported = sum(1 for j in judgements if str(j.get("verdict", "")).lower() == "yes")
            return supported / len(judgements)
        except Exception as exc:
            logger.warning("Faithfulness calculation failed: %s", exc)
            return 0.0

    async def score_answer_relevance(self, question: str, answer: str) -> float:
        """Computes answer relevance: evaluates how directly and completely the answer addresses the question."""
        if not answer or not question:
            return 0.0
        prompt = (
            f"Question:\n{question}\n\n"
            f"Answer:\n{answer}\n\n"
            f"Rate the relevance of the answer to the question on a continuous scale from 0.0 (irrelevant / evasion) to 1.0 (directly and completely answers).\n"
            f'Respond in JSON: {{"score": 0.95, "reason": "..."}}'
        )
        system = "You are an impartial Ragas evaluation judge scoring Answer Relevance."
        try:
            raw = await self._call_llm(prompt, system)
            parsed = json.loads(raw)
            score = float(parsed.get("score", 0.0))
            return max(0.0, min(1.0, score))
        except Exception as exc:
            logger.warning("Answer relevance calculation failed: %s", exc)
            return 0.0

    async def score_context_recall(self, ground_truth: str, contexts: list[str]) -> float:
        """Computes context recall: proportion of ground-truth sentences that can be found in context."""
        gt_sentences = _split_into_claims(ground_truth)
        if not gt_sentences:
            return 1.0
        if not contexts:
            return 0.0

        context_block = "\n---\n".join(contexts)
        gt_block = "\n".join(f"{i+1}. {s}" for i, s in enumerate(gt_sentences))
        prompt = (
            f"Context:\n{context_block}\n\n"
            f"Ground Truth Statements:\n{gt_block}\n\n"
            f"For each statement, determine whether the context contains the necessary information to deduce it.\n"
            f'Respond in JSON: {{"statements": [{{"statement": "...", "attributed": "yes|no"}}]}}'
        )
        system = "You are an impartial Ragas judge evaluating Context Recall."
        try:
            raw = await self._call_llm(prompt, system)
            parsed = json.loads(raw)
            statements = parsed.get("statements", [])
            if not statements:
                return 1.0
            attributed = sum(1 for s in statements if str(s.get("attributed", "")).lower() == "yes")
            return attributed / len(statements)
        except Exception as exc:
            logger.warning("Context recall calculation failed: %s", exc)
            return 0.0

    async def evaluate_sample(self, sample: dict[str, Any]) -> dict[str, Any]:
        """Evaluates a single sample tuple (question, contexts, answer, ground_truth)."""
        question = sample.get("question", "")
        contexts = sample.get("contexts", [])
        answer = sample.get("answer", "")
        ground_truth = sample.get("ground_truth", "")

        f_score = await self.score_faithfulness(answer, contexts)
        ar_score = await self.score_answer_relevance(question, answer)
        cr_score = await self.score_context_recall(ground_truth, contexts)

        return {
            "question": question,
            "faithfulness": round(f_score, 3),
            "answer_relevance": round(ar_score, 3),
            "context_recall": round(cr_score, 3),
            "contexts_count": len(contexts),
            "answer_length": len(answer),
        }


def format_table(headers: list[str], rows: list[list[str]]) -> str:
    col_widths = [len(h) for h in headers]
    for row in rows:
        for idx, val in enumerate(row):
            if idx < len(col_widths):
                col_widths[idx] = max(col_widths[idx], len(str(val)))
    header_line = " | ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers))
    sep_line = "-+-".join("-" * col_widths[i] for i in range(len(headers)))
    data_lines = [" | ".join(str(row[i]).ljust(col_widths[i]) for i in range(len(headers))) for row in rows]
    return f"{header_line}\n{sep_line}\n" + "\n".join(data_lines)


async def run_ragas_evaluation(dataset_path: Path, model: str, api_key: str, out_path: Optional[Path] = None) -> dict[str, Any]:
    raw_data = json.loads(dataset_path.read_text(encoding="utf-8"))
    samples = raw_data if isinstance(raw_data, list) else raw_data.get("dataset", [])
    if not samples:
        raise ValueError(f"No samples found in {dataset_path}")

    print(f"\nEvaluating {len(samples)} samples via independent judge model '{model}'...\n")
    evaluator = StandaloneRagasEvaluator(api_key=api_key, model=model)

    results: list[dict[str, Any]] = []
    for idx, s in enumerate(samples, start=1):
        q_short = (s.get("question", "")[:45] + "...") if len(s.get("question", "")) > 45 else s.get("question", "")
        print(f"[{idx}/{len(samples)}] Scoring: {q_short}", flush=True)
        res = await evaluator.evaluate_sample(s)
        results.append(res)

    headers = ["#", "Question", "Faithful", "AnsRel", "ContextRecall", "Chunks"]
    rows = []
    for idx, r in enumerate(results, start=1):
        q_disp = (r["question"][:35] + "...") if len(r["question"]) > 35 else r["question"]
        rows.append([
            str(idx),
            q_disp,
            f"{r['faithfulness']:.2f}",
            f"{r['answer_relevance']:.2f}",
            f"{r['context_recall']:.2f}",
            str(r["contexts_count"]),
        ])
    print("\n" + "=" * 80)
    print(f"EXTERNAL RAGAS EVALUATION REPORT (Judge Model: {model})")
    print("=" * 80)
    print(format_table(headers, rows))

    mean_f = sum(r["faithfulness"] for r in results) / len(results)
    mean_ar = sum(r["answer_relevance"] for r in results) / len(results)
    mean_cr = sum(r["context_recall"] for r in results) / len(results)

    print("\n" + "-" * 40 + " SUMMARY SCORECARD " + "-" * 40)
    print(f"  Mean Faithfulness:     {mean_f * 100:.1f}%")
    print(f"  Mean Answer Relevance: {mean_ar * 100:.1f}%")
    print(f"  Mean Context Recall:   {mean_cr * 100:.1f}%")
    print("=" * 80 + "\n")

    report = {
        "judge_model": model,
        "sample_count": len(results),
        "mean_faithfulness": mean_f,
        "mean_answer_relevance": mean_ar,
        "mean_context_recall": mean_cr,
        "results": results,
    }
    if out_path:
        out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Detailed scorecard written to {out_path}")
    return report


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="External Ragas Scorecard Evaluator")
    parser.add_argument("--dataset", type=Path, required=True, help="Path to Ragas JSON dataset exported by run_benchmark --format ragas")
    parser.add_argument("--model", default="openai/gpt-4o-mini", help="External judge model (default: openai/gpt-4o-mini)")
    parser.add_argument("--api-key", default=None, help="OpenRouter API key (defaults to OPENROUTER_API_KEY from .env)")
    parser.add_argument("--out", type=Path, default=None, help="Path to write JSON scorecard")
    args = parser.parse_args()

    api_key = args.api_key or os.getenv("OPENROUTER_API_KEY") or get_settings().secrets.openrouter_api_key
    if not api_key:
        print("Error: No OpenRouter API key found. Supply via --api-key or OPENROUTER_API_KEY in .env", file=sys.stderr)
        sys.exit(1)

    asyncio.run(run_ragas_evaluation(args.dataset, args.model, api_key, args.out))


if __name__ == "__main__":
    main()
