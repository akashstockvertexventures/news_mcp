# News Impact MCP Server

This MCP server exposes a tool `news_impact_html` that:
1) Queries MongoDB using your filters, and
2) Returns an inline-renderable HTML (Skybridge mime) that the OpenAI Apps SDK can display in ChatGPT.

## Env
- `MONGO_URI` (default: mongodb://localhost:27017)
- `MONGO_DB`  (default: newsdb)
- `MONGO_COLL` (default: news)
- `COMPONENT_HTML` (optional): path to the `index.html` component (defaults to ./components/news-impact/index.html)

## Install
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r server/requirements.txt
```

## Run
Apps SDK will spawn the server via stdio. For local debug you can run:
```bash
python server/mcp_server.py
```
(You'll need an Apps SDK client to call the tool.)

## Tool
- name: `news_impact_html`
- params: `sentiment`, `company_like`, `symbol`, `skip`, `limit`
- response: `text/html+skybridge` blob with the UI + embedded docs
