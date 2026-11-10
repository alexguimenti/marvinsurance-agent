"""Serve the context layer on this computer: `uv run -m context`, from the backend folder, at http://127.0.0.1:8006/mcp."""
import uvicorn

from context.server import build_server

if __name__ == "__main__":
    uvicorn.run(build_server().streamable_http_app(), host="127.0.0.1", port=8006, log_level="warning")
