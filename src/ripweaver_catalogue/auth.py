"""Constant-time bearer-token dependencies."""

import secrets
from collections.abc import Callable

from fastapi import Header, HTTPException, status
from pydantic import SecretStr


def bearer_dependency(
    configured: SecretStr | None, *, role: str
) -> Callable[[str | None], None]:
    def require_bearer(authorization: str | None = Header(default=None)) -> None:
        if configured is None or len(configured.get_secret_value()) < 32:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"{role} authentication is not configured",
            )
        scheme, separator, token = (authorization or "").partition(" ")
        valid = separator == " " and scheme.casefold() == "bearer"
        valid = valid and secrets.compare_digest(token, configured.get_secret_value())
        if not valid:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )

    return require_bearer
