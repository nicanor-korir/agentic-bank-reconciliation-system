import pytest

from recon.hashing import (
    ZERO_HASH,
    canonical_json,
    chain_hash,
    content_hash,
    verify_chain,
)


def test_key_order_does_not_change_the_hash():
    assert content_hash({"b": 1, "a": 2}) == content_hash({"a": 2, "b": 1})


def test_canonical_json_has_no_incidental_whitespace():
    assert canonical_json({"a": 1, "b": [1, 2]}) == '{"a":1,"b":[1,2]}'


def test_nan_is_rejected():
    with pytest.raises(ValueError):
        canonical_json({"a": float("nan")})


def test_unserialisable_type_is_rejected():
    with pytest.raises(TypeError):
        canonical_json({"a": object()})


def _build_chain(payloads):
    links, prev = [], ZERO_HASH
    for p in payloads:
        h = chain_hash(prev, p)
        links.append((prev, h, p))
        prev = h
    return links


def test_intact_chain_verifies():
    assert verify_chain(_build_chain([{"n": i} for i in range(5)])) is None


def test_tampered_payload_is_detected_at_its_index():
    links = _build_chain([{"n": i} for i in range(5)])
    prev, h, _ = links[2]
    links[2] = (prev, h, {"n": 99})
    assert verify_chain(links) == 2


def test_reordered_chain_is_detected():
    links = _build_chain([{"n": i} for i in range(5)])
    links[1], links[2] = links[2], links[1]
    assert verify_chain(links) == 1


def test_chain_requires_a_full_length_prev_hash():
    with pytest.raises(ValueError, match="64 hex"):
        chain_hash("abc", {"n": 1})
