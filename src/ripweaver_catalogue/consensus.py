"""Automatic, path-free hybrid consensus for contributed disc layouts."""

import hashlib
import json
import re
import unicodedata
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from .models import (
    ConsensusAssertion,
    ConsensusDisc,
    ConsensusItem,
    CreditLedgerEntry,
    Submission,
)
from .schemas import (
    ConsensusCandidate,
    ConsensusHelpItem,
    ConsensusItemStatus,
    ConsensusState,
    ConsensusSummary,
    DiscHelpRecord,
    DiscRecord,
    DiscSubmissionInput,
    DiscTitleInput,
    MatchSource,
    TitleClassification,
)

QUORUM = 2
EVIDENCE_RANK = {
    MatchSource.MANUAL_PLAYBACK.value: 5,
    MatchSource.DETERMINISTIC.value: 4,
    MatchSource.LOCAL_EVIDENCE.value: 3,
    MatchSource.GEMINI.value: 2,
    MatchSource.SERVER_ASSISTED.value: 1,
}


def lock_consensus_scope(session: Session, content_hash: str) -> None:
    """Serialize one disc's recomputation on PostgreSQL without storing a lock."""

    if session.bind is None or session.bind.dialect.name != "postgresql":
        return
    digest = hashlib.sha256(content_hash.upper().encode("ascii")).digest()
    lock_key = int.from_bytes(digest[:8], byteorder="big", signed=True)
    session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key}
    )


@dataclass(slots=True)
class ComputedItem:
    title_index: int
    state: str
    winning_structural_key: str | None
    winning_assignment_key: str | None
    support_count: int
    runner_up_count: int
    candidate_count: int
    winning_title: DiscTitleInput | None


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _normalized_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(part for part in re.split(r"[^\w]+", normalized) if part)


def structural_key(title: DiscTitleInput) -> str:
    """Identify one structural title observation without display metadata."""

    return _digest(
        {
            "title_index": title.title_index,
            "source_file": title.source_file.casefold(),
            "segment_map": [part.casefold() for part in title.segment_map],
            "duration_seconds": title.duration_seconds,
            "size_bytes": title.size_bytes,
        }
    )


def assignment_key(title: DiscTitleInput) -> str:
    """Group semantically equal matches while ignoring cosmetic title spelling."""

    assignment: dict[str, object] = {"classification": title.classification.value}
    if title.classification == TitleClassification.EPISODE:
        assignment.update(
            {
                "series_name": _normalized_text(title.series_name),
                "season_number": title.season_number,
                "episode_number": title.episode_number,
            }
        )
    elif title.classification == TitleClassification.MOVIE:
        assignment.update(
            {
                "movie_title": _normalized_text(title.movie_title),
                "movie_year": title.movie_year,
            }
        )
    elif title.classification == TitleClassification.PLAY_ALL:
        assignment["contained_title_indexes"] = sorted(title.contained_title_indexes)
    return _digest(assignment)


def _display_identity(title: DiscTitleInput) -> str:
    value = title.display_title
    if title.classification == TitleClassification.EPISODE:
        value = title.episode_title
    elif title.classification == TitleClassification.MOVIE:
        value = title.movie_title
    return _normalized_text(value) or ""


def _representative(rows: list[ConsensusAssertion]) -> DiscTitleInput:
    label_counts = Counter(
        _display_identity(DiscTitleInput.model_validate_json(row.title_json))
        for row in rows
    )
    ranked = sorted(
        rows,
        key=lambda row: (
            -EVIDENCE_RANK[row.match_source],
            -label_counts[
                _display_identity(DiscTitleInput.model_validate_json(row.title_json))
            ],
            row.title_json,
        ),
    )
    return DiscTitleInput.model_validate_json(ranked[0].title_json)


