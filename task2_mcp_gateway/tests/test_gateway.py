"""Task 2 tests: bearer-role extraction, admin_ interception (-32001), passthrough."""
from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from task2_mcp_gateway.gateway import create_app
from task2_mcp_gateway.mock_backend import app as backend_app

VIEWER = {"Authorization": "Bearer tok-viewer-456"}
ADMIN = {"Authorization": "Bearer tok-admin-123"}


@pytest.fixture
async def client():
    """Gateway wired to an in-process mock backend via ASGI transport."""
    transport = httpx.ASGITransport(app=backend_app)
    backend_client = httpx.AsyncClient(transport=transport, base_url="http://backend")
    gateway: FastAPI = create_app("http://backend/mcp", client=backend_client)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gateway), base_url="http://gateway"
    ) as c:
        yield c


async def test_tools_list_passthrough_viewer(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/mcp",
        headers=VIEWER,
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    )
    assert resp.status_code == 200
    names = {t["name"] for t in resp.json()["result"]["tools"]}
    assert "admin_reset_key" in names


async def test_admin_tool_blocked_for_viewer_without_downstream_call(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/mcp",
        headers=VIEWER,
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "admin_reset_key", "arguments": {}}},
    )
    body = resp.json()
    assert body["error"]["code"] == -32001
    assert "admin" in body["error"]["message"]
    assert body["id"] == 2


async def test_admin_tool_allowed_for_admin_and_forwarded(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/mcp",
        headers=ADMIN,
        json={"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "admin_reset_key", "arguments": {}}},
    )
    assert resp.json()["result"]["structuredContent"]["rotated"] is True


async def test_non_admin_tool_forwarded_for_viewer(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/mcp",
        headers=VIEWER,
        json={
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "get_customer_record", "arguments": {"customer_id": "CUST-00001"}},
        },
    )
    assert resp.json()["result"]["structuredContent"]["customer_id"] == "CUST-00001"


async def test_missing_or_bad_token_rejected(client: httpx.AsyncClient) -> None:
    for headers in ({}, {"Authorization": "Bearer nope"}, {"Authorization": "Basic abc"}):
        resp = await client.post(
            "/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 5, "method": "tools/list", "params": {}}
        )
        assert resp.json()["error"]["code"] == -32001


async def test_malformed_json_body_parse_error(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/mcp",
        headers={**VIEWER, "Content-Type": "application/json"},
        content=b"{not json",
    )
    assert resp.json()["error"]["code"] == -32700
