"""MCP server over stdio that searches the knowledge base stored in qdrant_data."""
import threading
from pathlib import Path

from fastmcp import FastMCP
from qdrant_client import QdrantClient, models

COLLECTION = "knowledge"
EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"

client = QdrantClient(path=str(Path(__file__).resolve().parent.parent / "qdrant_data"))
client_lock = threading.Lock()  # FastMCP runs tool calls in threads, and the client's embedder is not thread safe
mcp = FastMCP("knowledge")


@mcp.tool
def search_knowledge(query: str, topic: str | None = None, limit: int = 5) -> list[dict]:
    """Searches the knowledge base by meaning. Topics: products, events, places."""
    query_filter = None
    if topic:
        query_filter = models.Filter(must=[models.FieldCondition(key="topic", match=models.MatchValue(value=topic))])
    with client_lock:
        hits = client.query_points(
            COLLECTION,
            query=models.Document(text=query, model=EMBEDDING_MODEL),
            query_filter=query_filter,
            limit=limit,
        ).points
    return [{**hit.payload, "score": hit.score} for hit in hits]


if __name__ == "__main__":
    mcp.run(show_banner=False)
