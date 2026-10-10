"""Support Triage Agent - one process serving REST (for n8n) and MCP (at /mcp).

REST endpoints and MCP tools both call the SAME functions in tools/.
"""
import logging
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import anyio
import uvicorn
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field

load_dotenv(Path(__file__).resolve().parent / ".env")  # must run before tools read env vars

from tools.classify import classify_ticket as _classify  # noqa: E402
from tools.config import validate_configuration  # noqa: E402
from tools.errors import TriageError  # noqa: E402
from tools.rag_query import query_knowledge_base as _query_kb  # noqa: E402
from tools.security import RateLimiter  # noqa: E402
from tools.ticket_router import route_ticket as _route  # noqa: E402

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # httpx logs full URLs at INFO
logger = logging.getLogger("triage.server")

# ---------------------------------------------------------------- config
try:
    validate_configuration(os.environ)
except ValueError as exc:
    raise SystemExit(
        f"Invalid configuration: {exc}. Generate an API key with "
        '`python -c "import secrets; print(secrets.token_urlsafe(32))"`.'
    ) from None

API_KEY = os.environ["API_KEY"]
RATE_LIMIT = int(os.getenv("RATE_LIMIT_PER_MIN", "60"))
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]

Category = Literal["account_access", "billing", "technical_issue", "feature_request", "bug_report", "general_inquiry"]
Priority = Literal["low", "normal", "high", "urgent"]


# ---------------------------------------------------------------- request models
class ClassifyRequest(BaseModel):
    subject: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1, max_length=10000)


class RAGRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=3, ge=1, le=10)


class RouteRequest(BaseModel):
    subject: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1, max_length=10000)
    category: Category
    priority: Priority


# ---------------------------------------------------------------- security helpers
limiter = RateLimiter(RATE_LIMIT)


def rate_limit(request: Request, response: Response):
    client = request.client.host if request.client else "unknown"
    ok, remaining, retry_after = limiter.check(client)
    if not ok:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded",
            headers={"Retry-After": str(retry_after), "X-RateLimit-Limit": str(RATE_LIMIT), "X-RateLimit-Remaining": "0"},
        )
    response.headers["X-RateLimit-Limit"] = str(RATE_LIMIT)
    response.headers["X-RateLimit-Remaining"] = str(remaining)


def require_api_key(request: Request):
    supplied = request.headers.get("x-api-key", "")
    if not secrets.compare_digest(supplied.encode(), API_KEY.encode()):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


protected = [Depends(rate_limit), Depends(require_api_key)]  # rate limit first so brute force is throttled


class MCPApiKeyMiddleware:
    """Pure-ASGI API-key check for the /mcp mount (works with streaming responses)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith("/mcp"):
            if scope["method"] != "OPTIONS":
                client = scope.get("client")
                client_ip = client[0] if client else "unknown"
                ok, remaining, retry_after = limiter.check(client_ip)
                if not ok:
                    resp = JSONResponse(
                        {"detail": "Rate limit exceeded"},
                        status_code=429,
                        headers={
                            "Retry-After": str(retry_after),
                            "X-RateLimit-Limit": str(RATE_LIMIT),
                            "X-RateLimit-Remaining": "0",
                        },
                    )
                    await resp(scope, receive, send)
                    return
                headers = dict(scope["headers"])
                supplied = headers.get(b"x-api-key", b"")
                if not secrets.compare_digest(supplied, API_KEY.encode()):
                    resp = JSONResponse({"detail": "Invalid or missing API key"}, status_code=401)
                    await resp(scope, receive, send)
                    return
        await self.app(scope, receive, send)


# ---------------------------------------------------------------- MCP (same tool functions)
mcp = MCPServer("support-triage")


async def _run_tool(fn, *args):
    try:
        return await anyio.to_thread.run_sync(fn, *args)
    except TriageError as e:
        raise RuntimeError(e.public_message)
    except Exception:
        logger.exception("Unexpected MCP tool error")
        raise RuntimeError("Internal error")


@mcp.tool()
async def classify_ticket(subject: str, body: str) -> dict:
    """Classify a support ticket into a category and priority."""
    if not 1 <= len(subject) <= 500 or not 1 <= len(body) <= 10000:
        raise RuntimeError("subject must be 1-500 chars and body 1-10000 chars")
    return await _run_tool(_classify, subject, body)


@mcp.tool()
async def query_knowledge_base(query: str, top_k: int = 3) -> dict:
    """Search the support knowledge base. top_k must be 1-10."""
    return await _run_tool(_query_kb, query, top_k)


@mcp.tool()
async def route_ticket(subject: str, body: str, category: Category, priority: Priority) -> dict:
    """Store a classified ticket, pick the support team, and decide whether to alert."""
    return await _run_tool(_route, subject, body, category, priority)


mcp_app = mcp.streamable_http_app()  # serves /mcp


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with mcp.session_manager.run():
        yield


# ---------------------------------------------------------------- FastAPI app
app = FastAPI(title="Support Triage Agent", lifespan=lifespan)

if CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_methods=["GET", "POST"],
        allow_headers=["X-API-Key", "Content-Type"],
    )
app.add_middleware(MCPApiKeyMiddleware)


@app.exception_handler(TriageError)
async def triage_error_handler(request: Request, exc: TriageError):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.public_message})


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    # Return only field + message (default handler would echo the submitted input back)
    errors = [{"field": ".".join(str(x) for x in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": errors})


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception):
    logger.exception("Unhandled error on %s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/classify_ticket", dependencies=protected)
def classify_endpoint(req: ClassifyRequest):
    logger.info("classify_ticket subject_len=%d body_len=%d", len(req.subject), len(req.body))
    return _classify(req.subject, req.body)


@app.post("/query_knowledge_base", dependencies=protected)
def query_endpoint(req: RAGRequest):
    logger.info("query_knowledge_base query_len=%d top_k=%d", len(req.query), req.top_k)
    return _query_kb(req.query, req.top_k)


@app.post("/route_ticket", dependencies=protected)
def route_endpoint(req: RouteRequest):
    logger.info("route_ticket category=%s priority=%s", req.category, req.priority)
    return _route(req.subject, req.body, req.category, req.priority)


app.mount("/", mcp_app)  # keep LAST so REST routes win; exposes /mcp


if __name__ == "__main__":
    uvicorn.run(app, host=os.getenv("HOST", "0.0.0.0"), port=int(os.getenv("PORT", "8000")))