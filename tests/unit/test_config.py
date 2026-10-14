"""Unit tests for configuration loader and strict validation."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import PROJECT_ROOT, clear_settings_cache, get_settings, load_settings, model_family

CONFIG_PATH = PROJECT_ROOT / "config" / "config.json"


def _config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _write(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_valid_default_config_loading():
    clear_settings_cache()
    settings = get_settings()
    assert settings.server.host == "127.0.0.1"
    assert settings.limits.max_documents == 50
    assert settings.decision.backend == "llm_judge"
    assert settings.guardrails.max_rewrite_attempts == 2
    assert settings.llm.private_mode is False
    # Relative storage paths resolve against the project root, not the CWD
    assert Path(settings.database.sqlite_path).is_absolute()
    assert Path(settings.database.sqlite_path).is_relative_to(PROJECT_ROOT)


def test_missing_section_rejection(tmp_path: Path):
    data = _config()
    del data["limits"]
    with pytest.raises(ValidationError) as exc_info:
        load_settings(config_path=_write(tmp_path, data))
    assert "limits" in str(exc_info.value)


def test_missing_subfield_rejection(tmp_path: Path):
    data = _config()
    del data["limits"]["max_documents"]
    with pytest.raises(ValidationError) as exc_info:
        load_settings(config_path=_write(tmp_path, data))
    assert "max_documents" in str(exc_info.value)


def test_unknown_key_rejection(tmp_path: Path):
    data = _config()
    data["retrieval"]["surprise"] = 1
    with pytest.raises(ValidationError):
        load_settings(config_path=_write(tmp_path, data))


@pytest.mark.parametrize("backend", ["julia1"])
def test_deferred_decision_backends_are_rejected_with_pointer(tmp_path: Path, backend: str):
    data = _config()
    data["decision"]["backend"] = backend
    with pytest.raises(ValidationError) as exc_info:
        load_settings(config_path=_write(tmp_path, data))
    assert "D22" in str(exc_info.value)


def test_jev_decision_backend_valid_loading(tmp_path: Path):
    data = _config()
    data["decision"]["backend"] = "jev"
    settings = load_settings(config_path=_write(tmp_path, data))
    assert settings.decision.backend == "jev"



def test_missing_file_raises_not_found(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_settings(config_path=tmp_path / "does_not_exist.json")


def test_secrets_loading(tmp_path: Path):
    env_file = tmp_path / ".test_env"
    env_file.write_text("MASTER_API_KEY=test-master-secret-123\n", encoding="utf-8")
    settings = load_settings(config_path=CONFIG_PATH, env_file=env_file)
    assert settings.secrets.master_api_key == "test-master-secret-123"


@pytest.mark.parametrize(
    "model, family",
    [("qwen2.5:3b", "qwen"), ("phi3:latest", "phi"), ("gemma3:4b", "gemma"), ("openai/gpt-4o-mini", "openai")],
)
def test_model_family(model: str, family: str):
    assert model_family(model) == family


def test_default_config_satisfies_evaluator_independence():
    from app.services.inference import evaluator_independence_problems

    s = get_settings()
    assert evaluator_independence_problems(s.models.writer_model, s.models.judge_model) == []
    assert evaluator_independence_problems(s.llm.ollama_writer_model, s.llm.ollama_judge_model) == []
