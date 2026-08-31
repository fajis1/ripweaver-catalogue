"""Strict, bounded parsing for the untrusted submission boundary."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from fastapi import Request
from pydantic import ValidationError

from .schemas import DiscSubmissionInput

MAX_SUBMISSION_BYTES = 256 * 1024
MAX_JSON_DEPTH = 16
MAX_JSON_NODES = 20_000
VALIDATION_VERSION = 1


class SubmissionEnvelopeError(ValueError):
    """A path- and payload-free rejection safe to return to a caller."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        super().__init__(detail)


def _unique_object(pairs: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SubmissionEnvelopeError(
                422, "Submission JSON contains duplicate object keys"
            )
        result[key] = value
    return result


def _reject_non_finite(value: str) -> None:
    raise SubmissionEnvelopeError(422, "Submission JSON contains a non-finite number")


def _validate_shape(value: object) -> None:
    nodes = 0
    pending: list[tuple[object, int]] = [(value, 1)]
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES:
            raise SubmissionEnvelopeError(422, "Submission JSON is too complex")
        if depth > MAX_JSON_DEPTH:
            raise SubmissionEnvelopeError(422, "Submission JSON is nested too deeply")
        if isinstance(item, dict):
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)


async def parse_submission_request(request: Request) -> DiscSubmissionInput:
    """Read and validate one request without trusting transport metadata."""

    media_type = request.headers.get("content-type", "").partition(";")[0]
    if media_type.strip().casefold() != "application/json":
        raise SubmissionEnvelopeError(
            415, "Catalogue submissions require application/json"
        )
    content_encoding = request.headers.get("content-encoding", "identity")
    if content_encoding.strip().casefold() not in {"", "identity"}:
        raise SubmissionEnvelopeError(
            415, "Compressed catalogue submissions are not accepted"
        )
    declared_length = request.headers.get("content-length")
    if declared_length is not None:
        try:
            parsed_length = int(declared_length)
        except ValueError as exc:
            raise SubmissionEnvelopeError(
                400, "Submission Content-Length is invalid"
            ) from exc
        if parsed_length < 0:
            raise SubmissionEnvelopeError(400, "Submission Content-Length is invalid")
        if parsed_length > MAX_SUBMISSION_BYTES:
            raise SubmissionEnvelopeError(413, "Submission body is too large")

    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_SUBMISSION_BYTES:
            raise SubmissionEnvelopeError(413, "Submission body is too large")
        body.extend(chunk)
    if not body:
        raise SubmissionEnvelopeError(422, "Submission body is empty")
    try:
        document = json.loads(
            bytes(body).decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_non_finite,
        )
    except SubmissionEnvelopeError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise SubmissionEnvelopeError(422, "Submission body is not valid JSON") from exc
    _validate_shape(document)
    if not isinstance(document, dict):
        raise SubmissionEnvelopeError(422, "Submission body must be a JSON object")
    try:
        return DiscSubmissionInput.model_validate(document)
    except ValidationError as exc:
        raise SubmissionEnvelopeError(
            422, "Submission body failed strict catalogue validation"
        ) from exc
