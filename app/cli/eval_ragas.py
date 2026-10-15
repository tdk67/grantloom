"""External Ragas-style scorecard for an exported benchmark dataset.

Metrics (LLM-judged, one judge call per metric per sample):
1. Faithfulness: share of answer claims supported by the retrieved contexts.
2. Answer relevance: how directly the answer addresses the question.
3. Context recall: share of reference-answer statements deducible from the contexts.
4. Context precision: average precision over the ranked contexts judged useful for the reference.

Rules that keep the numbers honest:
- Guardrail refusals and abstentions are skipped (they carry no claims), never scored 0 or 1.
- A judge failure (HTTP, parse, empty verdict) marks the sample as an error and drops it from the
  means; it is never converted to a score. The report counts evaluated / skipped / errors.
- The judge model comes from config.json (`evaluation.judge_model`) and must differ in family from
  the writer, the pipeline judge and the decision model.

Usage:
  python -m app.cli.run_benchmark --json-out ds.json --format ragas
  python -m app.cli.eval_ragas --dataset ds.json [--model <id>] [--out scorecard.json]
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

from app.adapters.openrouter_client import OpenRouterClientAdapter
from app.core.config import Settings, get_settings, model_family
from app.ports.llm_client import LLMClientPort

logger = logging.getLogger("grantloom.cli.eval_ragas")

METRICS = ("faithfulness", "answer_relevance", "context_recall", "context_precision")
JUDGE_SYSTEM = "You are an impartial evaluation judge. Respond strictly in valid JSON."


class JudgeError(RuntimeError):
    """The judge call failed or returned an unusable verdict."""


def split_into_claims(text: str) -> list[str]:
    """Split text into sentences for claim-level verification (citation markers removed)."""
    clean = re.sub(r"\[[^\]]+\]", "", text).strip()
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", clean) if len(s.strip()) > 5]


def independence_problems(judge: str, others: dict[str, Optional[str]]) -> list[str]:
    """NFR-6: the Ragas judge must not share a model family with any model under test."""
    problems = []
    for role, model in others.items():
        if model and model_family(model) == model_family(judge):
            problems.append(f"judge '{judge}' shares model family '{model_family(judge)}' with the {role} '{model}'")
    return problems


def _render_numbered(items: list[str]) -> str:
    return "\n".join(f"{i + 1}. {t}" for i, t in enumerate(items))


class RagasJudge:
    """LLM-as-judge scorer. Every method raises JudgeError instead of returning a fallback score."""

    def __init__(self, client: LLMClientPort, model: str, api_key: str) -> None:
        self.client = client
        self.model = model
        self.api_key = api_key

    async def _ask(self, prompt: str) -> dict[str, Any]:
        try:
            raw = await self.client.generate_response(
                prompt=prompt, model=self.model, system_prompt=JUDGE_SYSTEM,
                temperature=0.0, api_key=self.api_key, json_output=True,
            )
            parsed = json.loads(raw)
        except Exception as exc:  # network, auth, upstream or invalid JSON: all are judge failures
            raise JudgeError(f"{type(exc).__name__}: {exc}") from exc
        if not isinstance(parsed, dict):
            raise JudgeError(f"judge returned non-object JSON: {parsed!r}")
        return parsed

    @staticmethod
    def _yes_ratio(parsed: dict[str, Any], key: str, flag: str, expected: int) -> float:
        rows = parsed.get(key)
        if not isinstance(rows, list) or not rows:
            raise JudgeError(f"judge returned no '{key}'")
        yes = sum(1 for r in rows if isinstance(r, dict) and str(r.get(flag, "")).strip().lower() == "yes")
        # Denominator is the number of statements sent, so a judge that drops statements cannot inflate the score.
        return yes / expected

    async def faithfulness(self, answer: str, contexts: list[str]) -> Optional[float]:
        claims = split_into_claims(answer)
        if not claims:
            return None  # nothing to verify: not applicable, not 1.0
        if not contexts:
            return 0.0  # factual claims with no retrieved evidence are unsupported
        prompt = (
            "Judge whether each statement is supported by the context.\n\n"
            f"Context:\n{chr(10).join(contexts)}\n\nStatements:\n{_render_numbered(claims)}\n\n"
            'Respond in JSON: {"judgements": [{"statement": "...", "verdict": "yes|no"}]}'
        )
        return self._yes_ratio(await self._ask(prompt), "judgements", "verdict", len(claims))

    async def answer_relevance(self, question: str, answer: str) -> float:
        prompt = (
            f"Question:\n{question}\n\nAnswer:\n{answer}\n\n"
            "Rate how directly and completely the answer addresses the question, from 0.0 (irrelevant "
            'or evasive) to 1.0 (directly and completely answers).\nRespond in JSON: {"score": 0.0, "reason": "..."}'
        )
        parsed = await self._ask(prompt)
        try:
            return max(0.0, min(1.0, float(parsed["score"])))
        except (KeyError, TypeError, ValueError) as exc:
            raise JudgeError(f"judge returned no numeric 'score': {parsed!r}") from exc

    async def context_recall(self, reference: str, contexts: list[str]) -> Optional[float]:
        statements = split_into_claims(reference)
        if not statements:
            return None
        if not contexts:
            return 0.0
        prompt = (
            f"Context:\n{chr(10).join(contexts)}\n\nReference statements:\n{_render_numbered(statements)}\n\n"
            "For each statement, say whether the context contains the information needed to deduce it.\n"
            'Respond in JSON: {"statements": [{"statement": "...", "attributed": "yes|no"}]}'
        )
        return self._yes_ratio(await self._ask(prompt), "statements", "attributed", len(statements))

    async def context_precision(self, question: str, reference: str, contexts: list[str]) -> Optional[float]:
        """Average precision@k over ranked contexts judged useful for answering with the reference."""
        if not contexts:
            return None
        numbered = "\n".join(f"[{i + 1}] {c}" for i, c in enumerate(contexts))
        prompt = (
            f"Question:\n{question}\n\nReference answer:\n{reference}\n\nRetrieved contexts, in rank order:\n{numbered}\n\n"
            "For each context, say whether it is useful for deriving the reference answer.\n"
            'Respond in JSON: {"verdicts": [{"index": 1, "useful": "yes|no"}]}'
        )
        parsed = await self._ask(prompt)
        rows = parsed.get("verdicts")
        if not isinstance(rows, list) or not rows:
            raise JudgeError("judge returned no 'verdicts'")
        useful = [False] * len(contexts)
        for r in rows:
            try:
                idx = int(r["index"]) - 1
            except (KeyError, TypeError, ValueError) as exc:
                raise JudgeError(f"bad verdict row: {r!r}") from exc
            if 0 <= idx < len(contexts):
                useful[idx] = str(r.get("useful", "")).strip().lower() == "yes"
        hits, total = 0, 0.0
        for k, flag in enumerate(useful, start=1):
            if flag:
                hits += 1
                total += hits / k
        return total / hits if hits else 0.0


def validate_sample(sample: Any, position: int) -> dict[str, Any]:
    if not isinstance(sample, dict):
        raise ValueError(f"sample #{position} is not an object: {type(sample).__name__}")
    for key in ("question", "answer", "contexts", "ground_truth"):
        if key not in sample:
            raise ValueError(f"sample #{position} is missing required key '{key}'")
    if not isinstance(sample["contexts"], list) or not all(isinstance(c, str) for c in sample["contexts"]):
        raise ValueError(f"sample #{position} 'contexts' must be a list of strings")
    if not str(sample["question"]).strip():
        raise ValueError(f"sample #{position} has an empty question")
    return sample


def load_groups(raw: Any) -> list[dict[str, Any]]:
    """Normalise every exporter shape into [{scale, writer_model, judge_model, dataset}]."""
    def group(obj: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(obj.get("dataset"), list):
            raise ValueError("dataset group has no 'dataset' list")
        return {
            "scale": obj.get("scale"),
            "writer_model": obj.get("writer_model"),
            "pipeline_judge_model": obj.get("pipeline_judge_model"),
            "dataset": obj["dataset"],
        }

    if isinstance(raw, dict):
        return [group(raw)]
    if isinstance(raw, list) and raw and all(isinstance(g, dict) and "dataset" in g for g in raw):
        return [group(g) for g in raw]
    if isinstance(raw, list):
        return [{"scale": None, "writer_model": None, "pipeline_judge_model": None, "dataset": raw}]
    raise ValueError("unrecognised dataset format")


async def evaluate_sample(judge: RagasJudge, sample: dict[str, Any]) -> dict[str, Any]:
    question, answer = str(sample["question"]), str(sample["answer"])
    contexts, reference = list(sample["contexts"]), str(sample["ground_truth"])
    row: dict[str, Any] = {
        "test_id": sample.get("test_id"), "question": question, "contexts_count": len(contexts),
        "status": "evaluated", "reason": None, **{m: None for m in METRICS},
    }
    if sample.get("blocked") or sample.get("abstained"):
        row.update(status="skipped", reason="guardrail refusal" if sample.get("blocked") else "abstention")
        return row
    try:
        row["faithfulness"] = await judge.faithfulness(answer, contexts)
        row["answer_relevance"] = await judge.answer_relevance(question, answer)
        row["context_recall"] = await judge.context_recall(reference, contexts)
        row["context_precision"] = await judge.context_precision(question, reference, contexts)
    except JudgeError as exc:
        row.update(status="error", reason=str(exc), **{m: None for m in METRICS})
    return row


def _mean(values: list[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in results if r["status"] == "evaluated"]
    return {
        "sample_count": len(results),
        "evaluated": len(ok),
        "skipped": sum(1 for r in results if r["status"] == "skipped"),
        "errors": sum(1 for r in results if r["status"] == "error"),
        **{f"mean_{m}": _mean([r[m] for r in ok if r[m] is not None]) for m in METRICS},
    }


def format_table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [max([len(h)] + [len(str(r[i])) for r in rows]) for i, h in enumerate(headers)]
    head = " | ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    sep = "-+-".join("-" * w for w in widths)
    body = [" | ".join(str(r[i]).ljust(widths[i]) for i in range(len(headers))) for r in rows]
    return "\n".join([head, sep, *body])


def _fmt(v: Optional[float]) -> str:
    return "n/a" if v is None else f"{v:.2f}"


async def run_ragas_evaluation(
    raw: Any,
    judge: RagasJudge,
    settings: Settings,
    out_path: Optional[Path] = None,
) -> dict[str, Any]:
    groups = load_groups(raw)
    if not any(g["dataset"] for g in groups):
        raise ValueError("No samples found in dataset")

    reports: list[dict[str, Any]] = []
    for g in groups:
        problems = independence_problems(judge.model, {
            "writer": g["writer_model"] or settings.models.writer_model,
            "pipeline judge": g["pipeline_judge_model"] or settings.models.judge_model,
            "decision model": settings.decision.jev_model if settings.decision.backend == "jev" else None,
        })
        if problems:
            raise ValueError("Judge independence violated (NFR-6): " + "; ".join(problems))

        samples = [validate_sample(s, i) for i, s in enumerate(g["dataset"], start=1)]
        label = f"N={g['scale']}" if g["scale"] is not None else "dataset"
        print(f"\nScoring {len(samples)} samples ({label}) with judge '{judge.model}'...", flush=True)
        results = []
        for i, s in enumerate(samples, start=1):
            print(f"  [{i}/{len(samples)}] {str(s['question'])[:60]}", flush=True)
            results.append(await evaluate_sample(judge, s))

        summary = summarize(results)
        rows = [[str(i), r["question"][:38], r["status"], _fmt(r["faithfulness"]), _fmt(r["answer_relevance"]),
                 _fmt(r["context_recall"]), _fmt(r["context_precision"]), str(r["contexts_count"])]
                for i, r in enumerate(results, start=1)]
        print("\n" + format_table(["#", "Question", "Status", "Faith", "AnsRel", "CtxRec", "CtxPrec", "Chunks"], rows))
        print(f"\n  {label}: evaluated={summary['evaluated']} skipped={summary['skipped']} errors={summary['errors']}")
        for m in METRICS:
            v = summary[f"mean_{m}"]
            print(f"  mean {m:<18} {'n/a' if v is None else f'{v * 100:.1f}%'}")
        for r in results:
            if r["status"] == "error":
                print(f"  ! judge error on '{r['question'][:50]}': {r['reason']}")
        reports.append({"scale": g["scale"], "writer_model": g["writer_model"], **summary, "results": results})

    report = {"judge_model": judge.model, "reports": reports}
    if out_path:
        out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nScorecard written to {out_path}")
    return report


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="External Ragas-style scorecard")
    parser.add_argument("--dataset", type=Path, required=True, help="JSON written by run_benchmark --format ragas")
    parser.add_argument("--model", default=None, help="Judge model (default: evaluation.judge_model from config.json)")
    parser.add_argument("--api-key", default=None, help="OpenRouter key (default: OPENROUTER_API_KEY)")
    parser.add_argument("--out", type=Path, default=None, help="Path to write the JSON scorecard")
    args = parser.parse_args()

    settings = get_settings()
    api_key = args.api_key or os.getenv("OPENROUTER_API_KEY") or settings.secrets.openrouter_api_key
    if not api_key:
        print("Error: no OpenRouter API key. Use --api-key or OPENROUTER_API_KEY in .env", file=sys.stderr)
        sys.exit(1)

    client = OpenRouterClientAdapter(
        base_url=settings.llm.openrouter_base_url,
        timeout_seconds=settings.timeouts.openrouter_seconds,
        log_full_prompts=settings.logging.log_full_prompts,
        default_max_tokens=settings.models.max_tokens,
    )
    judge = RagasJudge(client, args.model or settings.evaluation.judge_model, api_key)
    raw = json.loads(args.dataset.read_text(encoding="utf-8"))
    try:
        asyncio.run(run_ragas_evaluation(raw, judge, settings, args.out))
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
