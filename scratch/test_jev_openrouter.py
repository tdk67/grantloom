"""Short test script to verify Jev Decision Backend over OpenRouter.

Usage:
    uv run python scratch/test_jev_openrouter.py [OPTIONAL_OPENROUTER_API_KEY]

If an API key is provided (or set in OPENROUTER_API_KEY), it executes live calls to OpenRouter
testing G1 query screening and G2 strategy routing using the configured Jev model.
Otherwise, it runs simulated tests verifying prompt rendering, JSON schema validation,
and the 3-attempt retry loop on failure.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

from app.adapters.decision_backends import JevDecisionBackend
from app.adapters.openrouter_client import OpenRouterClientAdapter
from app.core.config import get_settings
from app.core.errors import DecisionBackendError, UpstreamServiceError
from app.ports.llm_client import LLMClientPort
from tests.helpers import ScriptedLLM


async def test_jev_simulated():
    print("\n[1] Running Simulated JevDecisionBackend Verification (Mock LLM Client Port)...")
    settings = get_settings()

    # 1. Test G1 screening with valid simulated JSON
    g1_response = '{"manipulation": false, "on_topic": true, "confidence": 0.95, "reason": "Query is asking about grant requirements."}'
    mock_client = ScriptedLLM(json_handler=lambda p: g1_response)
    backend = JevDecisionBackend(mock_client, settings.decision.jev_model, settings.decision)

    verdict_g1 = await backend.screen_query("What is the cost share requirement for solar grant?")
    print(f"  [OK] G1 Screening: passed={verdict_g1.passed}, confidence={verdict_g1.confidence}, backend={verdict_g1.metadata['backend']}")
    assert verdict_g1.passed is True
    assert verdict_g1.confidence == 0.95

    # 2. Test G2 routing with valid simulated JSON
    g2_response = '{"strategy": "composite", "confidence": 0.92, "reason": "Query requires cross-source synthesis."}'
    mock_client.json_handler = lambda p: g2_response
    verdict_g2 = await backend.route_query("Compare the proposal budget against solicitation personnel caps", ["semantic", "structured", "graph", "composite"])
    print(f"  [OK] G2 Strategy Routing: strategy={verdict_g2.metadata['strategy']}, confidence={verdict_g2.confidence}")
    assert verdict_g2.metadata["strategy"] == "composite"

    # 3. Test 3-attempt retry loop on failure
    attempt_count = 0
    def failing_handler(prompt):
        nonlocal attempt_count
        attempt_count += 1
        raise UpstreamServiceError("Connection timeout to OpenRouter")

    mock_client.json_handler = failing_handler
    try:
        await backend.screen_query("Test query")
        print("  [FAIL] Should have raised DecisionBackendError")
    except DecisionBackendError as exc:
        print(f"  [OK] 3-Attempt Retry on Timeout: correctly retried {attempt_count} times and raised DecisionBackendError: {exc}")
        assert attempt_count == 3


async def test_jev_live(api_key: str):
    print(f"\n[2] Running Live Jev Decision Call over OpenRouter...")
    settings = get_settings()
    model = settings.decision.jev_model
    base_url = settings.llm.openrouter_base_url
    print(f"  OpenRouter Base URL: {base_url}")
    print(f"  Jev Model Designated in config.json: {model}")
    print(f"  API Key: {api_key[:8]}...{api_key[-4:]}")

    client = OpenRouterClientAdapter(
        base_url=base_url,
        timeout_seconds=settings.timeouts.openrouter_seconds,
        log_full_prompts=False,
    )

    backend = JevDecisionBackend(
        llm_client=client,
        model=model,
        config=settings.decision,
    )

    test_query = "What are the cost share and matching fund requirements for funding solicitation DE-FOA-0003291?"
    print(f"\n  Query: '{test_query}'")
    print(f"  Executing G1 Query Screening via Jev...")
    t0 = time.perf_counter()
    try:
        g1_result = await backend.screen_query(test_query, api_key=api_key)
        duration_ms = (time.perf_counter() - t0) * 1000
        print(f"  [OK] G1 Response received in {duration_ms:.1f}ms:")
        print(f"    - Passed: {g1_result.passed}")
        print(f"    - In Domain: {g1_result.metadata.get('in_domain')}")
        print(f"    - Manipulation Detected: {g1_result.metadata.get('manipulation')}")
        print(f"    - Confidence: {g1_result.confidence}")
        print(f"    - Reason: {g1_result.reason}")
        print(f"    - Backend: {g1_result.metadata.get('backend')}")
    except Exception as exc:
        print(f"  [FAIL] Live G1 Screening failed: {type(exc).__name__}: {exc}")
        return

    print(f"\n  Executing G2 Strategy Routing via Jev...")
    t0 = time.perf_counter()
    try:
        strategies = ["hybrid", "semantic", "structured", "graph", "composite"]
        g2_result = await backend.route_query(test_query, strategies, api_key=api_key)
        duration_ms = (time.perf_counter() - t0) * 1000
        print(f"  [OK] G2 Response received in {duration_ms:.1f}ms:")
        print(f"    - Strategy Selected: {g2_result.metadata.get('strategy')}")
        print(f"    - Confidence: {g2_result.confidence}")
        print(f"    - Reason: {g2_result.reason}")
        print(f"    - Backend: {g2_result.metadata.get('backend')}")
    except Exception as exc:
        print(f"  [FAIL] Live G2 Routing failed: {type(exc).__name__}: {exc}")
        return

    print("\n[OK] Live OpenRouter Jev Verification Completed Successfully!")


async def main():
    print("=" * 70)
    print("GrantLoom: Jev Decision Backend over OpenRouter Test")
    print("=" * 70)

    # Always run simulated test first
    await test_jev_simulated()

    # Check for API key for live testing
    api_key = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("OPENROUTER_API_KEY")
    if api_key:
        await test_jev_live(api_key.strip())
    else:
        print("\n[Notice] No OPENROUTER_API_KEY provided.")
        print("  To run live against OpenRouter, invoke with:")
        print("    uv run python scratch/test_jev_openrouter.py sk-or-v1-YOUR_KEY")


if __name__ == "__main__":
    asyncio.run(main())
