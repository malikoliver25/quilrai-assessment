"""Task 3 tests: redactor unit tests + end-to-end SSE guardrail via ASGI."""
from __future__ import annotations

import json
import time

import httpx
import pytest

from task3_llm_guardrail.gateway import create_app
from task3_llm_guardrail.mock_llm import app as llm_app
from task3_llm_guardrail.redactor import REDACTED, StreamRedactor, redact_stream


# ---------- unit: redactor ----------

def test_redacts_email_ssn_card() -> None:
    out = "".join(redact_stream(["mail me at a.b@cde.com, ssn 123-45-6789, card 4111111111111111 ok"]))
    assert "a.b@cde.com" not in out and "123-45-6789" not in out and "4111111111111111" not in out
    assert out.count(REDACTED) == 3


def test_pattern_split_across_chunks() -> None:
    chunks = ["contact ada.lo", "velace@example", ".com now, ssn 123-45-", "6789 end"]
    out = "".join(redact_stream(chunks))
    assert "ada.lovelace@example.com" not in out
    assert "123-45-6789" not in out
    assert out.count(REDACTED) == 2


def test_card_split_across_chunks() -> None:
    chunks = ["card 4111 11", "11 1111 ", "1111 end"]
    out = "".join(redact_stream(chunks))
    assert "4111 1111 1111 1111" not in out
    assert out == "card [REDACTED] end"


def test_holds_back_only_boundary_tail() -> None:
    """Emitted output per feed must never buffer the whole stream."""
    r = StreamRedactor()
    total_in = 0
    total_out = 0
    for i in range(1000):
        chunk = "x" * 50
        total_in += len(chunk)
        total_out += len(r.feed(chunk))
    total_out += len(r.flush())
    # Output should track input minus a small constant tail, not zero until flush.
    assert total_out > total_in - 200
    assert total_out <= total_in


def test_clean_text_passes_through() -> None:
    out = "".join(redact_stream(["hello ", "world, nothing sensitive here"]))
    assert out == "hello world, nothing sensitive here"


# ---------- e2e: gateway SSE ----------

@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=llm_app)
    upstream = httpx.AsyncClient(transport=transport, base_url="http://llm")
    gateway = create_app("http://llm/v1/chat/completions", client=upstream)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gateway), base_url="http://gw"
    ) as c:
        yield c


async def _collect_sse(client: httpx.AsyncClient) -> tuple[str, float, int]:
    """POST and stream; returns (full text, seconds to first byte, event count)."""
    full = ""
    events = 0
    ttft = None
    start = time.monotonic()
    async with client.stream(
        "POST",
        "/v1/chat/completions",
        json={"model": "mock", "messages": [{"role": "user", "content": "hi"}], "stream": True},
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        async for line in resp.aiter_lines():
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            if ttft is None:
                ttft = time.monotonic() - start
            event = json.loads(payload)
            events += 1
            delta = (((event.get("choices") or [{}])[0]).get("delta") or {}).get("content")
            if delta:
                full += delta
    assert ttft is not None
    return full, ttft, events


async def test_stream_redacts_all_pii(client: httpx.AsyncClient) -> None:
    full, ttft, events = await _collect_sse(client)
    assert "ada.lovelace@example.com" not in full
    assert "123-45-6789" not in full
    assert "4111 1111 1111 1111" not in full
    assert "grace+hopper@mail.org" not in full
    assert full.count(REDACTED) == 4
    assert "Sure! Contact" in full  # non-PII content preserved
    assert events > 1
    assert ttft < 1.0  # first token arrives quickly, not after full buffering


async def test_ttft_beats_full_response_time(client: httpx.AsyncClient) -> None:
    """First token must arrive before the full response is buffered."""
    _, ttft, events = await _collect_sse(client)
    assert events > 1
    assert ttft < 0.5  # mock sleeps 10ms/chunk; buffering everything would exceed this easily
