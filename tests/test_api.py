from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from ripweaver_catalogue.config import Settings
from ripweaver_catalogue.main import create_app
from ripweaver_catalogue.models import Base

ADMIN_TOKEN = "admin-token-that-is-distinct-and-at-least-32-characters"
CONTENT_HASH = "0123456789ABCDEF0123456789ABCDEF"


@pytest.fixture
def client() -> Iterator[TestClient]:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    settings = Settings(
        database_url=SecretStr("sqlite+pysqlite://"),
        admin_token=SecretStr(ADMIN_TOKEN),
        allowed_hosts=["testserver"],
    )
    with TestClient(create_app(settings=settings, engine=engine)) as test_client:
        yield test_client
    engine.dispose()


def proposal() -> dict[str, object]:
    return {
        "schema_version": 1,
        "content_hash": CONTENT_HASH.lower(),
        "media_type": "bluray",
        "release_name": "Synthetic Series Disc",
        "edition": None,
        "titles": [
            {
                "title_index": 1,
                "source_file": "00001.mpls",
                "segment_map": ["46", "47"],
                "duration_seconds": 1440,
                "size_bytes": 2_000_000_000,
                "classification": "episode",
                "series_name": "Synthetic Series",
                "season_number": 1,
                "episode_number": 1,
                "episode_title": "Synthetic Pilot",
                "movie_title": None,
                "movie_year": None,
            }
        ],
    }


def installation_headers(client: TestClient) -> dict[str, str]:
    registered = client.post("/v1/installations/register")
    assert registered.status_code == 201
    return {"Authorization": f"Bearer {registered.json()['access_token']}"}


def submission_headers(
    client: TestClient, *, key: str = "stable-idempotency-key-0001"
) -> dict[str, str]:
    return {
        **installation_headers(client),
        "Idempotency-Key": key,
        "X-RipWeaver-Version": "1.3.6-test",
    }


def lookup_headers(
    authentication: dict[str, str], *, key: str = "stable-lookup-key-0001"
) -> dict[str, str]:
    return {**authentication, "Idempotency-Key": key}


def admin_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {ADMIN_TOKEN}"}


def test_health_and_schema_are_public(client: TestClient) -> None:
    assert client.get("/health/live").json() == {"status": "live"}
    assert client.get("/health/ready").json() == {"status": "ready"}
    schema = client.get("/v1/schema").json()
    assert schema["schema_version"] == 3
    assert schema["public_lookup"] is False
    assert schema["metered_lookup"] is True
    assert schema["support_checkout"] is False
    assert schema["automatic_piecewise_consensus"] is True
    assert schema["provisional_help"] is True
    assert schema["independent_quorum"] == 2
    assert schema["human_moderation_required"] is False
    assert schema["attachments_accepted"] is False
    assert schema["media_accepted"] is False
    assert client.get("/openapi.json").status_code == 404
    policy = client.get("/v1/support/policy").json()
    assert policy["minimum_amount_cents"] == 1000
    assert policy["minimum_rate_cents"] == 1
    assert policy["maximum_rate_cents"] == 100
    assert policy["payments_enabled"] is False


def test_unknown_disc_is_not_found(client: TestClient) -> None:
    authentication = installation_headers(client)
    response = client.post(
        f"/v1/lookups/discs/{CONTENT_HASH}",
        json={"mode": "automatic"},
        headers=lookup_headers(authentication),
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "Disc is not catalogued"}


def test_submission_requires_authentication(client: TestClient) -> None:
    response = client.post(
        "/v1/submissions",
        json=proposal(),
        headers={
            "Idempotency-Key": "stable-idempotency-key-0001",
            "X-RipWeaver-Version": "1.3.6-test",
        },
    )
    assert response.status_code == 401


def test_submission_remains_private_until_approved(client: TestClient) -> None:
    authentication = installation_headers(client)
    response = client.post(
        "/v1/submissions",
        json=proposal(),
        headers={
            **authentication,
            "Idempotency-Key": "stable-idempotency-key-0001",
            "X-RipWeaver-Version": "1.3.6-test",
        },
    )
    assert response.status_code == 202
    receipt = response.json()
    assert receipt["content_hash"] == CONTENT_HASH
    assert receipt["status"] == "pending"
    assert (
        client.post(
            f"/v1/lookups/discs/{CONTENT_HASH}",
            json={"mode": "automatic"},
            headers=lookup_headers(authentication),
        ).status_code
        == 404
    )

    pending = client.get("/v1/admin/submissions", headers=admin_headers()).json()
    assert [item["submission_id"] for item in pending] == [receipt["submission_id"]]
    assert "payload_json" not in pending[0]

    approved = client.post(
        f"/v1/admin/submissions/{receipt['submission_id']}/approve",
        headers=admin_headers(),
    )
    assert approved.status_code == 200
    assert approved.json()["revision"] == 1
    assert approved.json()["status"] == "reviewed"

    lookup = client.post(
        f"/v1/lookups/discs/{CONTENT_HASH}",
        json={"mode": "automatic"},
        headers=lookup_headers(authentication, key="approved-lookup-key-0001"),
    )
    assert lookup.status_code == 200
    assert lookup.json()["disc"]["titles"][0]["episode_number"] == 1
    assert lookup.json()["credit_source"] == "monthly"
    usage = client.get("/v1/account/usage", headers=authentication).json()
    assert usage["monthly_used"] == 1
    assert usage["contribution_credits"] == 1


def test_idempotency_key_cannot_be_reused_for_different_payload(
    client: TestClient,
) -> None:
    headers = submission_headers(client)
    first = client.post("/v1/submissions", json=proposal(), headers=headers)
    assert first.status_code == 202
    changed = proposal()
    changed["release_name"] = "Different release"
    second = client.post("/v1/submissions", json=changed, headers=headers)
    assert second.status_code == 409


@pytest.mark.parametrize(
    "unsafe_source",
    [r"C:\\Media\\00001.mpls", "BDMV/PLAYLIST/00001.mpls", "../00001.mpls"],
)
def test_submission_rejects_paths(client: TestClient, unsafe_source: str) -> None:
    payload = proposal()
    payload["titles"][0]["source_file"] = unsafe_source  # type: ignore[index]
    response = client.post(
        "/v1/submissions",
        json=payload,
        headers=submission_headers(
            client, key=f"path-rejection-{len(unsafe_source):04d}"
        ),
    )
    assert response.status_code == 422


def test_submission_rejects_unknown_private_fields(client: TestClient) -> None:
    payload = proposal()
    payload["jellyfin_path"] = r"D:\\TV Shows\\Synthetic Series"
    response = client.post(
        "/v1/submissions",
        json=payload,
        headers=submission_headers(client, key="private-field-rejection-0001"),
    )
    assert response.status_code == 422


def test_admin_can_reject_with_path_free_reason(client: TestClient) -> None:
    created = client.post(
        "/v1/submissions",
        json=proposal(),
        headers=submission_headers(client, key="rejection-idempotency-key-0001"),
    ).json()
    rejected = client.post(
        f"/v1/admin/submissions/{created['submission_id']}/reject",
        json={"reason_code": "conflicting_playlist_map"},
        headers=admin_headers(),
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    authentication = installation_headers(client)
    assert (
        client.post(
            f"/v1/lookups/discs/{CONTENT_HASH}",
            json={"mode": "automatic"},
            headers=lookup_headers(authentication, key="rejected-lookup-key-0001"),
        ).status_code
        == 404
    )
