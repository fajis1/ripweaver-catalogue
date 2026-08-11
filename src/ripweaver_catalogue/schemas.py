"""Path-free public protocol schemas."""

import re
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ContentHash = Annotated[str, Field(pattern=r"^[0-9A-Fa-f]{32}$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class MediaType(StrEnum):
    DVD = "dvd"
    BLURAY = "bluray"
    UHD = "uhd"


class TitleClassification(StrEnum):
    EPISODE = "episode"
    MOVIE = "movie"
    EXTRA = "extra"
    COMMENTARY = "commentary"
    PLAY_ALL = "play_all"
    MENU = "menu"
    WARNING = "warning"
    UNKNOWN = "unknown"


class MatchSource(StrEnum):
    MANUAL_PLAYBACK = "manual_playback"
    DETERMINISTIC = "deterministic"
    LOCAL_EVIDENCE = "local_evidence"
    GEMINI = "gemini"
    SERVER_ASSISTED = "server_assisted"


class ConsensusState(StrEnum):
    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"
    DISPUTED = "disputed"
    INCONSISTENT = "inconsistent"


class DiscTitleInput(StrictModel):
    title_index: int = Field(ge=0, le=9999)
    source_file: str = Field(min_length=1, max_length=96)
    segment_map: tuple[str, ...] = Field(default=(), max_length=500)
    duration_seconds: int | None = Field(default=None, ge=1, le=172800)
    size_bytes: int | None = Field(default=None, ge=1, le=10_000_000_000_000)
    classification: TitleClassification
    series_name: str | None = Field(default=None, max_length=200)
    season_number: int | None = Field(default=None, ge=0, le=999)
    episode_number: int | None = Field(default=None, ge=0, le=9999)
    episode_title: str | None = Field(default=None, max_length=300)
    movie_title: str | None = Field(default=None, max_length=300)
    movie_year: int | None = Field(default=None, ge=1870, le=2200)
    display_title: str | None = Field(default=None, max_length=300)
    contained_title_indexes: tuple[int, ...] = Field(default=(), max_length=1000)
    match_source: MatchSource | None = None

    @field_validator("source_file")
    @classmethod
    def source_file_must_be_path_free(cls, value: str) -> str:
        if "/" in value or "\\" in value or value in {".", ".."}:
            raise ValueError("source_file must be a path-free disc identifier")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
            raise ValueError("source_file contains unsupported characters")
        return value

    @field_validator("segment_map")
    @classmethod
    def segment_map_must_be_structural(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(re.fullmatch(r"[A-Za-z0-9_.-]{1,32}", item) is None for item in value):
            raise ValueError("segment_map contains an invalid identifier")
        return value

    @model_validator(mode="after")
    def validate_assignment(self) -> "DiscTitleInput":
        episode_fields = (
            self.series_name,
            self.season_number,
            self.episode_number,
            self.episode_title,
        )
        movie_fields = (self.movie_title, self.movie_year)
        if self.classification == TitleClassification.EPISODE:
            if (
                self.series_name is None
                or self.season_number is None
                or self.episode_number is None
            ):
                raise ValueError(
                    "episode assignments require series, season, and episode"
                )
            if any(value is not None for value in movie_fields):
                raise ValueError("episode assignments cannot contain movie fields")
        elif self.classification == TitleClassification.MOVIE:
            if self.movie_title is None:
                raise ValueError("movie assignments require movie_title")
            if any(value is not None for value in episode_fields):
                raise ValueError("movie assignments cannot contain episode fields")
        elif any(value is not None for value in (*episode_fields, *movie_fields)):
            raise ValueError(
                "non-episode and non-movie titles cannot contain assignment fields"
            )
        if len(self.contained_title_indexes) != len(set(self.contained_title_indexes)):
            raise ValueError("contained title indexes must be unique")
        if self.title_index in self.contained_title_indexes:
            raise ValueError("a title cannot contain itself")
        if (
            self.classification != TitleClassification.PLAY_ALL
            and self.contained_title_indexes
        ):
            raise ValueError("only play-all titles can contain other title indexes")
        return self


class DiscSubmissionInput(StrictModel):
    schema_version: Literal[1, 2] = 1
    content_hash: ContentHash
    media_type: MediaType
    release_name: str | None = Field(default=None, max_length=300)
    edition: str | None = Field(default=None, max_length=200)
    titles: tuple[DiscTitleInput, ...] = Field(min_length=1, max_length=1000)

    @field_validator("content_hash")
    @classmethod
    def normalize_hash(cls, value: str) -> str:
        return value.upper()

    @model_validator(mode="after")
    def title_indexes_must_be_unique(self) -> "DiscSubmissionInput":
        indexes = [title.title_index for title in self.titles]
        if len(indexes) != len(set(indexes)):
            raise ValueError("title indexes must be unique")
        if self.schema_version == 2 and any(
            title.match_source is None for title in self.titles
        ):
            raise ValueError("schema version 2 requires a match source for every title")
        available = set(indexes)
        for title in self.titles:
            if not set(title.contained_title_indexes).issubset(available):
                raise ValueError("contained title indexes must exist on the same disc")
        return self


class ConsensusItemStatus(StrictModel):
    title_index: int
    state: ConsensusState
    support_count: int
    runner_up_count: int
    candidate_count: int


class ConsensusSummary(StrictModel):
    quorum: Literal[2] = 2
    total_items: int
    confirmed_items: int
    unresolved_items: int
    whole_disc_consistent: bool
    complete: bool
    items: tuple[ConsensusItemStatus, ...]


class SubmissionReceipt(StrictModel):
    submission_id: str
    content_hash: str
    payload_sha256: str
    status: Literal["pending", "accepted", "approved", "rejected"]
    consensus: ConsensusSummary | None = None


class SubmissionSummary(SubmissionReceipt):
    client_version: str
    rejection_code: str | None
    created_at: str
    reviewed_at: str | None


class DiscRecord(StrictModel):
    schema_version: Literal[1, 2]
    content_hash: str
    media_type: MediaType
    release_name: str | None
    edition: str | None
    titles: tuple[DiscTitleInput, ...]
    revision: int
    payload_sha256: str
    status: Literal["reviewed", "consensus"] = "reviewed"
    consensus: ConsensusSummary | None = None


class ConsensusCandidate(StrictModel):
    title: DiscTitleInput
    independent_support: int
    total_observations: int
    best_match_source: MatchSource


class ConsensusHelpItem(StrictModel):
    title_index: int
    state: ConsensusState
    candidates: tuple[ConsensusCandidate, ...]


class DiscHelpRecord(StrictModel):
    schema_version: Literal[2] = 2
    content_hash: str
    media_type: MediaType
    total_items: int
    items: tuple[ConsensusHelpItem, ...]


class RejectionRequest(StrictModel):
    reason_code: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")


class InstallationReceipt(StrictModel):
    installation_id: str
    access_token: str
    token_type: Literal["bearer"] = "bearer"


class UsageSummary(StrictModel):
    monthly_limit: int
    monthly_used: int
    monthly_remaining: int
    contribution_credits: int
    purchased_credits: int
    total_automatic_remaining: int


class LookupRequest(StrictModel):
    mode: Literal["automatic", "manual"] = "automatic"
    support_prompt_version: str | None = Field(default=None, max_length=32)

    @model_validator(mode="after")
    def manual_mode_requires_prompt_version(self) -> "LookupRequest":
        if self.mode == "manual" and self.support_prompt_version is None:
            raise ValueError(
                "manual lookup requires the displayed support prompt version"
            )
        if self.mode == "automatic" and self.support_prompt_version is not None:
            raise ValueError("automatic lookup cannot acknowledge a support prompt")
        return self


class LookupResponse(StrictModel):
    disc: DiscRecord
    usage: UsageSummary
    credit_source: Literal["monthly", "contribution", "purchased", "manual", "cached"]


class SupportPolicy(StrictModel):
    policy_version: str
    terms_version: str
    currency: Literal["usd"] = "usd"
    minimum_amount_cents: int
    minimum_rate_cents: int
    maximum_rate_cents: int
    default_rate_cents: int
    monthly_automatic_lookups: int
    payments_enabled: bool
    support_message: str
    availability_disclosure: str
    refund_disclosure: str


class SupportRequired(StrictModel):
    code: Literal["support_confirmation_required"] = "support_confirmation_required"
    message: str
    usage: UsageSummary
    policy: SupportPolicy


class SupportCheckoutInput(StrictModel):
    amount_cents: int = Field(ge=100, le=100_000)
    support_rate_cents: int = Field(ge=1, le=100)
    terms_version: str = Field(max_length=32)
    accept_best_effort_terms: Literal[True]


class SupportCheckoutReceipt(StrictModel):
    order_id: str
    amount_cents: int
    support_rate_cents: int
    automatic_lookup_credits: int
    currency: Literal["usd"] = "usd"
    checkout_url: str
    status: Literal["checkout_created", "paid"]


class PaymentWebhookReceipt(StrictModel):
    received: Literal[True] = True
