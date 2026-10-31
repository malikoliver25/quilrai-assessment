"""Mock downstream MCP server (JSON-RPC over HTTP POST /mcp)."""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

TOOLS = [
    {"name": "get_customer_record", "description": "Read a customer record."},
    {"name": "admin_reset_key", "description": "Rotate the tenant API key. Admin only."},
    {"name": "admin_list_users", "description": "List all tenant users. Admin only."},
]

app = FastAPI(title="mock-mcp-backend")


@app.post("/mcp")
async def mcp_endpoint(request: Request) -> JSONResponse:
    body = await request.json()
    method = body.get("method")
    msg_id = body.get("id")
    if method == "tools/list":
        return JSONResponse({"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}})
    if method == "tools/call":
        name = body.get("params", {}).get("name")
        if name == "admin_reset_key":
            result = {"rotated": True, "new_key_suffix": "…x9f2"}
        elif name == "admin_list_users":
            result = {"users": ["ada", "alan", "grace"]}
        elif name == "get_customer_record":
            result = {"customer_id": body["params"]["arguments"].get("customer_id"), "name": "Ada Lovelace"}
        else:
            return JSONResponse(
                {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": f"Method not found: {name}"}}
            )
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"content": [{"type": "text", "text": str(result)}], "structuredContent": result},
            }
        )
    return JSONResponse({"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": "Method not found"}})


@app.get("/health")
async def health() -> dict:
    return {"ok": True}
