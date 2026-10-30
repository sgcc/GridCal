"""Fail-closed public-report string checks; raw evidence stays local."""

from collections.abc import Mapping
import re


_PRIVATE = re.compile(
    r"(?:sid:\[|(?:/Users/|/home/|/private/|file://)|"
    r"(?:[A-Za-z]:\\)|(?:\\\\))")


def assert_public_payload(payload: object, *, forbidden_tokens: tuple[str, ...] = ()) -> None:
    """Reject path, StableId and caller-supplied source tokens recursively."""
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            assert_public_payload(key, forbidden_tokens=forbidden_tokens)
            assert_public_payload(value, forbidden_tokens=forbidden_tokens)
    elif isinstance(payload, (tuple, list)):
        for value in payload:
            assert_public_payload(value, forbidden_tokens=forbidden_tokens)
    elif isinstance(payload, str):
        if _PRIVATE.search(payload) or any(
                token and token in payload for token in forbidden_tokens):
            raise ValueError("Public report contains private source identity")
