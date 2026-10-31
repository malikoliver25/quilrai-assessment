# QuilrAI Assessment — MCP / LLM Gateway Tasks

Python monorepo (3.11+). One package per task, all tests under each package's `tests/`.

## Setup

```bash
uv venv .venv && uv pip install --python .venv/bin/python 'mcp<2' pydantic fastapi uvicorn httpx pytest pytest-asyncio
# or: .venv/bin/pip install -e ".[test]"
```

Run all tests:

```bash
.venv/bin/python -m pytest
```

## Task 1 — Custom MCP Server (stdio, strict validation)

`task1_mcp_server/server.py` — official `mcp` SDK (low-level `Server`), stdio transport.

- Tools: `get_customer_record(customer_id: CUST-XXXXX)`, `trigger_refund(customer_id, amount>0, reason>=10)`.
- Pydantic models enforce schema; violations raise JSON-RPC error `-32602` (Invalid params) as real JSON-RPC error responses (raw `request_handlers` registration — the `@server.call_tool` decorator would swallow them into `isError` tool results).
- stdout carries only JSON-RPC frames; all logs go to stderr.

```bash
.venv/bin/python -m task1_mcp_server
```

## Task 2 — MCP Security Gateway Proxy

`task2_mcp_gateway/gateway.py` + `mock_backend.py`.

- `POST /mcp` on the gateway; `Authorization: Bearer <token>` maps to role (`tok-admin-123` → admin, `tok-viewer-456` → viewer).
- `tools/list` forwards transparently; `tools/call` with `admin_*` names requires admin, else the gateway itself returns JSON-RPC `-32001` without touching downstream.

```bash
.venv/bin/python -m uvicorn task2_mcp_gateway.mock_backend:app --port 9001 &
DOWNSTREAM_URL=http://127.0.0.1:9001/mcp .venv/bin/python -m task2_mcp_gateway.gateway
```

## Task 3 — LLM Gateway Streaming Guardrail (PII redaction)

`task3_llm_guardrail/gateway.py` + `mock_llm.py` + `redactor.py`.

- `POST /v1/chat/completions` streams SSE from upstream, redacting email / SSN / credit-card matches to `[REDACTED]` in real time.
- `StreamRedactor` holds back only the suffix that could still be a prefix of a match (bounded, pattern-aware), so TTFT stays low and memory stays O(1) in response size.

```bash
.venv/bin/python -m uvicorn task3_llm_guardrail.mock_llm:app --port 9002 &
UPSTREAM_URL=http://127.0.0.1:9002/v1/chat/completions .venv/bin/python -m task3_llm_guardrail.gateway
```

## Task 4 — Rate-Limiting & Model Fallback Router

`task4_router/router.py` + `limiter.py`.

- sqlite-backed token-aware sliding window: 50,000 tokens/min per tenant API key (`x-api-key`), expired rows evicted on every check.
- Primary provider: 3000ms timeout; on HTTP 429 or timeout, automatic failover to the secondary provider.
- Failures return a standardized gateway error payload (`429 rate_limit_exceeded` / `502 upstream_error`) with no upstream stack traces.

```bash
PRIMARY_URL=http://127.0.0.1:9100/v1/chat/completions SECONDARY_URL=http://127.0.0.1:9101/v1/chat/completions \
  .venv/bin/python -m task4_router.router
```
