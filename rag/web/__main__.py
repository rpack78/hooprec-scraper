"""Allow running as: python -m rag.web"""

from rag.web.app import app  # noqa: F401

if __name__ == "__main__":
    import os
    import uvicorn

    port = int(os.getenv("RAG_WEB_PORT", "8000"))
    uvicorn.run(
        "rag.web.app:app",
        host="127.0.0.1",
        port=port,
        reload=False,
        log_level="info",
    )
