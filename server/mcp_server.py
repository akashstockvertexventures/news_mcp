# mcp_server.py
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, List

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
    "required": ["query"],
}


# ===== Helpers =====
def _load_widget_html() -> str:
    path = os.path.abspath(WIDGET.html_path)
    if not os.path.exists(path):
        return (
            "<!doctype html><meta charset='utf-8'><title>News Impact</title>"
            f"<p>index.html not found at: {path}</p>"
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
        "annotations": {
            "destructiveHint": False,
            "openWorldHint": False,
            "readOnlyHint": True,
        },
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
    """Fetch documents from MongoDB and ensure schema matches widget expectations."""
    if not isinstance(limit, int):
        limit = 10
    limit = max(1, min(50, limit))

    client = MongoClient(MONGO_URI)
    coll = client[DB_NAME][COLL_NAME]

    projection = {
        "_id": 0,
        "symbolmap.Company_Name": 1,
        "symbolmap.NSE": 1,
        "short summary": 1,
        "impact": 1,
        "impact score": 1,
        "sentiment": 1,
        "news link": 1,
        "dt_tm": 1,
    }

    cur = coll.find(query or {}, projection).sort("dt_tm", DESCENDING).limit(limit)
    docs = []

    for d in cur:
        # Fix nested fields so front-end can access symbolmap.Company_Name correctly
        if "symbolmap.Company_Name" in d:
            company_name = d.pop("symbolmap.Company_Name")
            d["symbolmap"] = {"Company_Name": company_name}

        # Ensure consistent key presence
        for key in [
            "short summary",
            "impact",
            "impact score",
            "sentiment",
            "news link",
        ]:
            d.setdefault(key, "")

        docs.append(d)

    return docs


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
            types.ReadResourceResult(
                contents=[],
                _meta={"error": f"Unknown resource: {req.params.uri}"},
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
                        text="Invalid query keys. Allowed: sentiment, symbolmap.NSE, symbolmap.Company_Name, impact score.",
                    )
                ],
                isError=True,
            )
        )

    try:
        docs = _fetch_docs(query, int(limit))
    except Exception as e:
        return types.ServerResult(
            types.CallToolResult(
                content=[types.TextContent(type="text", text=f"Query error: {e}")],
                isError=True,
            )
        )

    widget_resource = _embedded_widget_resource()
    meta = {
        "openai.com/widget": widget_resource.model_dump(mode="json"),
        **_tool_meta(),
    }

    return types.ServerResult(
        types.CallToolResult(
            content=[
                types.TextContent(type="text", text=f"Fetched {len(docs)} item(s) for News Impact.")
            ],
            structuredContent={"docs": docs, "items": docs},
            _meta=meta,
        )
    )


# Register handlers
mcp._mcp_server.request_handlers[types.CallToolRequest] = _call_tool_request
mcp._mcp_server.request_handlers[types.ReadResourceRequest] = _handle_read_resource

# ===== Expose ASGI app =====
app = mcp.streamable_http_app()

# ===== CORS for local testing =====
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
