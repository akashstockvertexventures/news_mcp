# mcp_server.py
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple
from datetime import datetime

from pymongo import MongoClient, DESCENDING
from pymongo.errors import PyMongoError

import mcp.types as types
from mcp.server.fastmcp import FastMCP

# ============================================================
# Config & Constants
# ============================================================

MIME_TYPE = "text/html+skybridge"

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.environ.get("MONGO_DB", "newsdb")
MONGO_COLL = os.environ.get("MONGO_COLL", "news")

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

ALLOWED_QUERY_KEYS = {
    "sentiment",              # "Positive" | "Neutral" | "Negative"
    "symbolmap.NSE",          # exact NSE symbol, e.g., "RELIANCE"
    "symbolmap.Company_Name", # {"$regex": "...", "$options": "i"}
    "impact score",           # comparison operators ($gt, $gte, $lt, $lte, $eq)
}

# ============================================================
# Logging
# ============================================================

logger = logging.getLogger("news-impact-mcp")
handler = logging.StreamHandler()
formatter = logging.Formatter(
    "[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(LOG_LEVEL)

# ============================================================
# Error Types (clean, user-facing)
# ============================================================

class NewsImpactError(Exception):
    """Base error for predictable user-facing exceptions."""

class ValidationError(NewsImpactError):
    """Input validation or schema error."""

class MongoQueryError(NewsImpactError):
    """Database access or query failure."""

# ============================================================
# Widget Descriptor
# ============================================================

@dataclass(frozen=True)
class NewsWidget:
    identifier: str
    title: str
    template_uri: str
    invoking: str
    invoked: str
    html_path: str

WIDGET = NewsWidget(
    identifier="news-impact",
    title="News Impact Carousel",
    template_uri="ui://widget/news-impact.html",
    invoking="Fetching News Impact…",
    invoked="News Impact ready",
    # NOTE: if mcp_server.py sits next to a 'components' folder (no parent), remove '..,' below.
    html_path=os.path.join(os.path.dirname(__file__), "..", "components", "news-impact", "index.html"),
)

# ============================================================
# FastMCP App
# ============================================================

mcp = FastMCP(
    name="news-impact-python",
    sse_path="/mcp",
    message_path="/mcp/messages",
    stateless_http=True,
)

# ============================================================
# Input Schema
# ============================================================

NEWS_QUERY_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "title": "NewsImpactMongoQueryWithLimit",
    "description": (
        "Provide a MongoDB filter under 'query' (allowed keys only) and an optional 'limit' (1–50). "
        "Results are sorted by dt_tm desc."
    ),
    "properties": {
        "query": {
            "type": "object",
            "properties": {
                "sentiment": {"type": "string", "enum": ["Positive", "Neutral,","Negative"]},  # typo fixed below
                "symbolmap.NSE": {"type": "string"},
                "symbolmap.Company_Name": {
                    "type": "object",
                    "properties": {
                        "$regex": {"type": "string"},
                        "$options": {"type": "string", "enum": ["i"]},
                    },
                    "required": ["$regex", "$options"],
                    "additionalProperties": False,
                },
                "impact score": {
                    "type": "object",
                    "properties": {
                        "$gt": {"type": "number"},
                        "$gte": {"type": "number"},
                        "$lt": {"type": "number"},
                        "$lte": {"type": "number"},
                        "$eq": {"type": "number"},
                    },
                    "minProperties": 1,
                    "maxProperties": 2,
                    "additionalProperties": False,
                },
            },
            "additionalProperties": False,
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 50,
            "default": 10,
        },
    },
    "required": ["query"],
    "additionalProperties": False,
}
# fix the small enum typo above:
NEWS_QUERY_SCHEMA["properties"]["query"]["properties"]["sentiment"]["enum"] = ["Positive", "Neutral", "Negative"]

# ============================================================
# Helpers
# ============================================================

def _load_widget_html() -> str:
    """Load component HTML; return a simple fallback if missing."""
    path = os.path.abspath(WIDGET.html_path)
    if not os.path.exists(path):
        logger.warning("Widget HTML not found at %s", path)
        return (
            "<!doctype html><meta charset='utf-8'><title>News Impact</title>"
            "<style>body{font-family:system-ui,Segoe UI,Roboto,Arial}</style>"
            "<h2>News Impact</h2>"
            "<p><em>index.html</em> not found at:<br><code>" + path + "</code></p>"
        )
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def _tool_descriptor_meta() -> Dict[str, Any]:
    """Advertise tool but keep widget locked; we only enable after non-empty results."""
    return {
        "openai/outputTemplate": WIDGET.template_uri,
        "openai/toolInvocation/invoking": WIDGET.invoking,
        "openai/toolInvocation/invoked": WIDGET.invoked,
        "openai/widgetAccessible": False,
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
    }

