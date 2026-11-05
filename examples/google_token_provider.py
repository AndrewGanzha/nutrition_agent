"""Minimal refresh-token → access-token provider for :class:`GoogleHealthSink`.

The package itself never stores OAuth secrets or refresh tokens: the caller
owns them (env, secret manager) and injects a provider implementing
``TokenProvider``. This example is the smallest thing that works against the
real endpoint; cache tokens yourself if you call it often.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

import httpx

from nutrition_agent import GoogleHealthSink, TokenProvider

TOKEN_URL = "https://oauth2.googleapis.com/token"


class RefreshTokenProvider:
    """Exchanges a stored refresh token for a short-lived access token."""

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        refresh_tokens: Mapping[str | None, str],
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_tokens = refresh_tokens
        self._transport = transport

    async def __call__(self, user_id: str | None) -> str:
        refresh_token = self._refresh_tokens.get(user_id)
        if refresh_token is None:
            raise LookupError(f"no refresh token for user {user_id!r}")
        async with httpx.AsyncClient(timeout=10, transport=self._transport) as client:
            response = await client.post(
                TOKEN_URL,
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
            )
            response.raise_for_status()
            return str(response.json()["access_token"])


def build_google_sink(refresh_tokens: Mapping[str | None, str]) -> GoogleHealthSink:
    """Wire the provider into the sink; the OAuth secrets stay in the caller."""
    provider: TokenProvider = RefreshTokenProvider(
        client_id=os.environ["GOOGLE_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
        refresh_tokens=refresh_tokens,
    )
    return GoogleHealthSink(
        os.environ.get("NUTRITION_DB", "nutrition.db"),
        token_provider=provider,
    )
