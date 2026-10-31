"""Task 3: LLM Gateway streaming guardrail (PII redaction).

POST /v1/chat/completions streams an SSE response from the upstream LLM,
redacting PII (email / SSN / credit card) from each delta in real time.

Run: python -m task3_llm_guardrail.gateway
Env: UPSTREAM_URL (default http://127.0.0.1:9002), PORT (default 8000)
"""
from __future__ import annotations

import json
import os

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from task3_llm_guardrail.redactor import StreamRedactor


def _sse(event: dict) -> bytes:
    return f"data: {json.dumps(event)}\n\n".encode()


def create_app(upstream_url: str | None = None, client: httpx.AsyncClient | None = None) -> FastAPI:
    upstream_url = upstream_url or os.environ.get("UPSTREAM_URL", "http://127.0.0.1:9002/v1/chat/completions")
    app = FastAPI(title="llm-gateway-guardrail")
    app.state.http = client or httpx.AsyncClient(timeout=None)

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        body = await request.json()

        async def event_stream():
            redactor = StreamRedactor()
            try:
                async with app.state.http.stream("POST", upstream_url, json=body) as resp:
                    if resp.status_code != 200:
                        await resp.aread()
                        yield _sse({"error": {"code": resp.status_code, "message": "upstream error"}})
                        yield b"data: [DONE]\n\n"
                        return
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        if payload == "[DONE]":
                            tail = redactor.flush()
                            if tail:
                                yield _sse({"choices": [{"delta": {"content": tail}}]})
                            yield b"data: [DONE]\n\n"
                            return
                        try:
                            event = json.loads(payload)
                        except json.JSONDecodeError:
                            continue
                        delta = (((event.get("choices") or [{}])[0]).get("delta") or {}).get("content")
                        if delta:
                            out = redactor.feed(delta)
                            if out:
                                event["choices"][0]["delta"]["content"] = out
                                yield _sse(event)
                        else:
                            yield _sse(event)
            except httpx.HTTPError:
                yield _sse({"error": {"code": 502, "message": "upstream unavailable"}})
                yield b"data: [DONE]\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True, "upstream": upstream_url}

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", "8000")))
