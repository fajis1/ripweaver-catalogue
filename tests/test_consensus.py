from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from ripweaver_catalogue.config import Settings
from ripweaver_catalogue.main import create_app
from ripweaver_catalogue.models import Base
from ripweaver_catalogue.repository import record_trusted_legacy_proposal
from ripweaver_catalogue.schemas import DiscSubmissionInput, SubmissionReceipt

CONTENT_HASH = "ABCDEF0123456789ABCDEF0123456789"


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def client(engine: Engine) -> Iterator[TestClient]:
    settings = Settings(
        database_url=SecretStr("sqlite+pysqlite://"),
        allowed_hosts=["testserver"],
    )
    with TestClient(create_app(settings=settings, engine=engine)) as test_client:
        yield test_client


@dataclass(frozen=True)
class InstallationAuth:
    installation_id: str
    headers: dict[str, str]


def register(client: TestClient) -> InstallationAuth:
    response = client.post("/v1/installations/register")
    assert response.status_code == 201
    receipt = response.json()
    return InstallationAuth(
        installation_id=receipt["installation_id"],
        headers={"Authorization": f"Bearer {receipt['access_token']}"},
    )


def episode(
    title_index: int,
    episode_number: int,
    *,
    match_source: str = "deterministic",
) -> dict[str, object]:
    return {
        "title_index": title_index,
        "source_file": f"{title_index:05d}.mpls",
        "segment_map": [f"{title_index:05d}"],
        "duration_seconds": 1_400 + title_index,
        "size_bytes": 2_000_000_000 + title_index,
        "classification": "episode",
        "series_name": "Synthetic Series",
        "season_number": 1,
        "episode_number": episode_number,
        "episode_title": f"Episode {episode_number}",
        "movie_title": None,
        "movie_year": None,
        "display_title": None,
        "contained_title_indexes": [],
        "match_source": match_source,
    }


def proposal(
    titles: list[dict[str, object]], *, content_hash: str = CONTENT_HASH
) -> dict[str, object]:
    return {
        "schema_version": 2,
        "content_hash": content_hash,
        "media_type": "bluray",
        "release_name": "Synthetic Series Disc",
        "edition": None,
        "titles": titles,
    }


def submit(
    engine: Engine,
    authentication: InstallationAuth,
    payload: dict[str, object],
    *,
    key: str,
) -> SubmissionReceipt:
    with Session(engine) as session:
        return record_trusted_legacy_proposal(
            session,
            DiscSubmissionInput.model_validate(payload),
            trusted_internal=True,
            installation_id=authentication.installation_id,
            idempotency_key=key,
            client_version="legacy-consensus-test",
        )


def lookup(client: TestClient, authentication: InstallationAuth, *, key: str):
    return client.post(
        f"/v1/lookups/discs/{CONTENT_HASH}",
        json={"mode": "automatic"},
        headers={**authentication.headers, "Idempotency-Key": key},
    )


def test_one_upload_is_help_only_and_two_matching_uploads_confirm(
    client: TestClient, engine: Engine
) -> None:
    first_user = register(client)
    first = submit(
        engine,
        first_user,
        proposal([episode(1, 1)]),
        key="consensus-first-upload-0001",
    )
    assert first.status == "accepted"
    assert first.consensus is not None
    assert first.consensus.items[0].state.value == "candidate"
    candidate_lookup = lookup(client, first_user, key="candidate-lookup-key-0001")
    assert candidate_lookup.status_code == 404

    help_response = client.get(
        f"/v1/help/discs/{CONTENT_HASH}", headers=first_user.headers
    )
    assert help_response.status_code == 200
    candidate = help_response.json()["items"][0]["candidates"][0]
    assert candidate["independent_support"] == 1
    assert candidate["total_observations"] == 1

    second_user = register(client)
    second = submit(
        engine,
        second_user,
        proposal([episode(1, 1)]),
        key="consensus-second-upload-0002",
    )
    assert second.consensus is not None
    assert second.consensus.complete is True
    confirmed = lookup(client, register(client), key="confirmed-lookup-key-0002")
    assert confirmed.status_code == 200
    assert confirmed.json()["disc"]["status"] == "consensus"
    assert confirmed.json()["disc"]["titles"][0]["episode_number"] == 1
    assert (
        client.get("/v1/account/usage", headers=first_user.headers).json()[
            "contribution_credits"
        ]
        == 1
    )
    assert (
        client.get("/v1/account/usage", headers=second_user.headers).json()[
            "contribution_credits"
        ]
        == 1
    )


def test_strict_lead_self_heals_two_way_conflicts(
    client: TestClient, engine: Engine
) -> None:
    users = [register(client) for _index in range(5)]
    assignments = [1, 2, 1, 2, 2]
    expected_states = ["candidate", "disputed", "confirmed", "disputed", "confirmed"]
    for index, (user, assignment, expected) in enumerate(
        zip(users, assignments, expected_states, strict=True), start=1
    ):
        response = submit(
            engine,
            user,
            proposal([episode(1, assignment)]),
            key=f"conflict-submission-key-{index:04d}",
        )
        assert response.consensus is not None
        assert response.consensus.items[0].state.value == expected
        looked_up = lookup(
            client, register(client), key=f"conflict-lookup-key-{index:04d}"
        )
        if expected == "confirmed":
            assert looked_up.status_code == 200
            assert looked_up.json()["disc"]["titles"][0]["episode_number"] == (
                1 if index == 3 else 2
            )
        else:
            assert looked_up.status_code == 404


