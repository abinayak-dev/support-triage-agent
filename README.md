# AI Support Ticket Triage Agent

A Python service for classifying support tickets, retrieving relevant knowledge-base content, and routing tickets with an alert decision. One FastAPI process exposes the REST API used by n8n and the Streamable HTTP MCP server at `/mcp`; both call the same tool functions.

## Architecture

```mermaid
flowchart LR
    Ticket[Incoming ticket] --> N8N[n8n workflow]
    N8N -->|X-API-Key over HTTPS| API[FastAPI REST + MCP]
    API --> CLASSIFY[Classifier]
    CLASSIFY --> LLM[Ollama locally or hosted OpenAI-compatible chat]
    API --> RAG[Knowledge-base retrieval]
    RAG --> EMBED[Ollama locally or hosted OpenAI-compatible embeddings]
    RAG --> QDRANT[Qdrant]
    API --> ROUTE[Ticket routing]
    ROUTE --> PG[PostgreSQL]
    N8N --> DECISION[Alert decision and final result]
    FRONTEND[Optional public frontend] -->|Server-side proxy only| API
```

The repository does not contain frontend source or an exported n8n workflow. `docker-compose.yml` provides persistent local PostgreSQL and Qdrant services plus an opt-in API container profile. The existing n8n workflow is configured in the n8n instance; its HTTP Request nodes can use the configurable API base URL described below.

## Component and deployment audit

| Component | Current implementation and deployment requirement |
| --- | --- |
| FastAPI REST and MCP | `mcp-server/server.py` exposes `/health`, `/classify_ticket`, `/query_knowledge_base`, `/route_ticket`, and mounts MCP at `/mcp`. It is an ordinary long-running ASGI service; deploy it as a container or managed Python service. |
| Classification | `tools/classify.py` applies deterministic rules around the model result. Local default is Ollama `tinyllama` at `OLLAMA_HOST`; production can select an OpenAI-compatible chat endpoint via environment variables. |
| RAG embeddings | `tools/providers.py` uses local Ollama `nomic-embed-text` by default or a configured OpenAI-compatible embeddings endpoint. `rag-ingest/ingest.py` uses the same adapter as live queries. |
| Qdrant | The query and ingestion code use `QDRANT_HOST`, optional `QDRANT_API_KEY`, and `QDRANT_COLLECTION`. Production needs a reachable managed Qdrant or a separately operated Qdrant service. |
| PostgreSQL | `tools/ticket_router.py` persists routed tickets using required `POSTGRES_URL`. Production can use Supabase PostgreSQL or another PostgreSQL provider. The table definition is in `mcp-server/tickets.sql`. |
| Docker | Docker is not required by the Python code itself. The root Dockerfile packages the API. Compose manages only this project's PostgreSQL and Qdrant services by default; the API container is opt-in, and n8n is not managed or recreated by Compose. |
| n8n | n8n calls the REST endpoints server-to-server with `X-API-Key`. Its API credential belongs in n8n's credential store, not in browser code or a workflow export. |
| Persistent process/local filesystem | The FastAPI/MCP server needs a running process. The knowledge base is read from `rag-ingest/knowledge_base` during ingestion; deployed query instances use vectors in Qdrant and do not need the source files at runtime. |

### Provider and hosting decisions

- The current combined FastAPI/MCP entry point can remain. A separate REST-only entry point is not required on a container host.
- Keep MCP on the same service for internal/authorized clients; use an ingress rule to restrict `/mcp` if public demo users should only access REST.
- Vercel is appropriate for an optional frontend. Do not run this current Python/MCP service, Ollama, Qdrant, or PostgreSQL as Vercel serverless functions. The local Ollama process is not reachable from Vercel.
- A public deployment needs hosted chat and embedding endpoints, unless the backend container can securely reach an independently hosted Ollama server. The service does not silently substitute a local model in production: select and configure a provider explicitly.
- Use hosted PostgreSQL and Qdrant for a multi-user demo. Qdrant and the embedding model used to ingest vectors must match the live embedding model's vector dimensions.
- Keep `LLM_API_KEY`, `EMBEDDING_API_KEY`, `POSTGRES_URL`, `QDRANT_API_KEY`, and `API_KEY` in deployment secrets or server-side n8n credentials. Never place them in frontend variables exposed to the browser.

