"""Strict configuration loader for GrantLoom.

Adheres strictly to clean-code architecture:
1. Operational settings load strictly from config/config.json with NO in-code defaults.
2. Secrets load strictly from .env / environment variables.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict
from pydantic_settings import BaseSettings, SettingsConfigDict


class ServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host: str
    port: int


class DatabaseConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sqlite_path: str
    chroma_path: str
    kuzu_path: str


class LimitsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_documents: int
    max_file_size_mb: int
    max_pages_per_doc: int


class ModelsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    writer_model: str
    judge_model: str
    embedding_model: str


class RetrievalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dense_top_k: int
    bm25_top_k: int
    rrf_k: int
    min_relevance_threshold: float
    rerank_top_k: int


class GuardrailsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_rewrite_attempts: int
    quarantine_retention_days: int
    taint_threshold_chunks: int


class LLMConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    private_mode: bool
    ollama_base_url: str
    ollama_writer_model: str
    ollama_judge_model: str


class LoggingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    log_full_prompts: bool


class SecretsConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
    master_api_key: Optional[str] = None
    openrouter_api_key: Optional[str] = None


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    server: ServerConfig
    database: DatabaseConfig
    limits: LimitsConfig
    models: ModelsConfig
    retrieval: RetrievalConfig
    guardrails: GuardrailsConfig
    llm: LLMConfig
    logging: LoggingConfig
    secrets: SecretsConfig


def _find_default_config_path() -> Path:
    env_override = os.getenv("GRANTLOOM_CONFIG_PATH")
    if env_override:
        return Path(env_override)

    # Search from current working directory or package root
    cwd_path = Path.cwd() / "config" / "config.json"
    if cwd_path.exists():
        return cwd_path

    # Up one directory from app/core
    root_path = Path(__file__).resolve().parent.parent.parent / "config" / "config.json"
    return root_path


def load_settings(
    config_path: Optional[str | Path] = None,
    env_file: Optional[str | Path] = None,
) -> Settings:
    """Load settings strictly from config.json and .env without in-code defaults.
    
    Raises FileNotFoundError if config.json does not exist.
    Raises ValidationError if any required configuration key is missing.
    """
    path = Path(config_path) if config_path else _find_default_config_path()
    if not path.exists():
        raise FileNotFoundError(
            f"Required configuration file not found at: {path}. "
            "GrantLoom requires an explicit config/config.json with zero in-code defaults."
        )

    with open(path, "r", encoding="utf-8") as f:
        config_data = json.load(f)

    # Load secrets strictly from environment/.env
    secrets_kwargs = {}
    if env_file is not None:
        secrets_kwargs["_env_file"] = str(env_file)
    secrets = SecretsConfig(**secrets_kwargs)

    return Settings(
        server=ServerConfig(**config_data["server"]),
        database=DatabaseConfig(**config_data["database"]),
        limits=LimitsConfig(**config_data["limits"]),
        models=ModelsConfig(**config_data["models"]),
        retrieval=RetrievalConfig(**config_data["retrieval"]),
        guardrails=GuardrailsConfig(**config_data["guardrails"]),
        llm=LLMConfig(**config_data["llm"]),
        logging=LoggingConfig(**config_data["logging"]),
        secrets=secrets,
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Retrieve singleton application settings instance."""
    return load_settings()


def clear_settings_cache() -> None:
    """Clear cached settings instance (primarily for tests)."""
    get_settings.cache_clear()
