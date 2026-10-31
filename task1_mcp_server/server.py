"""Task 1: Custom MCP server with strict validation, stdio transport only.

stdout carries ONLY JSON-RPC frames. All logging goes to stderr.
Run: python -m task1_mcp_server
"""
from __future__ import annotations

import anyio
import json
import logging
import math
import re
import sys

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import McpError
from pydantic import BaseModel, Field, ValidationError, field_validator

# Logging MUST go to stderr so stdout stays pure JSON-RPC.
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="[task1-mcp-server] %(levelname)s %(message)s",
)
log = logging.getLogger("task1")

CUSTOMER_ID_RE = re.compile(r"^CUST-\d{5}$")
INVALID_PARAMS = types.INVALID_PARAMS  # -32602


def _validate_id(v: str) -> str:
    if not CUSTOMER_ID_RE.match(v):
        raise ValueError("customer_id must be formatted as CUST-XXXXX (5 digits)")
    return v


class CustomerId(BaseModel):
    customer_id: str = Field(..., min_length=1)

    _check = field_validator("customer_id")(_validate_id)


class RefundRequest(BaseModel):
    customer_id: str
    amount: float = Field(..., gt=0)
    reason: str = Field(..., min_length=10)

    _check_id = field_validator("customer_id")(_validate_id)

    @field_validator("amount")
    @classmethod
    def _finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("amount must be a finite number")
        return v


# Mock datastore.
_CUSTOMERS: dict[str, dict] = {
    "CUST-00001": {"customer_id": "CUST-00001", "name": "Ada Lovelace", "balance": 120.0},
    "CUST-00002": {"customer_id": "CUST-00002", "name": "Alan Turing", "balance": 75.5},
}
_REFUNDS: list[dict] = []

server = Server("quilrai-task1")


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="get_customer_record",
            description="Look up a customer record by customer_id (CUST-XXXXX).",
            inputSchema=CustomerId.model_json_schema(),
        ),
        types.Tool(
            name="trigger_refund",
            description="Trigger a refund: positive amount, reason >= 10 chars.",
            inputSchema=RefundRequest.model_json_schema(),
        ),
    ]


def _invalid_params(exc: ValidationError) -> McpError:
    fields = sorted({str(e["loc"][0]) for e in exc.errors()})
    return McpError(
        types.ErrorData(code=INVALID_PARAMS, message=f"Invalid params ({', '.join(fields)}): {exc.errors()[0]['msg']}")
    )


async def _call_tool(req: types.CallToolRequest) -> types.ServerResult:
    name = req.params.name
    arguments = req.params.arguments or {}
    if not isinstance(arguments, dict):
        raise McpError(types.ErrorData(code=INVALID_PARAMS, message="arguments must be an object"))
    try:
        if name == "get_customer_record":
            parsed = CustomerId(**arguments)
            customer = _CUSTOMERS.get(parsed.customer_id)
            if customer is None:
                return types.ServerResult(
                    types.CallToolResult(
                        content=[types.TextContent(type="text", text=f"no record for {parsed.customer_id}")],
                        isError=True,
                    )
                )
            log.info("served record for %s", parsed.customer_id)
            return types.ServerResult(
                types.CallToolResult(
                    content=[types.TextContent(type="text", text=json.dumps(customer))],
                    structuredContent=dict(customer),
                )
            )
        if name == "trigger_refund":
            parsed = RefundRequest(**arguments)
            refund = {
                "refund_id": f"RF-{len(_REFUNDS) + 1:05d}",
                "customer_id": parsed.customer_id,
                "amount": parsed.amount,
                "reason": parsed.reason,
                "status": "issued",
            }
            _REFUNDS.append(refund)
            log.info("issued refund %s", refund["refund_id"])
            return types.ServerResult(
                types.CallToolResult(
                    content=[types.TextContent(type="text", text=json.dumps(refund))],
                    structuredContent=refund,
                )
            )
        raise McpError(types.ErrorData(code=INVALID_PARAMS, message=f"Unknown tool: {name}"))
    except ValidationError as exc:
        raise _invalid_params(exc) from exc


# Raw handler registration: McpError propagates to a real JSON-RPC error
# response; the @server.call_tool decorator would swallow it into an
# isError tool result instead.
server.request_handlers[types.CallToolRequest] = _call_tool


async def _amain() -> None:
    async with stdio_server() as (read_stream, write_stream):
        log.info("starting stdio MCP server (stdout reserved for JSON-RPC)")
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    anyio.run(_amain)


if __name__ == "__main__":
    main()
