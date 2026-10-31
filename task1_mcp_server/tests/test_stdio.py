"""Raw JSON-RPC over stdio: drives the server as a subprocess and asserts
stdout purity, protocol compliance, and validation error codes."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVER_CMD = [sys.executable, "-m", "task1_mcp_server"]


def _rpc(proc: subprocess.Popen, method: str, params: dict | None = None, msg_id: int = 1) -> dict:
    req = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        req["params"] = params
    proc.stdin.write((json.dumps(req) + "\n").encode())
    proc.stdin.flush()
    line = proc.stdout.readline()
    assert line, "server closed stdout without a response"
    return json.loads(line)


def _start() -> subprocess.Popen:
    return subprocess.Popen(
        SERVER_CMD,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=ROOT,
    )


def _handshake(proc: subprocess.Popen) -> None:
    resp = _rpc(
        proc,
        "initialize",
        {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"},
        },
        msg_id=0,
    )
    assert resp.get("result", {}).get("serverInfo", {}).get("name"), resp
    proc.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
    proc.stdin.flush()


def test_tools_list_and_valid_call() -> None:
    proc = _start()
    try:
        _handshake(proc)
        tools = _rpc(proc, "tools/list", msg_id=1)
        names = {t["name"] for t in tools["result"]["tools"]}
        assert names == {"get_customer_record", "trigger_refund"}

        ok = _rpc(
            proc,
            "tools/call",
            {"name": "get_customer_record", "arguments": {"customer_id": "CUST-00001"}},
            msg_id=2,
        )
        assert ok["result"]["structuredContent"]["name"] == "Ada Lovelace"

        refund = _rpc(
            proc,
            "tools/call",
            {
                "name": "trigger_refund",
                "arguments": {"customer_id": "CUST-00001", "amount": 25.5, "reason": "billing error on invoice"},
            },
            msg_id=3,
        )
        assert refund["result"]["structuredContent"]["status"] == "issued"
        assert refund["result"]["structuredContent"]["amount"] == 25.5
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_invalid_customer_id_returns_jsonrpc_error() -> None:
    proc = _start()
    try:
        _handshake(proc)
        resp = _rpc(
            proc,
            "tools/call",
            {"name": "get_customer_record", "arguments": {"customer_id": "cust-1"}},
            msg_id=1,
        )
        assert "error" in resp, resp
        assert resp["error"]["code"] == -32602, resp  # Invalid params
        assert "CUST-XXXXX" in resp["error"]["message"]
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_refund_validation_edges() -> None:
    proc = _start()
    try:
        _handshake(proc)
        cases = [
            ({"customer_id": "CUST-00001", "amount": 0, "reason": "valid reason here"}, "amount"),
            ({"customer_id": "CUST-00001", "amount": -5, "reason": "valid reason here"}, "amount"),
            ({"customer_id": "CUST-00001", "amount": 1.0, "reason": "short"}, "reason"),
            ({"customer_id": "BAD", "amount": 1.0, "reason": "valid reason here"}, "customer_id"),
        ]
        for i, (args, field) in enumerate(cases, start=1):
            resp = _rpc(proc, "tools/call", {"name": "trigger_refund", "arguments": args}, msg_id=i)
            assert resp.get("error", {}).get("code") == -32602, (args, resp)
            assert field in resp["error"]["message"], (args, resp)
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_stdout_is_pure_jsonrpc() -> None:
    """Every stdout line must parse as a JSON-RPC message; logs go to stderr."""
    proc = _start()
    try:
        _handshake(proc)
        for i in range(3):
            _rpc(
                proc,
                "tools/call",
                {"name": "get_customer_record", "arguments": {"customer_id": "CUST-00002"}},
                msg_id=i,
            )
        proc.stdin.close()
        proc.wait(timeout=10)
        out = proc.stdout.read().decode()
        err = proc.stderr.read().decode()
    finally:
        if proc.poll() is None:
            proc.terminate()
    for line in out.splitlines():
        msg = json.loads(line)  # raises if any non-JSON leaked to stdout
        assert msg.get("jsonrpc") == "2.0"
    assert "[task1-mcp-server]" in err, "expected server logs on stderr"