def add_assertions(
    session: Session,
    submission: Submission,
    payload: DiscSubmissionInput,
) -> None:
    if payload.schema_version != 2 or submission.installation_id is None:
        return
    for title in payload.titles:
        if title.match_source is None:  # guarded by the public schema
            raise ValueError("Consensus contributions require match provenance")
        session.add(
            ConsensusAssertion(
                assertion_id=str(uuid.uuid4()),
                submission_id=submission.submission_id,
                installation_id=submission.installation_id,
                content_hash=payload.content_hash,
                title_index=title.title_index,
                structural_key=structural_key(title),
                assignment_key=assignment_key(title),
                title_json=_canonical_json(title.model_dump(mode="json")),
                match_source=title.match_source.value,
                independent=title.match_source != MatchSource.SERVER_ASSISTED,
            )
        )


def _latest_contributions(
    session: Session, content_hash: str
) -> tuple[list[Submission], list[ConsensusAssertion]]:
    submissions = session.scalars(
        select(Submission)
        .where(
            Submission.content_hash == content_hash,
            Submission.status == "accepted",
            Submission.installation_id.is_not(None),
        )
        .order_by(Submission.created_at, Submission.submission_id)
    ).all()
    latest_by_installation: dict[str, Submission] = {}
    for submission in submissions:
        if submission.installation_id is not None:
            latest_by_installation[submission.installation_id] = submission
    latest = list(latest_by_installation.values())
    if not latest:
        return [], []
    submission_ids = [row.submission_id for row in latest]
    assertions = session.scalars(
        select(ConsensusAssertion).where(
            ConsensusAssertion.submission_id.in_(submission_ids)
        )
    ).all()
    return latest, list(assertions)


def _compute_item(title_index: int, rows: list[ConsensusAssertion]) -> ComputedItem:
    independent = [row for row in rows if row.independent]
    candidate_count = len({(row.structural_key, row.assignment_key) for row in rows})
    structural_groups: dict[str, list[ConsensusAssertion]] = defaultdict(list)
    for row in independent:
        structural_groups[row.structural_key].append(row)
    ranked_structures = sorted(
        structural_groups.items(), key=lambda item: (-len(item[1]), item[0])
    )
    if not ranked_structures:
        return ComputedItem(
            title_index,
            ConsensusState.CANDIDATE.value,
            None,
            None,
            0,
            0,
            candidate_count,
            None,
        )
    structure_key, structure_rows = ranked_structures[0]
    structure_support = len(structure_rows)
    structure_runner = len(ranked_structures[1][1]) if len(ranked_structures) > 1 else 0
    if structure_support < QUORUM:
        state = (
            ConsensusState.DISPUTED.value
            if structure_runner == structure_support
            else ConsensusState.CANDIDATE.value
        )
        return ComputedItem(
            title_index,
            state,
            None,
            None,
            structure_support,
            structure_runner,
            candidate_count,
            None,
        )
    if structure_support == structure_runner:
        return ComputedItem(
            title_index,
            ConsensusState.DISPUTED.value,
            None,
            None,
            structure_support,
            structure_runner,
            candidate_count,
            None,
        )

    assignment_groups: dict[str, list[ConsensusAssertion]] = defaultdict(list)
    for row in structure_rows:
        assignment_groups[row.assignment_key].append(row)
    ranked_assignments = sorted(
        assignment_groups.items(), key=lambda item: (-len(item[1]), item[0])
    )
    assignment, assignment_rows = ranked_assignments[0]
    assignment_support = len(assignment_rows)
    assignment_runner = (
        len(ranked_assignments[1][1]) if len(ranked_assignments) > 1 else 0
    )
    runner = max(structure_runner, assignment_runner)
    if assignment_support < QUORUM or assignment_support == assignment_runner:
        return ComputedItem(
            title_index,
            ConsensusState.DISPUTED.value,
            None,
            None,
            assignment_support,
            runner,
            candidate_count,
            None,
        )
    return ComputedItem(
        title_index,
        ConsensusState.CONFIRMED.value,
        structure_key,
        assignment,
        assignment_support,
        runner,
        candidate_count,
        _representative(assignment_rows),
    )