## Local setup

Requirements: Python 3.11, Ollama, and local PostgreSQL/Qdrant services. n8n is optional if only testing the REST API.

1. Create an environment file from the safe template. Set a fresh `API_KEY`, a new URL-safe `POSTGRES_PASSWORD`, and a `POSTGRES_URL` with the matching password. Keep `APP_ENV=development` for local runs.

   ```powershell
   if (-not (Test-Path mcp-server\.env)) { Copy-Item .env.example mcp-server\.env }
   ```

2. Install the API dependencies and run the service:

   ```powershell
   Set-Location mcp-server
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install -r requirements.txt
   python server.py
   ```

   The server listens on `0.0.0.0:8000` by default. Ollama defaults to `http://localhost:11434`; pull `tinyllama` and `nomic-embed-text` before using local classification and RAG.

3. Ensure the `tickets` table exists. For the local Compose database, run from the repository root:

   ```powershell
   Get-Content mcp-server\tickets.sql | docker exec -i support-triage-postgres-local psql -U triage -d tickets
   ```

   This schema uses `CREATE TABLE IF NOT EXISTS`. For another database, apply `mcp-server/tickets.sql` with its provider console or a `psql` session configured from a secure connection string.

4. Ingest the knowledge base:

   ```powershell
   Set-Location ..\rag-ingest
   python -m pip install -r requirements.txt
   python ingest.py
   ```

The default local Qdrant URL is `http://localhost:6333`. Ingestion is safe to rerun: point IDs are deterministic and stale points are removed within the configured collection.

## Running tests

Run the backend tests from the backend directory so the existing `tools` package imports resolve:

```powershell
Set-Location mcp-server
python -m pip install -r requirements-dev.txt
python -m pytest -v
```

## Docker setup

### Persistent local services with Compose

Create `mcp-server/.env` from `.env.example` if needed, then set a fresh `POSTGRES_PASSWORD` and a matching `POSTGRES_URL`. Keep this file private; it is ignored by Git and excluded from the API image.

Start PostgreSQL and Qdrant:

```powershell
docker compose --env-file mcp-server\.env up -d postgres qdrant
```

The database is published only on `localhost:15432`, and Qdrant only on `localhost:6333`; both use named volumes. The API waits for PostgreSQL and Qdrant health checks. Qdrant's health check verifies its service port is accepting connections. The existing Ollama and n8n services are not managed by this Compose file.

To build and start the API container as well:

```powershell
docker compose --env-file mcp-server\.env --profile api up -d --build api
```

The API container connects to PostgreSQL and Qdrant over the Compose network and to host Ollama at `host.docker.internal:11434`. Its port 8000 is published for host and n8n access. `docker compose down` stops these project services but preserves their named volumes; do not add `-v` unless intentionally deleting local database and vector data.

### Manual API container

Build the API image from the repository root:

```powershell
docker build -t support-triage-agent .
```

For a container connecting to services running on the Windows host, configure the copied `mcp-server\.env` with host-reachable addresses (for example `http://host.docker.internal:11434`, `http://host.docker.internal:6333`, and PostgreSQL at `host.docker.internal:15432`). Run it with:

```powershell
docker run --rm --name support-triage-api -p 8000:8000 --env-file mcp-server\.env support-triage-agent
```

The API image does not bundle Ollama, Qdrant, PostgreSQL, or n8n. Do not publish database, Qdrant, Ollama, or n8n administrative ports to the public internet. The health check is `GET /health`.

## Environment variables

Copy `.env.example` to `mcp-server/.env` for local API and ingestion runs. In production, configure these directly as deployment secrets/environment variables instead of copying a local `.env`.

