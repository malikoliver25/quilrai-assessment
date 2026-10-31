"""Task 4 tests: sqlite sliding-window limiter + router failover behavior."""
from __future__ import annotations

import json
import httpx
import pytest
from fastapi import FastAPI, Request
from starlette.responses import JSONResponse
from task4_router.limiter import SlidingWindowLimiter
from task4_router.router import create_app

# ---------- limiter unit tests ----------

@pytest.fixture
def limiter(tmp_path):
    l = SlidingWindowLimiter(str(tmp_path / "test.db"), max_tokens=1000)
    yield l
    l.close()


def test_limiter_allows_then_blocks(limiter) -> None:
    assert limiter.check_and_record("t1", 600, now=1000.0) is True
    assert limiter.check_and_record("t1", 400, now=1000.5) is True
    assert limiter.check_and_record("t1", 1, now=1000.6) is False
    # Rejected requests must not consume quota.
    assert limiter.used_tokens("t1", now=1000.7) == 1000


def test_limiter_window_eviction(limiter) -> None:
    assert limiter.check_and_record("t1", 1000, now=1000.0) is True
    assert limiter.check_and_record("t1", 1, now=1000.0) is False
    # 61s later the old row is evicted and quota is free again.
    assert limiter.check_and_record("t1", 1000, now=1061.0) is True


def test_limiter_tenants_isolated(limiter) -> None:
    assert limiter.check_and_record("a", 1000, now=1.0) is True
    assert limiter.check_and_record("b", 1000, now=1.0) is True
    assert limiter.check_and_record("a", 1, now=1.1) is False
    assert limiter.check_and_record("b", 1, now=1.1) is False


# ---------- router e2e ----------

def _provider_app(status: int | None = None, delay: float = 0.0, fail: bool = False, tag: str = "mock", timeout_sim: bool = False):
    """Build a mock provider app. fail=True simulates a connection-level failure."""
    app = FastAPI()

    @app.post("/v1/chat/completions")
    async def completions(request: Request):
        import anyio

        if delay:
            await anyio.sleep(delay)
        if fail:
            raise RuntimeError("boom")  # connection-level failure
        if timeout_sim:
            raise httpx.ReadTimeout("simulated upstream timeout", request=request)
        if status == 429:
            return JSONResponse({"error": "rate limited upstream"}, status_code=429)
        if status and status >= 400:
            return JSONResponse({"error": f"upstream {status}"}, status_code=status)
        return JSONResponse({"choices": [{"message": {"content": "ok"}}], "provider": tag})
    return app


async def _make_client(primary_status=None, primary_delay=0.0, primary_fail=False, primary_timeout=False, secondary_status=200, tmp_path=None):
    async def transport_for(app):
        return httpx.ASGITransport(app=app)

    # Single client multiplexing by URL host.
    primary = _provider_app(primary_status, primary_delay, primary_fail, tag="primary", timeout_sim=primary_timeout)
    secondary = _provider_app(secondary_status, tag="secondary")
    mounts = {
        "http://primary": httpx.ASGITransport(app=primary),
        "http://secondary": httpx.ASGITransport(app=secondary),
    }

    class MultiTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            for host, t in mounts.items():
                if request.url.host == host.split("//")[1]:
                    return await t.handle_async_request(request)
            raise AssertionError(f"unexpected host {request.url}")

    upstream = httpx.AsyncClient(transport=MultiTransport())
    limiter = SlidingWindowLimiter(str(tmp_path / "rt.db"))
    gateway = create_app(
        primary_url="http://primary/v1/chat/completions",
        secondary_url="http://secondary/v1/chat/completions",
        limiter=limiter,
        client=upstream,
    )
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway), base_url="http://gw")
    return client


async def test_primary_success_no_failover(tmp_path) -> None:
    client = await _make_client(tmp_path=tmp_path)
    resp = await client.post(
        "/v1/chat/completions",
        headers={"x-api-key": "tenant-a"},
        json={"messages": [{"role": "user", "content": "hello"}]},
    )
    assert resp.status_code == 200
    assert "gateway" not in resp.json()


async def test_failover_on_429(tmp_path) -> None:
    client = await _make_client(primary_status=429, tmp_path=tmp_path)
    resp = await client.post(
        "/v1/chat/completions",
        headers={"x-api-key": "tenant-a"},
        json={"messages": [{"role": "user", "content": "hello"}]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["gateway"]["provider"] == "secondary"


async def test_failover_on_timeout(tmp_path) -> None:
    # Primary times out (ReadTimeout) -> secondary answers.
    client = await _make_client(primary_timeout=True, tmp_path=tmp_path)
    resp = await client.post(
        "/v1/chat/completions",
        headers={"x-api-key": "tenant-a"},
        json={"messages": [{"role": "user", "content": "hello"}]},
    )
    assert resp.status_code == 200
    assert resp.json()["gateway"]["provider"] == "secondary"


async def test_standardized_error_when_both_fail(tmp_path) -> None:
    client = await _make_client(primary_status=429, secondary_status=500, tmp_path=tmp_path)
    resp = await client.post(
        "/v1/chat/completions",
        headers={"x-api-key": "tenant-a"},
        json={"messages": [{"role": "user", "content": "hello"}]},
    )
    assert resp.status_code == 502
    err = resp.json()["error"]
    assert err["type"] == "upstream_error"
    assert "boom" not in json.dumps(err) and "Traceback" not in json.dumps(err)


async def test_rate_limit_returns_standardized_429(tmp_path) -> None:
    client = await _make_client(tmp_path=tmp_path)
    big = "x" * (50_000 * 4)  # ~50k tokens
    headers = {"x-api-key": "tenant-big"}
    r1 = await client.post("/v1/chat/completions", headers=headers, json={"messages": [{"role": "user", "content": big}]})
    assert r1.status_code == 200
    r2 = await client.post("/v1/chat/completions", headers=headers, json={"messages": [{"role": "user", "content": "tiny"}]})
    assert r2.status_code == 429
    err = r2.json()["error"]
    assert err["type"] == "rate_limit_exceeded"
    assert "upstream" not in err["message"].lower()
