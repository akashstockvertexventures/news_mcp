# mcp_server.py
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, List

import mcp.types as types
from mcp.server.fastmcp import FastMCP
from pymongo import MongoClient, DESCENDING

# ===== Widget metadata =====
# If your host is picky about MIME, you can try "text/html" instead.
MIME_TYPE = "text/html+skybridge"


@dataclass(frozen=True)
class NewsWidget:
    """Widget metadata configuration for the News Impact carousel."""
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

# ===== Input schema (UNCHANGED) =====
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
                        "$regex": {
                            "type": "string",
                            "description": "Substring to match within the company name.",
                        },
                        "$options": {
                            "type": "string",
                            "enum": ["i"],
                            "description": "Must be 'i' for case-insensitive match.",
                        },
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
    "required": ["query"],  # allow 'limit' to be omitted; we default it in code
}


# ===== Helpers =====
def _load_widget_html() -> str:
    """Load the local HTML file for the News Impact widget."""
    path = os.path.abspath(WIDGET.html_path)
    if not os.path.exists(path):
        return (
            "<!doctype html><meta charset='utf-8'><title>News Impact</title>"
            "<p>index.html not found at: "
            + path
            + "</p>"
        )
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _tool_meta() -> Dict[str, Any]:
    """Return standard metadata used by the tool and resources."""
    return {
        "openai/outputTemplate": WIDGET.template_uri,
        "openai/toolInvocation/invoking": WIDGET.invoking,
        "openai/toolInvocation/invoked": WIDGET.invoked,
        "openai/widgetAccessible": True,
        "openai/resultCanProduceWidget": True,
        "annotations": {
            "destructiveHint": False,
            "openWorldHint": False,
            "readOnlyHint": True,
        },
    }


def _embedded_widget_resource() -> types.EmbeddedResource:
    """Create an embedded HTML resource so the host can render the widget inline."""
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
    """
    Fetch MongoDB documents with the original projection,
    then post-process/rename fields to clean keys for the widget:
    company, symbol, summary, impact, score, sentiment, dt, news_link.
    """
    # enforce 1..50, default 10
    if not isinstance(limit, int):
        limit = 10
    limit = max(1, min(50, limit))

    client = MongoClient(MONGO_URI)
    coll = client[DB_NAME][COLL_NAME]

    projection = {
        "_id": 0,
        "symbolmap.Company_Name": 1,
        "short summary": 1,
        "impact": 1,
        "impact score": 1,
        "sentiment": 1,
        "news link": 1,
        "dt_tm": 1,
        "symbolmap.NSE": 1,
    }

    cur = coll.find(query or {}, projection).sort("dt_tm", DESCENDING).limit(limit)

    cleaned: List[Dict[str, Any]] = []
    for d in cur:
        # Safely extract source fields (original schema)
        company = d.get("symbolmap.Company_Name", "")
        symbol = d.get("symbolmap.NSE", "")
        summary = d.get("short summary", "")
        impact = d.get("impact", "")
        score = d.get("impact score", "")
        sentiment = d.get("sentiment", "")
        dt = d.get("dt_tm", "")
        news_link = d.get("news link", "")

        # Build renamed structure for the widget/frontend
        cleaned.append(
            {
                "company": company,
                "symbol": symbol,
                "summary": summary,
                "impact": impact,
                "score": score,
                "sentiment": sentiment,
                "dt": dt,
                "news_link": news_link,
            }
        )

    return cleaned


# ===== MCP definitions =====
@mcp._mcp_server.list_tools()
async def _list_tools() -> List[types.Tool]:
    """
    Tool: news-impact

    Retrieves and renders recent news documents from MongoDB as a Skybridge HTML widget.
    Accepts a MongoDB-style filter (`query`) and a `limit` (1–50, defaults to 10).
    """
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
    """Return the static HTML widget resource metadata to the host."""
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
    """Return the resource template metadata for the widget."""
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
    """Serve the widget HTML when the host requests the template URI."""
    if str(req.params.uri) != WIDGET.template_uri:
        return types.ServerResult(
            types.ReadResourceResult(
                contents=[], _meta={"error": f"Unknown resource: {req.params.uri}"}
            )
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
    """
    Run the tool: validate args, fetch+rename docs, and return a widget-producing result.
    (No plain text content is emitted so the host renders the widget inline.)
    """
    args = req.params.arguments or {}

    # 'query' is required; 'limit' is optional (defaults to 10)
    if "query" not in args:
        return types.ServerResult(
            types.CallToolResult(
                content=[
                    types.TextContent(type="text", text="Field 'query' is required.")
                ],
                isError=True,
            )
        )

    query = args.get("query") or {}
    limit = args.get("limit", 10)

    # Validate allowed keys (keep strict to avoid unexpected filters)
    allowed_keys = {
        "sentiment",
        "symbolmap.NSE",
        "symbolmap.Company_Name",
        "impact score",
    }
    if not isinstance(query, dict) or any(k not in allowed_keys for k in query.keys()):
        return types.ServerResult(
            types.CallToolResult(
                content=[
                    types.TextContent(
                        type="text",
                        text=(
                            "Invalid query keys. Allowed: sentiment, symbolmap.NSE, "
                            "symbolmap.Company_Name, impact score."
                        ),
                    )
                ],
                isError=True,
            )
        )

    # Fetch and rename fields
    try:
        docs = _fetch_docs(query, int(limit))
    except Exception as e:
        return types.ServerResult(
            types.CallToolResult(
                content=[types.TextContent(type="text", text=f"Query error: {e}")],
                isError=True,
            )
        )

    # Embed the widget HTML so the client can render without separate fetch.
    widget_resource = _embedded_widget_resource()
    call_meta = {
        # IMPORTANT: put the widget embedding JSON here to render inline
        "openai.com/widget": widget_resource.model_dump(mode="json"),
        # Also include the template URI explicitly
        "openai/outputTemplate": WIDGET.template_uri,
        "openai/toolInvocation/invoking": WIDGET.invoking,
        "openai/toolInvocation/invoked": WIDGET.invoked,
        "openai/widgetAccessible": True,
        "openai/resultCanProduceWidget": True,
    }

    # Provide data under both 'docs' and 'items' for template compatibility.
    call_result = types.CallToolResult(
        content=[],  # no plain text; let the host render the widget
        structuredContent={"docs": docs, "items": docs},
        _meta=call_meta,
    )
    return types.ServerResult(call_result)


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
    """Run the FastMCP ASGI app with Uvicorn."""
    import uvicorn

    uvicorn.run("mcp_server:app", host="0.0.0.0", port=8000, reload=False)
