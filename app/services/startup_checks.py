"""Checks run once at application startup."""

from __future__ import annotations

import logging

from app.core.config import Settings
from app.core.errors import ErrorCode, GrantLoomError
from app.services.inference import evaluator_independence_problems

logger = logging.getLogger("grantloom.startup")

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


class StartupCheckError(GrantLoomError):
    default_status = 500
    default_title = "Unsafe Configuration"
    default_code = ErrorCode.CONFIG_ERROR


def run_startup_checks(settings: Settings) -> list[str]:
    """Raise on unsafe configurations; return warnings (also logged)."""
    public_byok_demo = settings.server.public_byok and not settings.llm.private_mode
    if settings.server.host not in LOOPBACK_HOSTS and not settings.secrets.master_api_key and not public_byok_demo:
        raise StartupCheckError(
            f"server.host is '{settings.server.host}' but no MASTER_API_KEY is set. Refusing to expose an "
            "unauthenticated API on a non-loopback interface (public BYOK mode requires "
            "server.public_byok=true and llm.private_mode=false)."
        )

    warnings: list[str] = []
    if public_byok_demo and not settings.secrets.master_api_key:
        message = (
            "D27 public BYOK mode: serving remote clients without a MASTER_API_KEY. LLM calls are gated "
            "per request by the caller's X-OpenRouter-Key; document/admin endpoints are open and rely "
            "on reverse-proxy rate limiting."
        )
        warnings.append(message)
        logger.warning(message)
    pairs = [("OpenRouter", settings.models.writer_model, settings.models.judge_model)]
    pairs.append(("Ollama", settings.llm.ollama_writer_model, settings.llm.ollama_judge_model))
    active = "Ollama" if settings.llm.private_mode else "OpenRouter"
    for provider, writer, judge in pairs:
        for problem in evaluator_independence_problems(writer, judge):
            message = f"NFR-6 evaluator independence ({provider}{', active' if provider == active else ''}): {problem}"
            warnings.append(message)
            logger.warning(message)
    if settings.decision.backend == "heuristic":
        message = "Decision backend is 'heuristic': G1/G2/G4 use lexical rules only and Tier-2 ingestion scanning is off."
        warnings.append(message)
        logger.warning(message)
    return warnings
