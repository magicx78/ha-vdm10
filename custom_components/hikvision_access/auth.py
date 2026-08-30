"""HTTP Digest authentication (RFC 7616) for Hikvision ISAPI devices.

Why a hand-rolled helper: the VDM10 (and Hikvision ISAPI in general) only
offers Digest auth. aiohttp 3.12+ ships ``DigestAuthMiddleware``, but
middlewares bind to a ``ClientSession`` at construction time — incompatible
with Home Assistant's shared session from ``async_get_clientsession(hass)``.
Keeping the shared session (an iron rule of this integration) therefore
requires computing the ``Authorization`` header ourselves. ~100 lines,
no extra dependency, fully unit-testable against the RFC 7616 vectors.

Supports MD5, SHA-256 and their ``-sess`` variants with ``qop=auth``,
reuses the server nonce and increments the ``nc`` counter as the RFC
requires. Passwords never appear in logs or error messages.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import os
import re

_QUOTED_PAIR_RE = re.compile(r'(\w+)\s*=\s*(?:"([^"]*)"|([^\s,]+))')

_HASHERS = {
    "MD5": hashlib.md5,
    "MD5-SESS": hashlib.md5,
    "SHA-256": hashlib.sha256,
    "SHA-256-SESS": hashlib.sha256,
}


class DigestAuthError(Exception):
    """Raised when a Digest challenge cannot be parsed or answered."""


@dataclass
class DigestChallenge:
    """A parsed ``WWW-Authenticate: Digest ...`` challenge."""

    realm: str
    nonce: str
    qop: str | None = None
    algorithm: str = "MD5"
    opaque: str | None = None
    stale: bool = False


@dataclass
class HikvisionDigestAuth:
    """Computes Digest ``Authorization`` headers for one credential pair."""

    username: str
    password: str
    _challenge: DigestChallenge | None = field(default=None, repr=False)
    _nc: int = field(default=0, repr=False)

    @property
    def has_challenge(self) -> bool:
        """Return True once a server challenge has been captured."""
        return self._challenge is not None

    def clear(self) -> None:
        """Drop the cached challenge (e.g. after a final 401)."""
        self._challenge = None
        self._nc = 0

    def handle_401(self, www_authenticate: str | None) -> DigestChallenge:
        """Parse a 401 challenge header and cache it for subsequent requests.

        Returns the parsed challenge; ``challenge.stale`` tells the caller
        whether the previous nonce merely expired (retry is fine) or the
        credentials were actually rejected.
        """
        if not www_authenticate or not www_authenticate.strip().lower().startswith(
            "digest"
        ):
            raise DigestAuthError("Server did not offer a Digest challenge")

        params: dict[str, str] = {}
        for match in _QUOTED_PAIR_RE.finditer(
            www_authenticate.strip()[len("Digest") :]
        ):
            key = match.group(1).lower()
            params[key] = (
                match.group(2) if match.group(2) is not None else match.group(3)
            )

        try:
            challenge = DigestChallenge(
                realm=params["realm"],
                nonce=params["nonce"],
                qop=params.get("qop"),
                algorithm=params.get("algorithm", "MD5").upper(),
                opaque=params.get("opaque"),
                stale=params.get("stale", "").lower() == "true",
            )
        except KeyError as err:
            raise DigestAuthError(f"Digest challenge lacks {err}") from err

        if challenge.algorithm not in _HASHERS:
            raise DigestAuthError(
                f"Unsupported Digest algorithm: {challenge.algorithm}"
            )
        if challenge.qop is not None and "auth" not in [
            q.strip() for q in challenge.qop.split(",")
        ]:
            raise DigestAuthError(f"Unsupported qop: {challenge.qop}")

        self._challenge = challenge
        self._nc = 0
        return challenge

    def authorization_header(
        self, method: str, uri: str, cnonce: str | None = None
    ) -> str:
        """Build the ``Authorization`` header value for one request.

        ``cnonce`` is injectable for deterministic tests; production callers
        leave it None and get a random one.
        """
        challenge = self._challenge
        if challenge is None:
            raise DigestAuthError("No challenge captured yet (call handle_401 first)")

        hasher = _HASHERS[challenge.algorithm]

        def h(data: str) -> str:
            return hasher(data.encode("utf-8")).hexdigest()

        if cnonce is None:
            cnonce = os.urandom(16).hex()
        self._nc += 1
        nc_value = f"{self._nc:08x}"

        ha1 = h(f"{self.username}:{challenge.realm}:{self.password}")
        if challenge.algorithm.endswith("-SESS"):
            ha1 = h(f"{ha1}:{challenge.nonce}:{cnonce}")
        ha2 = h(f"{method.upper()}:{uri}")

        if challenge.qop is None:
            response = h(f"{ha1}:{challenge.nonce}:{ha2}")
        else:
            response = h(f"{ha1}:{challenge.nonce}:{nc_value}:{cnonce}:auth:{ha2}")

        parts = [
            f'username="{self.username}"',
            f'realm="{challenge.realm}"',
            f'nonce="{challenge.nonce}"',
            f'uri="{uri}"',
            f'response="{response}"',
            f"algorithm={challenge.algorithm}",
        ]
        if challenge.opaque is not None:
            parts.append(f'opaque="{challenge.opaque}"')
        if challenge.qop is not None:
            parts.extend(["qop=auth", f"nc={nc_value}", f'cnonce="{cnonce}"'])

        return "Digest " + ", ".join(parts)