| Variable | Default / required | Purpose |
| --- | --- | --- |
| `API_KEY` | Required | REST and MCP authentication using `X-API-Key`. Must be a unique high-entropy value. |
| `APP_ENV` | `development` | Use `production` on a public deployment; startup validation then requires TLS-enabled managed services and hosted model endpoints. |
| `HOST`, `PORT` | `0.0.0.0`, `8000` | Bind address and port; cloud platforms commonly set `PORT`. |
| `LOG_LEVEL` | `INFO` | Application log level. |
| `RATE_LIMIT_PER_MIN` | `60` | Per-process request limit for protected REST endpoints. |
| `CORS_ORIGINS` | Empty | Comma-separated frontend origins. CORS is enabled only for explicitly listed origins. It is not a substitute for API authentication. |
| `POSTGRES_URL` | Required for routing | PostgreSQL connection string. Use the provider's TLS/SSL-enabled URL in production. |
| `POSTGRES_PASSWORD` | Local Compose only | Password for the local Compose PostgreSQL role. Use a URL-safe random value and keep it only in `mcp-server/.env`; production credentials belong in the hosting secret manager. |
| `LLM_PROVIDER` | `ollama` | Chat provider: `ollama` or `openai_compatible`. |
| `OLLAMA_HOST` | `http://localhost:11434` | Local Ollama host, used by local chat and embedding adapters. |
| `CLASSIFY_MODEL` | `tinyllama` | Ollama chat model used when `LLM_MODEL` is not set. |
| `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | Required for `openai_compatible` | Chat endpoint base (usually ends with `/v1`), server-side bearer key, and provider model name. |
| `EMBEDDING_PROVIDER` | `ollama` | Embedding provider: `ollama` or `openai_compatible`. |
| `EMBED_MODEL` | `nomic-embed-text` | Model identifier sent to the configured embedding provider. |
| `EMBEDDING_BASE_URL`, `EMBEDDING_API_KEY` | Required for hosted embeddings | OpenAI-compatible embedding base (usually ends with `/v1`) and server-side bearer key. |
| `QDRANT_HOST` | `http://localhost:6333` | Qdrant URL; use the cloud cluster URL in production. |
| `QDRANT_API_KEY` | Optional locally | Qdrant Cloud API key. |
| `QDRANT_COLLECTION` | `knowledge_base` | Vector collection used by ingestion and queries. |
| `CHUNK_SIZE`, `CHUNK_OVERLAP` | `800`, `100` | Knowledge-base chunking settings for ingestion. |
| `TRIAGE_API_BASE_URL` | `http://host.docker.internal:8000` | Base URL for n8n HTTP Request nodes; set it to the deployed HTTPS API URL in production. Store the API key separately in n8n credentials. |

When changing the embedding model, ingest into a new `QDRANT_COLLECTION` and switch the API to it only after verifying the migration. Ingestion refuses a vector-dimension mismatch and never deletes or recreates a collection. Keep the old collection until the migration is verified and any cleanup is explicitly approved.

## API endpoints

Except for `GET /health`, REST endpoints require `X-API-Key`. All JSON requests use `Content-Type: application/json`.

| Method and path | Request JSON | Success response |
| --- | --- | --- |
| `GET /health` | None | `{"status":"ok"}` |
| `POST /classify_ticket` | `{"subject":"...","body":"..."}` | `{"category":"...","priority":"...","reasoning":"..."}` |
| `POST /query_knowledge_base` | `{"query":"...","top_k":3}` (`top_k` optional, 1–10) | `{"results":[{"text":"...","source":"...","score":0.0}]}` |
| `POST /route_ticket` | `{"subject":"...","body":"...","category":"...","priority":"..."}` | `{"ticket_id":1,"team":"...","priority":"...","should_alert":false}` |

