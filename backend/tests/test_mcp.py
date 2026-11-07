import asyncio
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime, timedelta, timezone
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def test_real_stdio_protocol(env):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid="mcp-test", alg="RS256", use="sig")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"keys": [jwk]}).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    current = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "sub": env.user,
            "iss": base + "/auth/v1",
            "aud": "authenticated",
            "iat": current,
            "exp": current + timedelta(minutes=5),
        },
        key,
        algorithm="RS256",
        headers={"kid": "mcp-test"},
    )
    process_env = {
        **os.environ,
        "DATABASE_URL": str(env.engine.url),
        "SUPABASE_URL": base,
        "CAMPUS_ACCESS_TOKEN": token,
        "CAMPUS_TENANT_ID": env.tenant,
    }

    async def run():
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "backend.app.mcp_server"], env=process_env
        )
        results = []
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = await session.list_tools()
                assert len(listed.tools) == 15
                for name, args in [
                    ("get_my_timetable", {"on": "2026-10-29", "at": "14:00"}),
                    ("search_canteen_items", {"budget": 100, "vegetarian": True}),
                    ("search_documents", {"query": "internal assessment circular"}),
                ]:
                    result = await session.call_tool(name, args)
                    assert not result.isError
                    data = json.loads(result.content[0].text)
                    assert data["tenant_id"] == env.tenant
                    assert data["data"]
                    results.append({"tool": name, "is_error": False})
                invalid = await session.call_tool("search_canteen_items", {"budget": -1})
                assert invalid.isError
        return results

    try:
        results = asyncio.run(run())
        from pathlib import Path

        path = Path("artifacts")
        path.mkdir(exist_ok=True)
        (path / "mcp-verification.json").write_text(
            json.dumps(
                {
                    "transport": "Official Python MCP SDK stdio",
                    "tools_discovered": 15,
                    "calls": results,
                    "invalid_input_rejected": True,
                },
                indent=2,
            )
        )
    finally:
        server.shutdown()
        server.server_close()
