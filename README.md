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

Lookups return only approved revisions. Each RipWeaver installation receives an
opaque bearer token; only its SHA-256 digest is stored by the server. Submissions
remain pending until separately approved with the admin token. Provider secrets
and the database URL are loaded from `.env` and are never stored in API records
or responses.

## Lookup credits and voluntary support

Each installation receives 10 automatic successful catalogue lookups per UTC
calendar month. An approved disc contribution earns one non-expiring automatic
lookup credit. Failed and not-found lookups do not consume anything. After the
monthly allowance and earned or purchased credits are exhausted, the API
returns a visible support-required decision; the person may still continue that
specific lookup manually without paying.

Support checkout is disabled by default. When it is eventually enabled, the
minimum payment is $10. The user chooses a support rate from $0.01 through $1.00
per automatic lookup; credits are the whole-number result of dividing the
payment by that rate. Purchased credits do not expire while the service exists.
Monthly allowance is consumed first, then contributed credits, then purchased
credits.

The support terms are returned by `GET /v1/support/policy` and must be displayed
and affirmatively accepted before checkout. They explain that RipWeaver is a
best-effort service with no guarantee of future maintenance, uptime, data
preservation, or continued availability; payments are generally final after
credits are issued, subject to applicable law and payment-network rules.

Stripe Checkout is hosted by Stripe. The catalogue server never accepts card
details and never grants credits from a browser redirect. Credits are appended
to the ledger only after an amount-checked, signature-verified, idempotently
processed webhook. Keep Stripe disabled until the account, live webhook, terms,
and deployment have been reviewed.

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

Leave `CATALOGUE_SUPPORT_PAYMENTS_ENABLED=false` until Stripe is configured.
Later, store the live Stripe secret and webhook secret only in the ignored
deployment `.env`, route Stripe to
`https://api.ripweaver.com/v1/payments/stripe/webhook`, and then enable the
feature. Never commit either secret.

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
- `POST /v1/installations/register` — issue a new installation token once
- `GET /v1/support/policy` — public prices, limits, and best-effort disclosures
- `GET /v1/account/usage` — authenticated allowance and credit balance
- `POST /v1/lookups/discs/{content_hash}` — authenticated automatic/manual lookup
- `POST /v1/support/checkout` — disabled-until-configured hosted checkout
- `POST /v1/payments/stripe/webhook` — signature-verified fulfillment boundary
- `POST /v1/submissions` — authenticated pending proposal
- `GET /v1/admin/submissions` — authenticated moderation list
- `POST /v1/admin/submissions/{id}/approve` — publish a revision
- `POST /v1/admin/submissions/{id}/reject` — reject with a path-free reason code

The API is intentionally narrow. It uses pseudonymous installation identity,
not personal profiles or email accounts. Automated trust, attachments, media
upload, and direct database access are out of scope. Reinstalling without
preserving the local token creates a new identity; this is acceptable because
manual lookups remain available and the support prompt is not a hard paywall.

## Licensing

Distributed under the MIT License. See `LICENSE` for details.
