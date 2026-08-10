# RipWeaver Catalogue

Privacy-safe public disc metadata for RipWeaver clients. Clients access this
service over HTTPS; PostgreSQL remains private and is never exposed through
Cloudflare Tunnel.

## Security boundary

The API accepts structural disc metadata only: a content identifier, media
type, source playlist/file identifiers, segment maps, runtimes, sizes, and
reviewed movie/episode classifications. Validation rejects local paths and the
schema has no fields for media, transcripts, screenshots, drive serials,
Jellyfin details, credentials, or command output.

Public lookups return only approved revisions. Submissions require a dedicated
submission token and remain pending until separately approved with the admin
token. Tokens and the database URL are loaded from `.env` and are never stored
in API records or responses.

## Local development

```powershell
Copy-Item .env.example .env
uv sync --group dev
uv run alembic upgrade head
uv run uvicorn ripweaver_catalogue.main:app --reload
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

Use a local PostgreSQL URL in `.env`. Tests use a temporary SQLite database and
synthetic metadata only.

## Docker deployment

1. Copy `.env.example` to `.env` and replace every placeholder independently.
2. Start PostgreSQL and apply migrations:

   ```bash
   docker compose up -d postgres
   docker compose run --rm migrate
   docker compose up -d api
   ```

3. Confirm `api` is healthy from inside the Docker network.
4. Add the dedicated Cloudflare tunnel token to `.env`.
5. Start `cloudflared`:

   ```bash
   docker compose up -d cloudflared
   ```

The remotely managed tunnel route should send `api.ripweaver.com` to
`http://api:8080`. Do not create a route to `postgres:5432` and do not add a
host port mapping to PostgreSQL.

Run a collision-refusing manual backup with:

```bash
docker compose --profile tools run --rm backup
```

Backups are written under ignored `./backups/`. Production backups should also
be copied, encrypted, to storage outside the VM and tested with a restore drill.

## Initial API

- `GET /health/live` — process health, no database access
- `GET /health/ready` — database readiness
- `GET /v1/schema` — protocol capabilities
- `GET /v1/discs/{content_hash}` — public approved lookup
- `POST /v1/submissions` — authenticated pending proposal
- `GET /v1/admin/submissions` — authenticated moderation list
- `POST /v1/admin/submissions/{id}/approve` — publish a revision
- `POST /v1/admin/submissions/{id}/reject` — reject with a path-free reason code

The API is intentionally narrow. Account registration, automated trust,
attachments, media upload, and direct database access are out of scope.

## Licensing

No project license has been selected yet. Add one deliberately before accepting
external contributions or publishing reusable source packages.
