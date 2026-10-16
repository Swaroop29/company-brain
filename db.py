"""FalkorDB connection helper.

Local Docker (default):   FALKORDB_HOST=localhost FALKORDB_PORT=6379
FalkorDB Cloud:           also set FALKORDB_USERNAME / FALKORDB_PASSWORD (and FALKORDB_SSL=true if asked)
"""
import os

from dotenv import load_dotenv

load_dotenv()

GRAPH_NAME = os.getenv("GRAPH_NAME", "companybrain")

_graph = None


def get_graph():
    global _graph
    if _graph is not None:
        return _graph

    lite_path = os.getenv("FALKORDB_LITE_PATH")  # only used for offline testing
    if lite_path:
        from redislite.falkordb_client import FalkorDB
        db = FalkorDB(lite_path)
    else:
        from falkordb import FalkorDB
        kwargs = dict(
            host=os.getenv("FALKORDB_HOST", "localhost"),
            port=int(os.getenv("FALKORDB_PORT", "6379")),
        )
        if os.getenv("FALKORDB_USERNAME"):
            kwargs["username"] = os.getenv("FALKORDB_USERNAME")
        if os.getenv("FALKORDB_PASSWORD"):
            kwargs["password"] = os.getenv("FALKORDB_PASSWORD")
        if os.getenv("FALKORDB_SSL", "").lower() in ("1", "true", "yes"):
            kwargs["ssl"] = True
        db = FalkorDB(**kwargs)

    _graph = db.select_graph(GRAPH_NAME)
    return _graph


def graph_is_empty() -> bool:
    res = get_graph().query("MATCH (n) RETURN count(n)")
    return res.result_set[0][0] == 0
