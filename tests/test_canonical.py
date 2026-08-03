"""Canonicalization conformance (§7.1, §13 "Canonicalization")."""

from __future__ import annotations

import json

import pytest

from federated_agent_web.canonical import (
    CanonicalizationError,
    canonical_bytes,
    parse_strict,
)

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]


class TestStrictParseRejections:
    def test_duplicate_member_rejected(self):
        with pytest.raises(CanonicalizationError, match="duplicate"):
            parse_strict(b'{"a": 1, "a": 2}')

    def test_duplicate_nested_member_rejected(self):
        with pytest.raises(CanonicalizationError, match="duplicate"):
            parse_strict(b'{"outer": {"k": 1, "k": 2}}')

    def test_nan_rejected(self):
        with pytest.raises(CanonicalizationError, match="non-finite"):
            parse_strict(b'{"x": NaN}')

    def test_infinity_rejected(self):
        with pytest.raises(CanonicalizationError, match="non-finite"):
            parse_strict(b'{"x": Infinity}')

    def test_negative_infinity_rejected(self):
        with pytest.raises(CanonicalizationError, match="non-finite"):
            parse_strict(b'{"x": -Infinity}')

    def test_negative_zero_float_rejected(self):
        with pytest.raises(CanonicalizationError, match="negative zero"):
            parse_strict(b'{"x": -0.0}')

    def test_negative_zero_exponent_rejected(self):
        with pytest.raises(CanonicalizationError, match="negative zero"):
            parse_strict(b'{"x": -0e10}')

    def test_negative_zero_int_literal_rejected(self):
        with pytest.raises(CanonicalizationError, match="negative zero"):
            parse_strict(b'{"x": -0}')

    def test_lone_surrogate_escape_rejected(self):
        with pytest.raises(CanonicalizationError):
            parse_strict('{"s": "\\ud800"}'.encode("utf-8"))

    def test_invalid_utf8_rejected(self):
        with pytest.raises(CanonicalizationError):
            parse_strict(b'{"s": "\xff\xfe"}')

    def test_out_of_domain_integer_rejected(self):
        with pytest.raises(CanonicalizationError, match="exceeds"):
            parse_strict(b'{"n": 9007199254740992}')

    def test_non_object_top_level_rejected(self):
        with pytest.raises(CanonicalizationError, match="object"):
            parse_strict(b'[1, 2]')


class TestCanonicalSerialization:
    def test_nested_key_order(self):
        assert canonical_bytes({"z": {"b": 2, "a": 1}, "y": [3, 1, 2]}) == b'{"y":[3,1,2],"z":{"a":1,"b":2}}'

    def test_unicode_preserved_as_utf8(self):
        assert canonical_bytes({"s": "héllo"}) == b'{"s":"h\xc3\xa9llo"}'

    def test_sort_keys_is_not_substitute(self):
        # json.dumps(sort_keys=True) emits floats with trailing ".0"; JCS must not.
        assert canonical_bytes({"b": 1.0, "a": 2}) == b'{"a":2,"b":1}'

    def test_exponent_forms(self):
        assert canonical_bytes(1e30) == b"1e+30"
        assert canonical_bytes(1e-7) == b"1e-7"
        assert canonical_bytes(0.1) == b"0.1"

    def test_int_within_domain(self):
        assert canonical_bytes(42) == b"42"

    def test_escaped_strings(self):
        assert canonical_bytes({"s": "a\nb"}) == b'{"s":"a\\nb"}'


class TestGoldenVectorReproduction:
    """Recompute canonical bytes from the static JSON fixtures (§13 vectors)."""

    def test_all_canonicalization_vectors(self):
        directory = ROOT / "vectors" / "canonicalization"
        cases = [p.stem for p in sorted(directory.glob("*.json"))]
        assert cases, "no canonicalization fixtures found"
        for name in cases:
            obj = json.loads((directory / f"{name}.json").read_text(encoding="utf-8"))
            expected_hex = (directory / f"{name}.canonical.hex").read_text().strip()
            assert canonical_bytes(obj).hex() == expected_hex, f"vector {name} diverged"
