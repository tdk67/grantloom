"""Unit tests for configuration loader and strict validation."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import (
    clear_settings_cache,
    get_settings,
    load_settings,
)


def test_valid_default_config_loading():
    """Verify default config/config.json loads cleanly with all expected properties."""
    clear_settings_cache()
    settings = get_settings()

    assert settings.server.host == "127.0.0.1"
    assert settings.server.port == 8000
    assert settings.limits.max_documents == 50
    assert settings.limits.max_file_size_mb == 25
    assert settings.models.writer_model == "anthropic/claude-3.5-sonnet"
    assert settings.models.judge_model == "openai/gpt-4o-mini"
    assert settings.retrieval.rrf_k == 60
    assert settings.guardrails.max_rewrite_attempts == 2
    assert settings.llm.private_mode is False
    assert settings.llm.ollama_base_url == "http://127.0.0.1:11434"
    assert settings.llm.ollama_writer_model == "qwen2.5:3b"
    assert settings.llm.ollama_judge_model == "qwen2.5:3b"


def test_missing_key_rejection(tmp_path: Path):
    """Verify that omitting a required config key raises ValidationError (no in-code defaults)."""
    incomplete_data = {
        "server": {"host": "127.0.0.1", "port": 8080},
        "database": {
            "sqlite_path": "test.db",
            "chroma_path": "chroma",
            "kuzu_path": "kuzu",
        },
        # limits is missing entirely!
        "models": {
            "writer_model": "writer",
            "judge_model": "judge",
            "embedding_model": "embed",
        },
        "retrieval": {
            "dense_top_k": 5,
            "bm25_top_k": 5,
            "rrf_k": 50,
            "min_relevance_threshold": 0.3,
            "rerank_top_k": 3,
        },
        "guardrails": {
            "max_rewrite_attempts": 2,
            "quarantine_retention_days": 10,
            "taint_threshold_chunks": 2,
        },
        "llm": {
            "private_mode": False,
            "ollama_base_url": "http://127.0.0.1:11434",
            "ollama_writer_model": "test-writer-model",
            "ollama_judge_model": "test-judge-model",
        },
        "logging": {"log_full_prompts": False},
    }

    config_file = tmp_path / "incomplete_config.json"
    config_file.write_text(json.dumps(incomplete_data), encoding="utf-8")

    with pytest.raises(KeyError):
        load_settings(config_path=config_file)


def test_missing_subfield_rejection(tmp_path: Path):
    """Verify that omitting a subfield within limits raises ValidationError."""
    data = {
        "server": {"host": "127.0.0.1", "port": 8080},
        "database": {
            "sqlite_path": "test.db",
            "chroma_path": "chroma",
            "kuzu_path": "kuzu",
        },
        "limits": {
            # max_documents is omitted!
            "max_file_size_mb": 25,
            "max_pages_per_doc": 100,
        },
        "models": {
            "writer_model": "writer",
            "judge_model": "judge",
            "embedding_model": "embed",
        },
        "retrieval": {
            "dense_top_k": 5,
            "bm25_top_k": 5,
            "rrf_k": 50,
            "min_relevance_threshold": 0.3,
            "rerank_top_k": 3,
        },
        "guardrails": {
            "max_rewrite_attempts": 2,
            "quarantine_retention_days": 10,
            "taint_threshold_chunks": 2,
        },
        "llm": {
            "private_mode": False,
            "ollama_base_url": "http://127.0.0.1:11434",
            "ollama_writer_model": "test-writer-model",
            "ollama_judge_model": "test-judge-model",
        },
        "logging": {"log_full_prompts": False},
    }

    config_file = tmp_path / "missing_subfield.json"
    config_file.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ValidationError) as exc_info:
        load_settings(config_path=config_file)
    assert "max_documents" in str(exc_info.value)


def test_missing_file_raises_not_found(tmp_path: Path):
    """Verify non-existent configuration path raises FileNotFoundError."""
    non_existent = tmp_path / "does_not_exist.json"
    with pytest.raises(FileNotFoundError):
        load_settings(config_path=non_existent)


def test_secrets_loading(tmp_path: Path):
    """Verify secrets load from custom env file without polluting config.json."""
    default_config = Path("config/config.json")
    env_file = tmp_path / ".test_env"
    env_file.write_text("MASTER_API_KEY=test-master-secret-123\n", encoding="utf-8")

    settings = load_settings(config_path=default_config, env_file=env_file)
    assert settings.secrets.master_api_key == "test-master-secret-123"
