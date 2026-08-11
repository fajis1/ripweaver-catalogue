from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from ripweaver_catalogue.config import Settings
from ripweaver_catalogue.main import create_app
from ripweaver_catalogue.models import Base

CONTENT_HASH = "ABCDEF0123456789ABCDEF0123456789"


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
        allowed_hosts=["testserver"],
    )
    with TestClient(create_app(settings=settings, engine=engine)) as test_client:
        yield test_client
    engine.dispose()


def register(client: TestClient) -> dict[str, str]:
    response = client.post("/v1/installations/register")
    assert response.status_code == 201
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


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
    client: TestClient,
    authentication: dict[str, str],
    payload: dict[str, object],
    *,
    key: str,
):
    return client.post(
        "/v1/submissions",
        json=payload,
        headers={
            **authentication,
            "Idempotency-Key": key,
            "X-RipWeaver-Version": "consensus-test",
        },
    )


def lookup(client: TestClient, authentication: dict[str, str], *, key: str):
    return client.post(
        f"/v1/lookups/discs/{CONTENT_HASH}",
        json={"mode": "automatic"},
        headers={**authentication, "Idempotency-Key": key},
    )


def test_one_upload_is_help_only_and_two_matching_uploads_confirm(
    client: TestClient,
) -> None:
    first_user = register(client)
    first = submit(
        client,
        first_user,
        proposal([episode(1, 1)]),
        key="consensus-first-upload-0001",
    )
    assert first.status_code == 202
    assert first.json()["status"] == "accepted"
    assert first.json()["consensus"]["items"][0]["state"] == "candidate"
    candidate_lookup = lookup(client, first_user, key="candidate-lookup-key-0001")
    assert candidate_lookup.status_code == 404

    help_response = client.get(f"/v1/help/discs/{CONTENT_HASH}", headers=first_user)
    assert help_response.status_code == 200
    candidate = help_response.json()["items"][0]["candidates"][0]
    assert candidate["independent_support"] == 1
    assert candidate["total_observations"] == 1

    second_user = register(client)
    second = submit(
        client,
        second_user,
        proposal([episode(1, 1)]),
        key="consensus-second-upload-0002",
    )
    assert second.status_code == 202
    assert second.json()["consensus"]["complete"] is True
    confirmed = lookup(client, register(client), key="confirmed-lookup-key-0002")
    assert confirmed.status_code == 200
    assert confirmed.json()["disc"]["status"] == "consensus"
    assert confirmed.json()["disc"]["titles"][0]["episode_number"] == 1
    assert (
        client.get("/v1/account/usage", headers=first_user).json()[
            "contribution_credits"
        ]
        == 1
    )
    assert (
        client.get("/v1/account/usage", headers=second_user).json()[
            "contribution_credits"
        ]
        == 1
    )


def test_strict_lead_self_heals_two_way_conflicts(client: TestClient) -> None:
    users = [register(client) for _index in range(5)]
    assignments = [1, 2, 1, 2, 2]
    expected_states = ["candidate", "disputed", "confirmed", "disputed", "confirmed"]
    for index, (user, assignment, expected) in enumerate(
        zip(users, assignments, expected_states, strict=True), start=1
    ):
        response = submit(
            client,
            user,
            proposal([episode(1, assignment)]),
            key=f"conflict-submission-key-{index:04d}",
        )
        assert response.status_code == 202
        assert response.json()["consensus"]["items"][0]["state"] == expected
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
    client: TestClient,
) -> None:
    first_user = register(client)
    second_user = register(client)
    submit(
        client,
        first_user,
        proposal([episode(1, 1)]),
        key="replacement-first-user-0001",
    )
    submit(
        client,
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
        client,
        second_user,
        proposal([episode(1, 2)]),
        key="replacement-second-user-0003",
    )
    assert replacement.json()["consensus"]["items"][0]["state"] == "disputed"
    assert (
        lookup(
            client, register(client), key="replacement-disputed-lookup-0002"
        ).status_code
        == 404
    )
    assert (
        client.get("/v1/account/usage", headers=second_user).json()[
            "contribution_credits"
        ]
        == 1
    )


def test_piecewise_layout_keeps_safe_majority_and_prefers_manual_bonus_name(
    client: TestClient,
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
        client,
        register(client),
        proposal(first_titles),
        key="piecewise-first-upload-0001",
    )
    submit(
        client,
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


def test_server_assisted_observation_never_forms_quorum(client: TestClient) -> None:
    assisted_installation = register(client)
    submit(
        client,
        register(client),
        proposal([episode(1, 1)]),
        key="independent-upload-key-0001",
    )
    submit(
        client,
        assisted_installation,
        proposal([episode(1, 1, match_source="server_assisted")]),
        key="assisted-upload-key-0002",
    )
    assisted_lookup = lookup(client, register(client), key="assisted-lookup-key-0003")
    assert assisted_lookup.status_code == 404
    help_response = client.get(
        f"/v1/help/discs/{CONTENT_HASH}", headers=register(client)
    ).json()
    candidate = help_response["items"][0]["candidates"][0]
    assert candidate["independent_support"] == 1
    assert candidate["total_observations"] == 2
    assert (
        client.get("/v1/account/usage", headers=assisted_installation).json()[
            "contribution_credits"
        ]
        == 0
    )


def test_whole_disc_consistency_holds_only_duplicate_episode_items(
    client: TestClient,
) -> None:
    titles = [episode(1, 1), episode(2, 1), episode(3, 2)]
    for index in range(2):
        submit(
            client,
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
