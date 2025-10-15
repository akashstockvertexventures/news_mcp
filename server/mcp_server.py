# mcp_server.py
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from datetime import datetime

import mcp.types as types
from mcp.server.fastmcp import FastMCP
from pymongo import MongoClient, DESCENDING

# ===== Widget metadata =====
MIME_TYPE = "text/html+skybridge"


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
    template_uri="ui://widget/news-impact.html",  # must match HTML below
    invoking="Rendering News Impact",
    invoked="News Impact ready",
    html_path=os.path.join(
        os.path.dirname(__file__), "..", "components", "news-impact", "index.html"
    ),
)

# ===== Mongo config =====
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = os.environ.get("MONGO_DB", "newsdb")
COLL_NAME = os.environ.get("MONGO_COLL", "news")

# ===== FastMCP app =====
mcp = FastMCP(
    name="news-impact-python",
    sse_path="/mcp",
    message_path="/mcp/messages",
    stateless_http=True,
)

# ===== Input schema =====
NEWS_QUERY_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "title": "NewsImpactMongoQueryWithLimit",
    "description": "Return a MongoDB query object (under 'query') and a limit (1–50). Results are sorted by dt_tm desc.",
    "properties": {
        "query": {
            "type": "object",
            "description": "Exact MongoDB query filter for the news impact collection.",
            "properties": {
                "sentiment": {
                    "type": "string",
                    "enum": ["Positive", "Neutral", "Negative"],
                    "description": "Sentiment tone of the news item.",
                },
                "symbolmap.NSE": {
                    "type": "string",
                    "description": "Exact NSE code for the company, e.g., RELIANCE, TCS.",
                },
                "symbolmap.Company_Name": {
                    "type": "object",
                    "description": "Case-insensitive substring match for the company name.",
                    "properties": {
                        "$regex": {"type": "string"},
                        "$options": {"type": "string", "enum": ["i"]},
                    },
                    "required": ["$regex", "$options"],
                },
                "impact score": {
                    "type": "object",
                    "description": "Numeric impact score filter using MongoDB comparison operators.",
                    "properties": {
                        "$gt": {"type": "number"},
                        "$gte": {"type": "number"},
                        "$lt": {"type": "number"},
                        "$lte": {"type": "number"},
                        "$eq": {"type": "number"},
                    },
                    "minProperties": 1,
                    "maxProperties": 2,
                },
            },
            "additionalProperties": False,
        },
        "limit": {
            "type": "integer",
            "description": "Maximum number of documents to return (1–50). Default = 10.",
            "minimum": 1,
            "maximum": 50,
            "default": 10,
        },
    },
    "required": ["query"],
}

# ===== Helpers =====
def _load_widget_html() -> str:
    path = os.path.abspath(WIDGET.html_path)
    if not os.path.exists(path):
        return (
            "<!doctype html><meta charset='utf-8'><title>News Impact</title>"
            "<p>index.html not found at: " + path + "</p>"
        )
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _tool_meta() -> Dict[str, Any]:
    return {
        "openai/outputTemplate": WIDGET.template_uri,
        "openai/toolInvocation/invoking": WIDGET.invoking,
        "openai/toolInvocation/invoked": WIDGET.invoked,
        "openai/widgetAccessible": True,
        "openai/resultCanProduceWidget": True,
        "annotations": {"destructiveHint": False, "openWorldHint": False, "readOnlyHint": True},
    }


def _embedded_widget_resource() -> types.EmbeddedResource:
    return types.EmbeddedResource(
        type="resource",
        resource=types.TextResourceContents(
            uri=WIDGET.template_uri,
            mimeType=MIME_TYPE,
            text=_load_widget_html(),
            title=WIDGET.title,
        ),
    )


