import os

from app.mcp_server import register_mcp_tools


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


if __name__ == "__main__":
    transport = os.getenv("MCP_TRANSPORT", "stdio")
    host = os.getenv("MCP_HOST", "127.0.0.1")
    port = _env_int("MCP_PORT", 8001)
    path = os.getenv("MCP_STREAMABLE_PATH", "/mcp")

    mcp = register_mcp_tools(host=host, port=port, streamable_http_path=path)
    mcp.run(transport=transport)