`/mcp` serves the same three tool functions over Streamable HTTP and also requires `X-API-Key`. Never treat routing as successful if PostgreSQL is unavailable: it now returns a service error rather than a synthetic, non-persistent ticket ID.

Example PowerShell request (run from any directory after setting `$apiKey` securely in the current session):

```powershell
$headers = @{ 'X-API-Key' = $apiKey; 'Content-Type' = 'application/json' }
Invoke-RestMethod -Uri 'http://localhost:8000/classify_ticket' -Method Post -Headers $headers -Body '{"subject":"Password reset issue","body":"I cannot reset my password"}'
```

## n8n workflow

The existing workflow pattern remains:

```text
Incoming ticket
  -> Classify Ticket
  -> Knowledge Base
  -> Route Ticket
  -> Alert required?
       -> alert payload
       -> no alert
  -> final result
```

For each HTTP Request node, use `{{$env.TRIAGE_API_BASE_URL}}` as the base and append the appropriate path. Configure the n8n process with:

- Local API running on the host: `TRIAGE_API_BASE_URL=http://host.docker.internal:8000`
- Deployed API: `TRIAGE_API_BASE_URL=https://<your-api-domain>`

Configure one reusable n8n **Header Auth** credential with header name `X-API-Key` and the API key value. Attach it to each API request node; do not put the key in node JSON, workflow exports, webhook input, or browser code.

Request bodies:

- Classify: `subject`, `body`
- Knowledge base: `query` (typically subject plus body), optional `top_k`
- Route: original `subject` and `body`, plus `category` and `priority` from the classify response

Branch on the route response's `should_alert`. The repository does not include an export of the existing n8n workflow, so no UI-managed workflow was overwritten or claimed to have been updated.

## Production architecture and deployment

Recommended public demo:

```text
Vercel frontend (optional)
  -> server-side Next.js/API proxy (holds API_KEY; never browser JavaScript)
  -> container-hosted FastAPI + MCP (Render, Railway, Fly.io, Cloud Run, etc.)
       -> hosted OpenAI-compatible chat endpoint
       -> hosted OpenAI-compatible embedding endpoint
       -> Qdrant Cloud
       -> Supabase PostgreSQL
n8n (self-hosted or hosted) -> same HTTPS API using server-side Header Auth credential
```