def _to_iso(x):
    if isinstance(x, datetime):
        try:
            return x.isoformat()
        except Exception:
            return str(x)
    return x if (x is None or isinstance(x, (str, int, float, bool))) else str(x)

def _preview(obj, maxlen=600):
    try:
        s = json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        s = str(obj)
    return (s[:maxlen] + " …") if len(s) > maxlen else s

def _validate_and_normalize_args(args: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
    if not isinstance(args, dict):
        raise ValidationError("Arguments must be an object.")
    if "query" not in args:
        raise ValidationError("Field 'query' is required.")

    query = args.get("query") or {}
    if not isinstance(query, dict):
        raise ValidationError("'query' must be an object.")

    unknown = [k for k in query.keys() if k not in ALLOWED_QUERY_KEYS]
    if unknown:
        raise ValidationError(
            "Invalid query keys: " + ", ".join(unknown) +
            ". Allowed: " + ", ".join(sorted(ALLOWED_QUERY_KEYS))
        )

    if "sentiment" in query and query["sentiment"] not in ("Positive", "Neutral", "Negative"):
        raise ValidationError("sentiment must be one of: Positive, Neutral, Negative.")

    if "symbolmap.Company_Name" in query:
        sub = query["symbolmap.Company_Name"]
        if not isinstance(sub, dict):
            raise ValidationError("symbolmap.Company_Name must be an object.")
        if set(sub.keys()) != {"$regex", "$options"}:
            raise ValidationError("symbolmap.Company_Name must have keys: $regex and $options.")
        if not isinstance(sub.get("$regex"), str) or not sub.get("$regex"):
            raise ValidationError("symbolmap.Company_Name.$regex must be a non-empty string.")
        if sub.get("$options") != "i":
            raise ValidationError("symbolmap.Company_Name.$options must be 'i'.")

    if "impact score" in query:
        cmp_obj = query["impact score"]
        if not isinstance(cmp_obj, dict) or not cmp_obj:
            raise ValidationError("impact score must be an object with comparison operator(s).")
        allowed_ops = {"$gt", "$gte", "$lt", "$lte", "$eq"}
        if any(op not in allowed_ops for op in cmp_obj.keys()):
            raise ValidationError("impact score uses only: $gt, $gte, $lt, $lte, $eq.")
        if not all(isinstance(v, (int, float)) for v in cmp_obj.values()):
            raise ValidationError("impact score operator values must be numbers.")

    limit = args.get("limit", 10)
    try:
        limit = int(limit)
    except Exception:
        raise ValidationError("'limit' must be an integer between 1 and 50.")
    if not (1 <= limit <= 50):
        raise ValidationError("'limit' must be between 1 and 50.")
    return query, limit

def _fetch_from_mongo(query: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
    projection = {
        "_id": 0,
        "symbolmap.Company_Name": 1,
        "symbolmap.NSE": 1,
        "dt_tm": 1,
        "short summary": 1,
        "impact": 1,
        "impact score": 1,
        "sentiment": 1,
        "news link": 1,
    }
    try:
        client = MongoClient(MONGO_URI)
        coll = client[MONGO_DB][MONGO_COLL]
        cur = coll.find(query, projection).sort("dt_tm", DESCENDING).limit(limit)
        docs = list(cur)
        logger.info("[TOOL] Mongo query returned %d doc(s)", len(docs))
        return docs
    except PyMongoError as e:
        raise MongoQueryError(f"MongoDB error: {e}")

def _normalize_docs(docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for d in docs:
        symbolmap = d.get("symbolmap") or {}
        normalized.append(
            {
                "company": symbolmap.get("Company_Name", "") or "",
                "symbol": symbolmap.get("NSE", "") or "",
                "dt": _to_iso(d.get("dt_tm")),
                "summary": d.get("short summary", "") or "",
                "impact": d.get("impact"),
                "score": d.get("impact score"),
                "sentiment": d.get("sentiment"),
                "link": d.get("news link", "") or "",
            }
        )
    return normalized

# ============================================================
# MCP: Tools & Resources
# ============================================================

@mcp._mcp_server.list_tools()
async def _list_tools() -> List[types.Tool]:
    return [
        types.Tool(
            name="news-impact",
            title=WIDGET.title,
            description="Query MongoDB and (only if results are found) render the News Impact carousel.",
            inputSchema=NEWS_QUERY_SCHEMA,
            _meta=_tool_descriptor_meta(),
        )
    ]

async def _handle_read_resource(req: types.ReadResourceRequest) -> types.ServerResult:
    uri = str(req.params.uri)
    logger.info("[READ_RESOURCE] %s", uri)
    if uri != WIDGET.template_uri:
        logger.warning("[READ_RESOURCE] unknown uri=%s (expected %s)", uri, WIDGET.template_uri)
        return types.ServerResult(
            types.ReadResourceResult(contents=[], _meta={"error": f"Unknown resource: {uri}"})
        )
    html = _load_widget_html()
    contents = [
        types.TextResourceContents(
            uri=WIDGET.template_uri,
            mimeType=MIME_TYPE,
            text=html,
            title=WIDGET.title,
            _meta={
                "openai/widgetDescription": "Scrollable News Impact carousel",
                "openai/widgetPrefersBorder": True,
                "openai/widgetCSP": {"connect_domains": [], "resource_domains": []},
            },
        )
    ]
    return types.ServerResult(types.ReadResourceResult(contents=contents))

async def _call_tool_request(req: types.CallToolRequest) -> types.ServerResult:
    tool_name = req.params.name
    args = req.params.arguments or {}
    logger.debug("[CALL_TOOL] %s args=%s", tool_name, json.dumps(args, default=str))

    if tool_name != "news-impact":
        return types.ServerResult(
            types.CallToolResult(
                content=[types.TextContent(type="text", text="Unknown tool.")],
                isError=True,
            )
        )

    try:
        query, limit = _validate_and_normalize_args(args)
        docs = _fetch_from_mongo(query, limit)
        normalized = _normalize_docs(docs)
    except ValidationError as ve:
        logger.warning("[CALL_TOOL] validation error: %s", ve)
        return types.ServerResult(
            types.CallToolResult(
                content=[types.TextContent(type="text", text=f"Validation error: {ve}")],
                isError=True,
            )
        )
    except MongoQueryError as me:
        logger.error("[CALL_TOOL] mongo error: %s", me)
        return types.ServerResult(
            types.CallToolResult(
                content=[types.TextContent(type="text", text=f"Database error: {me}")],
                isError=True,
            )
        )
    except Exception as e:
        logger.exception("[CALL_TOOL] unexpected error")
        return types.ServerResult(
            types.CallToolResult(
                content=[types.TextContent(type="text", text=f"Unexpected error: {e}")],
                isError=True,
            )
        )

    logger.info("[CALL_TOOL] normalized=%d", len(normalized))
    if normalized:
        logger.debug("[CALL_TOOL] first item: %s", _preview(normalized[0]))

    # No results → no widget
    if not normalized:
        return types.ServerResult(
            types.CallToolResult(
                content=[types.TextContent(type="text", text="No news matched your query. Widget not rendered.")],
                structuredContent={"items": []},
            )
        )

    # Results found → enable widget rendering now
    result_meta = {
        "openai/widgetAccessible": True,
        "openai/resultCanProduceWidget": True,
        "openai/outputTemplate": WIDGET.template_uri,
        "openai/toolInvocation/invoking": WIDGET.invoking,
        "openai/toolInvocation/invoked": WIDGET.invoked,
    }

    return types.ServerResult(
        types.CallToolResult(
            content=[types.TextContent(type="text", text=f"Fetched {len(normalized)} item(s) for News Impact.")],
            structuredContent={"items": normalized},
            _meta=result_meta,
        )
    )

# Register handlers explicitly
mcp._mcp_server.request_handlers[types.CallToolRequest] = _call_tool_request
mcp._mcp_server.request_handlers[types.ReadResourceRequest] = _handle_read_resource

# Expose ASGI app
app = mcp.streamable_http_app()

# Optional: CORS for local dev / preview
try:
    from starlette.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=False,
    )
except Exception:
    pass

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("mcp_server:app", host="0.0.0.0", port=8000, reload=False)