def _quorum_choice(values: list[str]) -> tuple[str, bool]:
    counts = Counter(values)
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    winner, support = ranked[0]
    runner = ranked[1][1] if len(ranked) > 1 else 0
    return winner, support >= QUORUM and support > runner


def _preferred_optional_text(values: list[str | None]) -> str | None:
    present = [value for value in values if value]
    if not present:
        return None
    normalized_counts = Counter(_normalized_text(value) for value in present)
    return sorted(
        present,
        key=lambda value: (-normalized_counts[_normalized_text(value)], value),
    )[0]


def _apply_whole_disc_consistency(
    items: dict[int, ComputedItem], *, known_indexes: set[int], media_confirmed: bool
) -> None:
    if not media_confirmed:
        for item in items.values():
            if item.state == ConsensusState.CONFIRMED.value:
                item.state = ConsensusState.INCONSISTENT.value
        return

    episodes: dict[tuple[str | None, int | None, int | None], list[ComputedItem]] = (
        defaultdict(list)
    )
    for item in items.values():
        title = item.winning_title
        if item.state != ConsensusState.CONFIRMED.value or title is None:
            continue
        if title.classification == TitleClassification.EPISODE:
            episodes[
                (
                    _normalized_text(title.series_name),
                    title.season_number,
                    title.episode_number,
                )
            ].append(item)
        if title.classification == TitleClassification.PLAY_ALL and not set(
            title.contained_title_indexes
        ).issubset(known_indexes):
            item.state = ConsensusState.INCONSISTENT.value
    for duplicate_items in episodes.values():
        if len(duplicate_items) > 1:
            for item in duplicate_items:
                item.state = ConsensusState.INCONSISTENT.value


def _grant_consensus_credits(
    session: Session,
    *,
    content_hash: str,
    total_items: int,
    items: dict[int, ComputedItem],
    submissions: list[Submission],
    assertions: list[ConsensusAssertion],
    threshold: float,
) -> None:
    if total_items < 1:
        return
    by_submission: dict[str, list[ConsensusAssertion]] = defaultdict(list)
    for assertion in assertions:
        by_submission[assertion.submission_id].append(assertion)
    winners = {
        index: item
        for index, item in items.items()
        if item.state == ConsensusState.CONFIRMED.value
    }
    for submission in submissions:
        if submission.installation_id is None:
            continue
        rows = by_submission[submission.submission_id]
        # A layout that used catalogue help is useful corroborating telemetry, but
        # it is not an independent contribution and must not earn lookup credit.
        # Requiring every row to be independent also prevents a mostly-local disc
        # from receiving credit for replaying one server-assisted assignment.
        if not rows or any(not row.independent for row in rows):
            continue
        matching = sum(
            1
            for row in rows
            if (winner := winners.get(row.title_index)) is not None
            and row.structural_key == winner.winning_structural_key
            and row.assignment_key == winner.winning_assignment_key
        )
        if matching / total_items < threshold:
            continue
        existing = session.scalar(
            select(CreditLedgerEntry).where(
                CreditLedgerEntry.installation_id == submission.installation_id,
                CreditLedgerEntry.reason == "consensus_disc",
                CreditLedgerEntry.reference_id == content_hash,
                CreditLedgerEntry.bucket == "contribution",
            )
        )
        if existing is None:
            session.add(
                CreditLedgerEntry(
                    entry_id=str(uuid.uuid4()),
                    installation_id=submission.installation_id,
                    bucket="contribution",
                    delta=1,
                    reason="consensus_disc",
                    reference_id=content_hash,
                )
            )


