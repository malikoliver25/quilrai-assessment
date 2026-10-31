"""Mock LLM provider: streams an SSE chat completion containing PII."""
from __future__ import annotations

import asyncio
import json

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

TEXT = (
    "Sure! Contact ada.lovelace@example.com or call about SSN 123-45-6789. "
    "Card on file: 4111 1111 1111 1111. Reference email grace+hopper@mail.org. Done."
)

app = FastAPI(title="mock-llm")


@app.post("/v1/chat/completions")
async def completions(request: Request) -> StreamingResponse:
    # Split into awkward chunks so PII spans chunk boundaries.
    chunks = ["Sure! Contact ada.lo", "velace@example", ".com or SSN 123-45-", "6789. Card: 4111 1111 ", "1111 1111. Email grace+hop", "per@mail.o", "rg. Done."]

    async def gen():
        for c in chunks:
            yield f"data: {json.dumps({'choices': [{'delta': {'content': c}}]})}\n\n".encode()
            await asyncio.sleep(0.01)
        yield b"data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/health")
async def health() -> dict:
    return {"ok": True}
