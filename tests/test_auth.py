"""Tests for the RFC 7616 Digest helper."""

from __future__ import annotations

import hashlib
import re

import pytest

from custom_components.hikvision_access.auth import (
    DigestAuthError,
    HikvisionDigestAuth,
)

# Official example from RFC 7616 section 3.9.1: same request answered for
# MD5 and SHA-256. Any change to our hashing breaks these known responses.
RFC_USERNAME = "Mufasa"
RFC_PASSWORD = "Circle of Life"
RFC_REALM = "http-auth@example.org"
RFC_URI = "/dir/index.html"
RFC_NONCE = "7ypf/xlj9XXwfDPEoM4URrv/xwf94BcCAzFZH4GiTo0v"
RFC_OPAQUE = "FQhe/qaU925kfnzjCev0ciny7QMkPqMAFRtzCUYo5tdS"
RFC_CNONCE = "f2/wE4q74E6zIJEtWaHKaf5wv/H5QzzpXusqGemxURZJ"
RFC_RESPONSE_MD5 = "8ca523f5e9506fed4657c9700eebdbec"
RFC_RESPONSE_SHA256 = "753927fa0e85d155564e2e272a28d1802ca10daf4496794697cf8db5856cb6c1"


def _rfc_challenge(algorithm: str) -> str:
    return (
        f'Digest realm="{RFC_REALM}", qop="auth", algorithm={algorithm}, '
        f'nonce="{RFC_NONCE}", opaque="{RFC_OPAQUE}"'
    )


def _header_params(header: str) -> dict[str, str]:
    return {
        match.group(1): match.group(2) if match.group(2) is not None else match.group(3)
        for match in re.finditer(r'(\w+)\s*=\s*(?:"([^"]*)"|([^\s,]+))', header)
    }


@pytest.mark.parametrize(
    ("algorithm", "expected_response"),
    [("MD5", RFC_RESPONSE_MD5), ("SHA-256", RFC_RESPONSE_SHA256)],
)
def test_rfc7616_vectors(algorithm: str, expected_response: str) -> None:
    """The helper reproduces the RFC 7616 section 3.9.1 responses exactly."""
    auth = HikvisionDigestAuth(username=RFC_USERNAME, password=RFC_PASSWORD)
    challenge = auth.handle_401(_rfc_challenge(algorithm))
    assert challenge.realm == RFC_REALM
    assert challenge.stale is False

    header = auth.authorization_header("GET", RFC_URI, cnonce=RFC_CNONCE)
    params = _header_params(header)
    assert header.startswith("Digest ")
    assert params["response"] == expected_response
    assert params["nc"] == "00000001"
    assert params["qop"] == "auth"
    assert params["opaque"] == RFC_OPAQUE
    assert params["algorithm"] == algorithm


def test_nc_counter_increments_and_resets() -> None:
    """nc counts up per request and resets on a fresh challenge."""
    auth = HikvisionDigestAuth(username="user", password="pass")
    auth.handle_401(_rfc_challenge("MD5"))

    first = _header_params(auth.authorization_header("GET", "/a", cnonce="c1"))
    second = _header_params(auth.authorization_header("GET", "/a", cnonce="c1"))
    assert first["nc"] == "00000001"
    assert second["nc"] == "00000002"
    assert first["response"] != second["response"]

    auth.handle_401(_rfc_challenge("MD5"))
    third = _header_params(auth.authorization_header("GET", "/a", cnonce="c1"))
    assert third["nc"] == "00000001"
    assert third["response"] == first["response"]


def test_stale_flag_is_parsed() -> None:
    """stale=true reaches the caller so a silent retry is possible."""
    auth = HikvisionDigestAuth(username="user", password="pass")
    challenge = auth.handle_401('Digest realm="r", nonce="n2", qop="auth", stale=true')
    assert challenge.stale is True


def test_legacy_challenge_without_qop() -> None:
    """RFC 2069 style (no qop): response = H(HA1:nonce:HA2)."""
    auth = HikvisionDigestAuth(username="user", password="pass")
    auth.handle_401('Digest realm="r", nonce="n1"')
    params = _header_params(auth.authorization_header("GET", "/x", cnonce="ignored"))

    ha1 = hashlib.md5(b"user:r:pass").hexdigest()
    ha2 = hashlib.md5(b"GET:/x").hexdigest()
    expected = hashlib.md5(f"{ha1}:n1:{ha2}".encode()).hexdigest()
    assert params["response"] == expected
    assert "qop" not in params
    assert "nc" not in params


def test_sess_variant_uses_cnonce_in_ha1() -> None:
    """MD5-sess mixes nonce and cnonce into HA1."""
    auth = HikvisionDigestAuth(username="user", password="pass")
    auth.handle_401('Digest realm="r", nonce="n1", qop="auth", algorithm=MD5-sess')
    params = _header_params(auth.authorization_header("GET", "/x", cnonce="cn"))

    ha1 = hashlib.md5(b"user:r:pass").hexdigest()
    ha1 = hashlib.md5(f"{ha1}:n1:cn".encode()).hexdigest()
    ha2 = hashlib.md5(b"GET:/x").hexdigest()
    expected = hashlib.md5(f"{ha1}:n1:00000001:cn:auth:{ha2}".encode()).hexdigest()
    assert params["response"] == expected


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        'Basic realm="r"',
        'Digest nonce="n1"',  # realm missing
        'Digest realm="r"',  # nonce missing
        'Digest realm="r", nonce="n", algorithm=SHA-512',  # unsupported algo
        'Digest realm="r", nonce="n", qop="auth-int"',  # unsupported qop
    ],
)
def test_bad_challenges_raise(header: str | None) -> None:
    """Unusable challenges raise instead of producing broken headers."""
    auth = HikvisionDigestAuth(username="user", password="pass")
    with pytest.raises(DigestAuthError):
        auth.handle_401(header)


def test_header_before_challenge_raises() -> None:
    """Asking for a header without a challenge is a programming error."""
    auth = HikvisionDigestAuth(username="user", password="pass")
    with pytest.raises(DigestAuthError):
        auth.authorization_header("GET", "/x")


def test_clear_drops_challenge() -> None:
    """clear() forgets the cached challenge."""
    auth = HikvisionDigestAuth(username="user", password="pass")
    auth.handle_401(_rfc_challenge("MD5"))
    assert auth.has_challenge
    auth.clear()
    assert not auth.has_challenge