def recompute_consensus(
    session: Session, content_hash: str, *, credit_threshold: float = 0.90
) -> None:
    normalized_hash = content_hash.upper()
    submissions, assertions = _latest_contributions(session, normalized_hash)
    if not submissions:
        return
    by_index: dict[int, list[ConsensusAssertion]] = defaultdict(list)
    for assertion in assertions:
        by_index[assertion.title_index].append(assertion)
    items = {
        index: _compute_item(index, rows) for index, rows in sorted(by_index.items())
    }

    independent_submission_payloads: list[DiscSubmissionInput] = []
    independent_submission_ids = {
        assertion.submission_id for assertion in assertions if assertion.independent
    }
    for submission in submissions:
        if submission.submission_id in independent_submission_ids:
            independent_submission_payloads.append(
                DiscSubmissionInput.model_validate_json(submission.payload_json)
            )
    all_payloads = [
        DiscSubmissionInput.model_validate_json(submission.payload_json)
        for submission in submissions
    ]
    media_values = [
        payload.media_type.value for payload in independent_submission_payloads
    ]
    if media_values:
        media_type, media_confirmed = _quorum_choice(media_values)
    else:
        media_type = all_payloads[0].media_type.value
        media_confirmed = False
    _apply_whole_disc_consistency(
        items, known_indexes=set(items), media_confirmed=media_confirmed
    )

    disc = session.get(ConsensusDisc, normalized_hash)
    if disc is None:
        disc = ConsensusDisc(
            content_hash=normalized_hash,
            media_type=media_type,
            generation=0,
            total_items=0,
            confirmed_items=0,
            unresolved_items=0,
            whole_disc_consistent=False,
        )
        session.add(disc)
        session.flush()
    disc.media_type = media_type
    disc.release_name = _preferred_optional_text(
        [payload.release_name for payload in independent_submission_payloads]
    )
    disc.edition = _preferred_optional_text(
        [payload.edition for payload in independent_submission_payloads]
    )
    disc.generation += 1
    disc.total_items = len(items)
    disc.confirmed_items = sum(
        item.state == ConsensusState.CONFIRMED.value for item in items.values()
    )
    disc.unresolved_items = disc.total_items - disc.confirmed_items
    disc.whole_disc_consistent = media_confirmed and all(
        item.state != ConsensusState.INCONSISTENT.value for item in items.values()
    )
    confirmed_payload = [
        item.winning_title.model_dump(mode="json")
        for item in items.values()
        if item.state == ConsensusState.CONFIRMED.value
        and item.winning_title is not None
    ]
    disc.payload_sha256 = (
        _digest(
            {
                "content_hash": normalized_hash,
                "media_type": disc.media_type,
                "release_name": disc.release_name,
                "edition": disc.edition,
                "titles": confirmed_payload,
            }
        )
        if confirmed_payload
        else None
    )
    disc.updated_at = datetime.now(UTC)

    if items:
        session.execute(
            delete(ConsensusItem).where(
                ConsensusItem.content_hash == normalized_hash,
                ConsensusItem.title_index.not_in(set(items)),
            )
        )
    for index, computed in items.items():
        row = session.get(ConsensusItem, (normalized_hash, index))
        if row is None:
            row = ConsensusItem(content_hash=normalized_hash, title_index=index)
            session.add(row)
        row.state = computed.state
        row.winning_structural_key = computed.winning_structural_key
        row.winning_assignment_key = computed.winning_assignment_key
        row.support_count = computed.support_count
        row.runner_up_count = computed.runner_up_count
        row.candidate_count = computed.candidate_count
        row.winning_title_json = (
            _canonical_json(computed.winning_title.model_dump(mode="json"))
            if computed.winning_title is not None
            else None
        )
        row.updated_at = datetime.now(UTC)
    _grant_consensus_credits(
        session,
        content_hash=normalized_hash,
        total_items=disc.total_items,
        items=items,
        submissions=submissions,
        assertions=assertions,
        threshold=credit_threshold,
    )


