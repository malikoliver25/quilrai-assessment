"""Task 2: MCP Security Gateway Proxy.

Sits between an AI agent client and a downstream MCP server.
- `Authorization: Bearer <token>` maps to a role (admin / viewer).
- `tools/list` is forwarded transparently.
- `tools/call` with a name starting with `admin_` requires role=admin;
  otherwise the gateway returns JSON-RPC error -32001 WITHOUT calling
  the downstream server.

Run: python -m task2_mcp_gateway.gateway  (env: DOWNSTREAM_URL, default http://127.0.0.1:9001/mcp)
"""
from __future__ import annotations

import os

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

UNAUTHORIZED_TOOL_CALL = -32001

# Static token registry for the demo gateway.
TOKENS = {
    "tok-admin-123": "admin",
    "tok-viewer-456": "viewer",
}


def role_for_token(auth_header: str | None) -> str | None:
    if not auth_header or not auth_header.startswith("Bearer "):
        return None
    return TOKENS.get(auth_header.removeprefix("Bearer ").strip())


def create_app(downstream_url: str | None = None, client: httpx.AsyncClient | None = None) -> FastAPI:
    downstream_url = downstream_url or os.environ.get("DOWNSTREAM_URL", "http://127.0.0.1:9001/mcp")
    app = FastAPI(title="mcp-security-gateway")
    app.state.http = client or httpx.AsyncClient(timeout=30.0)

    @app.post("/mcp")
    async def mcp_proxy(request: Request) -> JSONResponse:
        role = role_for_token(request.headers.get("authorization"))
        if role is None:
            return JSONResponse(
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32001, "message": "Missing or invalid Bearer token"}}
            )
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
            )
        method = body.get("method")
        msg_id = body.get("id")

        if method == "tools/call":
            name = (body.get("params") or {}).get("name", "")
            if name.startswith("admin_") and role != "admin":
                # Intercepted: never forwarded downstream.
                return JSONResponse(
                    {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "error": {"code": UNAUTHORIZED_TOOL_CALL, "message": f"Unauthorized Tool Call: '{name}' requires role admin"},
                    }
                )

        resp = await app.state.http.post(downstream_url, json=body)
        return JSONResponse(resp.json(), status_code=resp.status_code)

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True, "downstream": downstream_url}

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", "8000")))
