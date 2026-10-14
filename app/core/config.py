"""Strict configuration loader for GrantLoom.

Adheres strictly to clean-code architecture:
1. Operational settings load strictly from config/config.json with NO in-code defaults.
2. Secrets load strictly from .env / environment variables.
3. Relative storage paths resolve against the project root, never the process CWD.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ServerConfig(_Strict):
    host: str
    port: int
    # Public BYOK demo mode (decisions-log D27): serve non-loopback clients without a
    # MASTER_API_KEY. Every LLM-costing call still requires the caller's own X-OpenRouter-Key
    # (InferenceProfile.authorize); nginx/slowapi provide the rate limiting.
    public_byok: bool


class DatabaseConfig(_Strict):
    sqlite_path: str
    chroma_path: str
    kuzu_path: str
    uploads_path: str
    synthetic_path: str

    @field_validator("sqlite_path", "chroma_path", "kuzu_path", "uploads_path", "synthetic_path")
    @classmethod
    def _resolve_against_project_root(cls, value: str) -> str:
        path = Path(value)
        return str(path if path.is_absolute() else PROJECT_ROOT / path)


class LimitsConfig(_Strict):
    max_documents: int
    max_file_size_mb: int
    max_pages_per_doc: int


class RateLimitsConfig(_Strict):
    ask: str
    upload: str
    compliance: str
    admin: str


class ModelsConfig(_Strict):
    writer_model: str
    judge_model: str
    embedding_backend: Literal["deterministic", "openrouter"]
    embedding_model: str
    embedding_dimension: int


class RetrievalConfig(_Strict):
    dense_top_k: int
    bm25_top_k: int
    rrf_k: int
    min_relevance_threshold: float  # dense cosine floor for the abstention check (FR-Q8)
    min_bm25_overlap: float  # lexical-overlap floor for the abstention check; calibrated separately
    # from the dense cosine score because the two signals live on different scales (review-04 C2)
    rerank_top_k: int
    top_k_after_filter: int
    max_structured_tables: int
    max_tool_evidence_chunks: int


class ChunkingConfig(_Strict):
    target_chunk_size: int
    chunk_overlap: int
    rows_per_chunk: int


class IngestionConfig(_Strict):
    worker_poll_interval_seconds: float
    embed_batch_size: int
    max_job_attempts: int
    stale_job_timeout_seconds: int
    maintenance_interval_seconds: int


class GuardrailsConfig(_Strict):
    max_rewrite_attempts: int
    quarantine_retention_days: int
    taint_threshold_chunks: int
    tier2_sample_rate: float
    max_query_chars: int
    citation_excerpt_chars: int  # evidence excerpt length returned per citation (review-04 C6)


class LLMJudgeConfig(_Strict):
    max_chunk_chars: int
    max_tier2_chunks_per_call: int


DEFERRED_DECISION_BACKENDS = {"julia1"}


class DecisionConfig(_Strict):
    backend: str
    jev_model: str
    jev_base_url: str
    router_min_confidence: float
    domain_min_confidence: float
    injection_probability_threshold: float
    min_chunk_usefulness: float
    max_retries: int = 3
    retry_delay_seconds: float = 1.0
    llm_judge: LLMJudgeConfig
    # Corpus-evidence override for false out-of-domain refusals (G1). Explicit config, identical
    # in dev and Docker (review-04 A2): no implicit gating on other blocks.
    corpus_evidence_guard: bool
    corpus_in_domain_overlap: float

    @field_validator("backend")
    @classmethod
    def _known_backend(cls, value: str) -> str:
        if value in DEFERRED_DECISION_BACKENDS:
            raise ValueError(
                f"Decision backend '{value}' is deferred and not implemented (see decisions-log.md D22). "
                "Use 'llm_judge' or 'jev'."
            )
        if value == "heuristic":
            raise ValueError(
                "The heuristic decision backend was removed from production (decisions-log D31): "
                "keyword rules cannot judge meaning or catch paraphrased injections. Use 'llm_judge' or 'jev'."
            )
        if value not in ("llm_judge", "jev"):
            raise ValueError(f"Unknown decision backend '{value}'. Use 'llm_judge' or 'jev'.")
        return value


class SqlGuardConfig(_Strict):
    max_rows: int
    timeout_seconds: float


class ComplianceConfig(_Strict):
    max_requirements: int
    evidence_top_k: int
    evidence_candidates: int
    max_concurrent_assessments: int


class TimeoutsConfig(_Strict):
    openrouter_seconds: float
    ollama_seconds: float
    probe_seconds: float
    embedding_seconds: float


class LLMConfig(_Strict):
    private_mode: bool
    openrouter_base_url: str
    ollama_base_url: str
    ollama_writer_model: str
    ollama_judge_model: str
    byok_validation_ttl_seconds: int  # 0 = validate once per process lifetime (D28)
    byok_validated_keys_max: int


class LoggingConfig(_Strict):
    log_full_prompts: bool


class SecretsConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
    master_api_key: Optional[str] = None
    openrouter_api_key: Optional[str] = None


class Settings(_Strict):
    server: ServerConfig
    database: DatabaseConfig
    limits: LimitsConfig
    rate_limits: RateLimitsConfig
    models: ModelsConfig
    retrieval: RetrievalConfig
    chunking: ChunkingConfig
    ingestion: IngestionConfig
    guardrails: GuardrailsConfig
    decision: DecisionConfig
    sql_guard: SqlGuardConfig
    compliance: ComplianceConfig
    timeouts: TimeoutsConfig
    llm: LLMConfig
    logging: LoggingConfig
    secrets: SecretsConfig


def _find_default_config_path() -> Path:
    env_override = os.getenv("GRANTLOOM_CONFIG_PATH")
    if env_override:
        return Path(env_override)
    return PROJECT_ROOT / "config" / "config.json"


def load_settings(
    config_path: Optional[str | Path] = None,
    env_file: Optional[str | Path] = None,
) -> Settings:
    """Load settings strictly from config.json and .env without in-code defaults.

    Raises FileNotFoundError if config.json does not exist.
    Raises pydantic.ValidationError if any section or key is missing or unknown.
    """
    path = Path(config_path) if config_path else _find_default_config_path()
    if not path.exists():
        raise FileNotFoundError(
            f"Required configuration file not found at: {path}. "
            "GrantLoom requires an explicit config/config.json with zero in-code defaults."
        )

    with open(path, "r", encoding="utf-8") as f:
        config_data = json.load(f)

    secrets_kwargs = {"_env_file": str(env_file if env_file is not None else PROJECT_ROOT / ".env")}
    secrets = SecretsConfig(**secrets_kwargs)

    return Settings.model_validate({**config_data, "secrets": secrets})


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Retrieve singleton application settings instance."""
    return load_settings()


def clear_settings_cache() -> None:
    """Clear cached settings instance (primarily for tests)."""
    get_settings.cache_clear()


def model_family(model_id: str) -> str:
    """Coarse model family used for the NFR-6 evaluator-independence check.

    OpenRouter ids are '<vendor>/<model>' (family = vendor); Ollama ids are '<name><version>:<tag>'
    (family = name with version digits stripped, e.g. 'qwen2.5:3b' -> 'qwen').
    """
    lowered = model_id.lower().strip()
    if "/" in lowered:
        return lowered.split("/", 1)[0]
    base = lowered.split(":", 1)[0]
    stripped = base.rstrip("0123456789.-_")
    return stripped or base