def consensus_summary(session: Session, content_hash: str) -> ConsensusSummary | None:
    disc = session.get(ConsensusDisc, content_hash.upper())
    if disc is None:
        return None
    rows = session.scalars(
        select(ConsensusItem)
        .where(ConsensusItem.content_hash == disc.content_hash)
        .order_by(ConsensusItem.title_index)
    ).all()
    return ConsensusSummary(
        total_items=disc.total_items,
        confirmed_items=disc.confirmed_items,
        unresolved_items=disc.unresolved_items,
        whole_disc_consistent=disc.whole_disc_consistent,
        complete=disc.total_items > 0 and disc.unresolved_items == 0,
        items=tuple(
            ConsensusItemStatus(
                title_index=row.title_index,
                state=row.state,
                support_count=row.support_count,
                runner_up_count=row.runner_up_count,
                candidate_count=row.candidate_count,
            )
            for row in rows
        ),
    )


def get_consensus_disc(session: Session, content_hash: str) -> DiscRecord | None:
    disc = session.get(ConsensusDisc, content_hash.upper())
    if disc is None or disc.confirmed_items < 1 or disc.payload_sha256 is None:
        return None
    rows = session.scalars(
        select(ConsensusItem)
        .where(
            ConsensusItem.content_hash == disc.content_hash,
            ConsensusItem.state == ConsensusState.CONFIRMED.value,
        )
        .order_by(ConsensusItem.title_index)
    ).all()
    titles = tuple(
        DiscTitleInput.model_validate_json(row.winning_title_json)
        for row in rows
        if row.winning_title_json is not None
    )
    if not titles:
        return None
    summary = consensus_summary(session, disc.content_hash)
    return DiscRecord(
        schema_version=2,
        content_hash=disc.content_hash,
        media_type=disc.media_type,
        release_name=disc.release_name,
        edition=disc.edition,
        titles=titles,
        revision=disc.generation,
        payload_sha256=disc.payload_sha256,
        status="consensus",
        consensus=summary,
    )


def get_consensus_help(session: Session, content_hash: str) -> DiscHelpRecord | None:
    normalized_hash = content_hash.upper()
    disc = session.get(ConsensusDisc, normalized_hash)
    if disc is None:
        return None
    _submissions, assertions = _latest_contributions(session, normalized_hash)
    stored_items = {
        item.title_index: item
        for item in session.scalars(
            select(ConsensusItem).where(ConsensusItem.content_hash == normalized_hash)
        ).all()
    }
    by_index: dict[int, list[ConsensusAssertion]] = defaultdict(list)
    for assertion in assertions:
        by_index[assertion.title_index].append(assertion)
    help_items: list[ConsensusHelpItem] = []
    for index, rows in sorted(by_index.items()):
        groups: dict[tuple[str, str], list[ConsensusAssertion]] = defaultdict(list)
        for row in rows:
            groups[(row.structural_key, row.assignment_key)].append(row)
        candidates: list[ConsensusCandidate] = []
        for grouped_rows in groups.values():
            representative = _representative(grouped_rows)
            best_source = max(
                grouped_rows, key=lambda row: EVIDENCE_RANK[row.match_source]
            ).match_source
            candidates.append(
                ConsensusCandidate(
                    title=representative,
                    independent_support=sum(row.independent for row in grouped_rows),
                    total_observations=len(grouped_rows),
                    best_match_source=best_source,
                )
            )
        candidates.sort(
            key=lambda candidate: (
                -candidate.independent_support,
                -EVIDENCE_RANK[candidate.best_match_source.value],
                _canonical_json(candidate.title.model_dump(mode="json")),
            )
        )
        state = stored_items[index].state
        help_items.append(
            ConsensusHelpItem(
                title_index=index,
                state=state,
                candidates=tuple(candidates),
            )
        )
    return DiscHelpRecord(
        content_hash=normalized_hash,
        media_type=disc.media_type,
        total_items=disc.total_items,
        items=tuple(help_items),
    )
