# RipWeaver Catalogue

Privacy-safe public disc metadata for RipWeaver clients. Clients access this
service over HTTPS; PostgreSQL remains private and is never exposed through
Cloudflare Tunnel.

## Security boundary

The API accepts structural disc metadata only: a content identifier, media
type, source playlist/file identifiers, segment maps, runtimes, sizes, match
provenance, and movie/episode classifications. The submission boundary accepts
only bounded, uncompressed JSON; it rejects duplicate keys, excessive size or
nesting, unknown fields, control characters, local paths, and invalid structural
relationships. The schema has no fields for media, transcripts, screenshots,
drive serials, Jellyfin details, credentials, or command output.

Lookups return only legacy approved revisions or title-level results confirmed
before the quarantine boundary. Each RipWeaver installation receives an opaque
bearer token; only its SHA-256 digest is stored by the server. Every new public
submission is stored in the separate `submission_quarantine` table with status
`pending`. A database constraint permits only `pending` or `rejected`, and the
table has no relationship or code path to consensus assertions, reviewed
revisions, contribution credits, or public lookup results. Provider secrets and
the database URL are loaded from `.env` and are never stored in API records or
responses.

The public protocol advertises schema version 4,
`submissions_quarantined: true`, and
`quarantine_publication_enabled: false`. Quarantine is deliberately a holding
boundary, not a moderation queue: there is a reject action but no approve or
publish action.

## Hybrid consensus

The historical consensus engine is piecewise rather than all-or-nothing. It is
retained so already-confirmed records remain readable and its behavior remains
covered by isolated tests. Public submissions no longer feed it. Each title
index first needs
agreement on its structural identity (playlist, segment map, runtime, and size),
then on its semantic assignment. A winner requires at least two independent
installations and must strictly lead the runner-up. One matching upload is only
a candidate; 1-1 and 2-2 ties are disputed; 2-1 and 3-2 majorities are confirmed.
This lets RipWeaver use 95% of a disc safely while continuing local matching for
one disputed bonus item.

After title voting, a whole-disc consistency pass checks relationships such as
duplicate episode assignments and play-all references. Only affected titles are
held. Cosmetic title differences do not split a semantic vote. When matching
claims use different display wording, provenance chooses the label in this
order: manual playback, deterministic mapping, strong local evidence, Gemini,
then server-assisted evidence.

`GET /v1/help/discs/{content_hash}` exposes path-free candidate evidence when a
desktop matcher is confused. It is free and does not make a candidate automatic.
Any result derived from that endpoint must be contributed as `server_assisted`,
which cannot form quorum or earn credit. This prevents the catalogue from
confirming its own suggestion. If even one title in a disc layout used server
help, that layout receives no exchange credit.

## Lookup credits and voluntary support

Each installation receives 10 automatic successful catalogue lookups per UTC
calendar month. Previously issued contribution credits remain spendable, but
quarantined submissions do not earn new credits. Failed and not-found lookups
do not consume anything. After the
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
- `POST /v1/lookups/discs/{content_hash}` — confirmed automatic/manual lookup
- `GET /v1/help/discs/{content_hash}` — unmetered provisional candidate help
- `POST /v1/support/checkout` — disabled-until-configured hosted checkout
- `POST /v1/payments/stripe/webhook` — signature-verified fulfillment boundary
- `POST /v1/submissions` — strict authenticated pending-only quarantine ingest
- `GET /v1/admin/quarantine` — private path-free quarantine summary
- `POST /v1/admin/quarantine/{id}/reject` — reject; there is no approve action
- `GET /v1/admin/submissions` — legacy schema-v1 moderation list
- `POST /v1/admin/submissions/{id}/approve` — publish a legacy revision
- `POST /v1/admin/submissions/{id}/reject` — reject a legacy proposal

The API is intentionally narrow. It uses pseudonymous installation identity,
not personal profiles or email accounts. Attachments, media upload, personal
profiles, and direct database access are out of scope. Reinstalling without
preserving the local token creates a new identity; this is acceptable because
manual lookups remain available and the support prompt is not a hard paywall.

## Licensing

Distributed under the MIT License. See `LICENSE` for details.
