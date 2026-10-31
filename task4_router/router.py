"""Task 4: Rate-limiting & model fallback router for an LLM gateway.

- Token-aware sliding window (50k tokens/min/tenant) backed by sqlite.
- Primary model gets a 3000ms timeout; on HTTP 429 or timeout, fail over
  to the secondary provider.
- All failures surface as a standardized gateway error payload (502) with
  no upstream stack traces or internal details.

Run: python -m task4_router.router
Env: PRIMARY_URL, SECONDARY_URL, PORT (default 8000), DB_PATH
"""
from __future__ import annotations

import logging
import os


import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from task4_router.limiter import SlidingWindowLimiter

log = logging.getLogger("task4-router")

TIMEOUT_MS = 3000
RATE_LIMITED = 429
GATEWAY_ERROR = 502
TOO_MANY_REQUESTS_GATEWAY = 429


def estimate_tokens(text: str) -> int:
    """Cheap proxy for token counting (~4 chars/token)."""
    return max(1, len(text) // 4)


def gateway_error(code: int, message: str, error_type: str) -> JSONResponse:
    """Standardized gateway error: no upstream internals leak."""
    return JSONResponse(
        status_code=code,
        content={
            "error": {
                "code": code,
                "type": error_type,
                "message": message,
                "request_id": None,
            }
        },
    )


def create_app(
    primary_url: str | None = None,
    secondary_url: str | None = None,
    limiter: SlidingWindowLimiter | None = None,
    client: httpx.AsyncClient | None = None,
) -> FastAPI:
    primary_url = primary_url or os.environ.get("PRIMARY_URL", "http://127.0.0.1:9100/v1/chat/completions")
    secondary_url = secondary_url or os.environ.get("SECONDARY_URL", "http://127.0.0.1:9101/v1/chat/completions")
    app = FastAPI(title="llm-gateway-router")
    app.state.http = client or httpx.AsyncClient(timeout=None)
    app.state.limiter = limiter or SlidingWindowLimiter(os.environ.get("DB_PATH", "task4_router/limiter.db"))

    async def _call_provider(url: str, body: dict) -> httpx.Response:
        return await app.state.http.post(url, json=body, timeout=TIMEOUT_MS / 1000.0)

    @app.post("/v1/chat/completions")
    async def completions(request: Request):
        tenant = request.headers.get("x-api-key", "anonymous")
        try:
            body = await request.json()
        except Exception:
            return gateway_error(400, "Malformed request body", "invalid_request")

        prompt_text = " ".join(m.get("content", "") for m in body.get("messages", []) if isinstance(m, dict))
        tokens = estimate_tokens(prompt_text)
        if not app.state.limiter.check_and_record(tenant, tokens):
            return gateway_error(
                TOO_MANY_REQUESTS_GATEWAY,
                "Rate limit exceeded: too many tokens for this tenant in the last 60s",
                "rate_limit_exceeded",
            )

        used_primary = False
        try:
            resp = await _call_provider(primary_url, body)
            used_primary = True
            if resp.status_code == RATE_LIMITED:
                raise httpx.HTTPStatusError("429", request=resp.request, response=resp)
            if resp.status_code >= 400:
                log.warning("primary returned %s; failing over", resp.status_code)
            else:
                return JSONResponse(resp.json(), status_code=resp.status_code)
        except httpx.HTTPError:
            # 429, timeout, or any transport failure (dead primary) -> fail over.
            log.warning("primary failed (429/timeout/transport); failing over to secondary")

        try:
            resp = await _call_provider(secondary_url, body)
            if resp.status_code >= 400:
                return gateway_error(
                    GATEWAY_ERROR, "All upstream model providers failed", "upstream_error"
                )
            data = resp.json()
            data["gateway"] = {"provider": "secondary", "primary_used": used_primary}
            return JSONResponse(data, status_code=resp.status_code)
        except httpx.HTTPError:
            return gateway_error(GATEWAY_ERROR, "All upstream model providers failed", "upstream_error")

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True, "primary": primary_url, "secondary": secondary_url}

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", "8000")))