For a concrete container-host target, use a Render Docker Web Service with managed PostgreSQL and Qdrant Cloud. These are independently provisioned services; check current pricing and region/network requirements before creating paid resources. The backend can also run on another Docker-compatible host with equivalent settings. The exact required production variables are `APP_ENV`, `API_KEY`, `POSTGRES_URL`, `QDRANT_HOST`, `QDRANT_API_KEY`, `LLM_PROVIDER`, `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `EMBEDDING_PROVIDER`, `EMBEDDING_BASE_URL`, `EMBEDDING_API_KEY`, and `EMBED_MODEL`. A placeholder-only template is provided in [`render.env.example`](./render.env.example). Production startup rejects localhost dependencies, non-TLS database/Qdrant/provider URLs, missing credentials, and short API keys. PostgreSQL must use `sslmode=require` or stronger; prefer `verify-full` when supported by the provider. Configure `CORS_ORIGINS` to only the frontend origin if browser requests are needed; preferably keep the API key in the server-side frontend proxy and do not call the protected API directly from browser JavaScript.

### Render deployment checklist

1. Create a Docker Web Service from the repository root using the root `Dockerfile`. The process binds to `0.0.0.0` and honors Render's `PORT`; the Docker health check also follows that port and probes `GET /health`. Do not override Render's assigned `PORT` with a conflicting value.
2. Provision a managed PostgreSQL database reachable from the web service. Before applying the schema, confirm this is the intended new/empty production database and take a backup if it already contains data. Apply [`mcp-server/tickets.sql`](./mcp-server/tickets.sql) once using the provider's SQL console or a trusted `psql` client configured through a secure libpq service/password file or protected environment. From the repository root, run `psql -v ON_ERROR_STOP=1 -f mcp-server/tickets.sql`; do not put a credential-bearing connection URL directly on the command line. The script is idempotent (`CREATE TABLE IF NOT EXISTS`) and creates only the `tickets` table; it does not migrate an existing table's columns. Verify the table exists before testing ticket routing.
3. Provision Qdrant Cloud and an OpenAI-compatible chat endpoint and embedding endpoint, reachable from Render over HTTPS. Render cannot access the developer workstation's Ollama at `localhost` or `host.docker.internal`; production configuration requires `openai_compatible` for both chat and embedding providers.
4. Add the variables from [`render.env.example`](./render.env.example) to Render's Environment settings, replacing every placeholder with real values there. Do not upload `.env`, commit credentials, or include secrets in a frontend bundle. `HOST` is optional (defaults to `0.0.0.0`), `PORT` is supplied by Render, and other optional settings include `RATE_LIMIT_PER_MIN` and `CORS_ORIGINS`.
5. Ensure `POSTGRES_URL` uses `sslmode=require` or stronger, and that Qdrant, chat, and embedding endpoints use HTTPS. The startup validator enforces these conditions and rejects Ollama providers in production rather than silently falling back to local defaults.
6. Match `EMBED_MODEL` to the embedding model used for ingestion. Confirm the model returns vectors matching the selected Qdrant collection's configured dimension. If migrating from the current local 768-dimensional collection to a different embedding dimension, ingest into a separate collection and switch only after verification; do not delete or recreate the existing collection.
7. Configure Render's health check path as `/health`. It confirms process liveness only; after startup, make authenticated requests to verify provider/Qdrant retrieval and database ticket routing separately.
8. Configure n8n to use the Render HTTPS URL and its Header Auth credential for `X-API-Key`. Keep one API worker unless the in-memory rate limiter is replaced with a shared limiter.

If using Vercel, implement a server-side route that forwards requests and attaches `X-API-Key` from a Vercel server-only environment variable. Do not prefix this key with `NEXT_PUBLIC_` or otherwise return it to the browser.

This repository does not contain a frontend or a provider account, and no public deployment has been performed. A hosted LLM is optional in the code but required for a cloud deployment that cannot reach the developer's local Ollama. The `openai_compatible` adapter also supports compatible self-hosted endpoints; it does not require a specific paid provider.

## Security and known limitations

- `.env` and nested `.env` files are ignored by Git and Docker; `.env.example` contains placeholders only. Keep real values in secret stores and rotate any credential that has been exposed.
- A PostgreSQL password was exposed outside the repository and must be treated as compromised. No credential has been rotated here. After confirming a current backup and maintenance window, change the PostgreSQL role password using the database provider's credential-rotation flow, update `POSTGRES_URL` in the host secret manager and any local Compose environment that uses that role, then restart/redeploy the API and verify routing. For local Compose, an operator can connect with `docker exec -it support-triage-postgres-local psql -U triage -d tickets` and use PostgreSQL's interactive `\password triage` command; then update both local password settings and recreate only the API service. Updating `POSTGRES_PASSWORD` alone does not change a role in an already-initialized volume. Do not alter or recreate volumes as part of rotation.
- n8n API keys must be stored as n8n credentials, not workflow literals.
- The REST rate limiter is in-memory and per process. Use one API worker for demo use; for horizontal scaling, move rate limiting to a shared service at the gateway or application layer.
- The MCP endpoint and REST endpoints share the same API key and per-process rate limiter. Use network/path access controls if MCP should not be public.
- `/health` is a liveness check, not a dependency-readiness check. Database, Qdrant, and model-provider failures are returned by the endpoint that needs them.
- Ingestion supports `.txt` and `.md` files. It removes stale points only in the selected collection.
- Hosted provider endpoints must implement the OpenAI-compatible chat-completions and/or embeddings API shapes used by the adapters.
- Vercel is not the runtime for this full Python/MCP/Ollama stack; use a container host for the backend and services.
