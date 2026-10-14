"""Integration tests for Milestone M6: Thin Frontend Implementation & Crawler Endpoints.

Verifies:
1. curl -f http://localhost:8000/robots.txt and /llms.txt and /sitemap.xml return valid content.
2. Root / serves index.html with accessible markup and skip link.
3. Static assets /styles.css and /app.js are served cleanly.
"""

from __future__ import annotations

import httpx
import pytest

from app.api.deps import reset_dependencies
from app.main import create_app


@pytest.mark.asyncio
async def test_crawler_endpoints_and_static_frontend():
    reset_dependencies()
    app = create_app()

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # 1. /robots.txt
        res_robots = await client.get("/robots.txt")
        assert res_robots.status_code == 200
        assert "User-agent" in res_robots.text
        assert "Disallow: /api/" in res_robots.text

        # 2. /sitemap.xml
        res_sitemap = await client.get("/sitemap.xml")
        assert res_sitemap.status_code == 200
        assert "<urlset" in res_sitemap.text

        # 3. /llms.txt
        res_llms = await client.get("/llms.txt")
        assert res_llms.status_code == 200
        assert "GrantLoom" in res_llms.text
        assert "Core Capabilities" in res_llms.text

        # 4. Root / (index.html)
        res_root = await client.get("/")
        assert res_root.status_code == 200
        assert "text/html" in res_root.headers.get("content-type", "")
        assert "GrantLoom" in res_root.text
        assert "Skip to main content" in res_root.text

        # 5. /styles.css
        res_css = await client.get("/styles.css")
        assert res_css.status_code == 200
        assert "--bg-primary" in res_css.text

        # 6. /app.js
        res_js = await client.get("/app.js")
        assert res_js.status_code == 200
        assert "grantloom_db" in res_js.text


def test_app_js_has_no_duplicate_function_declarations():
    """review-04 B4 regression: `loadStarterPrompts` was declared twice in app.js. JS hoisting made
    the last declaration silently win, so the earlier one (and `window.useStarterPrompt`) was dead
    code no test caught. Duplicate top-level function declarations must fail the suite."""
    from collections import Counter
    from pathlib import Path
    import re

    source = Path(__file__).resolve().parents[2] / "app" / "static" / "app.js"
    names = re.findall(r"^(?:async\s+)?function\s+([A-Za-z0-9_$]+)", source.read_text(encoding="utf-8"), re.M)
    duplicates = {n: c for n, c in Counter(names).items() if c > 1}
    assert not duplicates, f"duplicate function declarations in app.js: {duplicates}"
    assert names.count("loadStarterPrompts") == 1
    assert "useStarterPrompt" not in source.read_text(encoding="utf-8")