def test_latest_full_disc_submission_replaces_one_installations_old_vote(
    client: TestClient, engine: Engine
) -> None:
    first_user = register(client)
    second_user = register(client)
    submit(
        engine,
        first_user,
        proposal([episode(1, 1)]),
        key="replacement-first-user-0001",
    )
    submit(
        engine,
        second_user,
        proposal([episode(1, 1)]),
        key="replacement-second-user-0002",
    )
    assert (
        lookup(
            client, register(client), key="replacement-confirmed-lookup-0001"
        ).status_code
        == 200
    )

    replacement = submit(
        engine,
        second_user,
        proposal([episode(1, 2)]),
        key="replacement-second-user-0003",
    )
    assert replacement.consensus is not None
    assert replacement.consensus.items[0].state.value == "disputed"
    assert (
        lookup(
            client, register(client), key="replacement-disputed-lookup-0002"
        ).status_code
        == 404
    )
    assert (
        client.get("/v1/account/usage", headers=second_user.headers).json()[
            "contribution_credits"
        ]
        == 1
    )


def test_piecewise_layout_keeps_safe_majority_and_prefers_manual_bonus_name(
    client: TestClient, engine: Engine
) -> None:
    first_titles = [episode(index, index) for index in range(1, 19)]
    second_titles = [episode(index, index) for index in range(1, 19)]
    first_titles.append(
        {
            **episode(19, 19, match_source="manual_playback"),
            "classification": "extra",
            "series_name": None,
            "season_number": None,
            "episode_number": None,
            "episode_title": None,
            "display_title": "Original Documentary",
        }
    )
    second_titles.append(
        {
            **episode(19, 19, match_source="gemini"),
            "classification": "extra",
            "series_name": None,
            "season_number": None,
            "episode_number": None,
            "episode_title": None,
            "display_title": "Making Of Feature",
        }
    )
    first_titles.append(episode(20, 20))
    second_titles.append(episode(20, 21))

    submit(
        engine,
        register(client),
        proposal(first_titles),
        key="piecewise-first-upload-0001",
    )
    submit(
        engine,
        register(client),
        proposal(second_titles),
        key="piecewise-second-upload-0002",
    )
    response = lookup(client, register(client), key="piecewise-lookup-key-0003")
    assert response.status_code == 200
    disc = response.json()["disc"]
    assert len(disc["titles"]) == 19
    assert disc["consensus"]["confirmed_items"] == 19
    assert disc["consensus"]["unresolved_items"] == 1
    assert disc["consensus"]["whole_disc_consistent"] is True
    extra = next(title for title in disc["titles"] if title["title_index"] == 19)
    assert extra["display_title"] == "Original Documentary"
    unresolved = next(
        item for item in disc["consensus"]["items"] if item["title_index"] == 20
    )
    assert unresolved["state"] == "disputed"


def test_server_assisted_observation_never_forms_quorum(
    client: TestClient, engine: Engine
) -> None:
    assisted_installation = register(client)
    submit(
        engine,
        register(client),
        proposal([episode(1, 1)]),
        key="independent-upload-key-0001",
    )
    submit(
        engine,
        assisted_installation,
        proposal([episode(1, 1, match_source="server_assisted")]),
        key="assisted-upload-key-0002",
    )
    assisted_lookup = lookup(client, register(client), key="assisted-lookup-key-0003")
    assert assisted_lookup.status_code == 404
    help_response = client.get(
        f"/v1/help/discs/{CONTENT_HASH}", headers=register(client).headers
    ).json()
    candidate = help_response["items"][0]["candidates"][0]
    assert candidate["independent_support"] == 1
    assert candidate["total_observations"] == 2
    assert (
        client.get("/v1/account/usage", headers=assisted_installation.headers).json()[
            "contribution_credits"
        ]
        == 0
    )


def test_whole_disc_consistency_holds_only_duplicate_episode_items(
    client: TestClient, engine: Engine
) -> None:
    titles = [episode(1, 1), episode(2, 1), episode(3, 2)]
    for index in range(2):
        submit(
            engine,
            register(client),
            proposal(titles),
            key=f"duplicate-episode-upload-{index:04d}",
        )
    response = lookup(client, register(client), key="duplicate-episode-lookup-0001")
    assert response.status_code == 200
    disc = response.json()["disc"]
    assert [title["title_index"] for title in disc["titles"]] == [3]
    assert disc["consensus"]["whole_disc_consistent"] is False
    states = {item["title_index"]: item["state"] for item in disc["consensus"]["items"]}
    assert states == {1: "inconsistent", 2: "inconsistent", 3: "confirmed"}