def _fetch_docs(query: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
    if not isinstance(limit, int):
        limit = 10
    limit = max(1, min(50, limit))

    client = MongoClient(MONGO_URI)
    coll = client[DB_NAME][COLL_NAME]

    projection = {
        "_id": 0,
        "symbolmap.Company_Name": 1,  # -> company
        "symbolmap.NSE": 1,           # -> symbol
        "dt_tm": 1,                   # -> dt
        "short summary": 1,           # -> summary
        "impact": 1,                  # -> impact
        "impact score": 1,            # -> score
        "sentiment": 1,               # -> sentiment
        "news link": 1,               # -> link
    }

    cur = coll.find(query or {}, projection).sort("dt_tm", DESCENDING).limit(limit)
    return list(cur)


def _to_iso(dt: Any) -> Optional[str]:
    if dt is None:
        return None
    if isinstance(dt, str):
        return dt
    if isinstance(dt, datetime):
        return dt.isoformat()
    try:
        return str(dt)
    except Exception:
        return None


def _to_num_0_10(x: Any) -> Optional[float]:
    try:
        n = float(x)
        return 0.0 if n < 0 else 10.0 if n > 10 else n
    except Exception:
        return None


def _normalize_docs(docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for d in docs:
        sm = d.get("symbolmap") or {}
        out.append(
            {
                "company": (sm.get("Company_Name") or "").strip(),
                "symbol": (sm.get("NSE") or "").strip(),
                "dt": _to_iso(d.get("dt_tm")),
                "summary": (d.get("short summary") or "").strip(),
                "impact": (d.get("impact") or "").strip(),
                "score": _to_num_0_10(d.get("impact score")),
                "sentiment": (d.get("sentiment") or "").strip() or "Neutral",
                "link": (d.get("news link") or "").strip(),
            }
        )
    return out


# ===== MCP definitions =====
@mcp._mcp_server.list_tools()
async def _list_tools() -> List[types.Tool]:
    return [
        types.Tool(
            name="news-impact",
            title=WIDGET.title,
            description="Render the News Impact carousel using a MongoDB query and limit.",
            inputSchema=NEWS_QUERY_SCHEMA,
            _meta=_tool_meta(),
        )
    ]


@mcp._mcp_server.list_resources()
async def _list_resources() -> List[types.Resource]:
    return [
        types.Resource(
            name=WIDGET.title,
            title=WIDGET.title,
            uri=WIDGET.template_uri,
            description="News Impact widget HTML",
            mimeType=MIME_TYPE,
            _meta=_tool_meta(),
        )
    ]


@mcp._mcp_server.list_resource_templates()
async def _list_resource_templates() -> List[types.ResourceTemplate]:
    return [
        types.ResourceTemplate(
            name=WIDGET.title,
            title=WIDGET.title,
            uriTemplate=WIDGET.template_uri,
            description="News Impact widget HTML",
            mimeType=MIME_TYPE,
            _meta=_tool_meta(),
        )
    ]


# ===== Handlers =====
async def _handle_read_resource(req: types.ReadResourceRequest) -> types.ServerResult:
    if str(req.params.uri) != WIDGET.template_uri:
        return types.ServerResult(
            types.ReadResourceResult(contents=[], _meta={"error": f"Unknown resource: {req.params.uri}"})
        )
    contents = [
        types.TextResourceContents(
            uri=WIDGET.template_uri,
            mimeType=MIME_TYPE,
            text=_load_widget_html(),
            _meta=_tool_meta(),
        )
    ]
    return types.ServerResult(types.ReadResourceResult(contents=contents))


async def _call_tool_request(req: types.CallToolRequest) -> types.ServerResult:
    args = req.params.arguments or {}

    if "query" not in args:
        return types.ServerResult(
            types.CallToolResult(
                content=[types.TextContent(type="text", text="Field 'query' is required.")],
                isError=True,
            )
        )

    query = args.get("query") or {}
    limit = args.get("limit", 10)

    allowed = {"sentiment", "symbolmap.NSE", "symbolmap.Company_Name", "impact score"}
    if not isinstance(query, dict) or any(k not in allowed for k in query.keys()):
        return types.ServerResult(
            types.CallToolResult(
                content=[
                    types.TextContent(
                        type="text",
                        text="Invalid query keys. Allowed: sentiment, symbolmap.NSE, symbolmap.Company_Name, impact score.",
                    )
                ],
                isError=True,
            )
        )

    try:
        docs = _fetch_docs(query, int(limit))
        items = _normalize_docs(docs)
    except Exception as e:
        return types.ServerResult(
            types.CallToolResult(content=[types.TextContent(type="text", text=f"Query error: {e}")], isError=True)
        )

    widget_resource = _embedded_widget_resource()
    meta = {"openai.com/widget": widget_resource.model_dump(mode="json"), **_tool_meta()}

    return types.ServerResult(
        types.CallToolResult(
            content=[types.TextContent(type="text", text=f"Fetched {len(items)} item(s) for News Impact.")],
            structuredContent={"items": items},  # ONLY items
            _meta=meta,
        )
    )


# Register handlers
mcp._mcp_server.request_handlers[types.CallToolRequest] = _call_tool_request
mcp._mcp_server.request_handlers[types.ReadResourceRequest] = _handle_read_resource

# Expose ASGI app
app = mcp.streamable_http_app()

# Optional: CORS for local testing
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
