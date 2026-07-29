from __future__ import annotations

import json
import unittest

from project_glacier_orchestrator.canonical import (
    MAX_JSON_DEPTH,
    MAX_SAFE_INTEGER,
    canonical_json_bytes,
    strict_json_loads,
)
from project_glacier_orchestrator.errors import OrchestratorError


class CanonicalJsonTests(unittest.TestCase):
    def assert_error(self, code: str, function, *args, **kwargs) -> None:
        with self.assertRaises(OrchestratorError) as caught:
            function(*args, **kwargs)
        self.assertEqual(code, caught.exception.code)

    def test_duplicate_keys_are_rejected_before_object_construction(self) -> None:
        self.assert_error("DUPLICATE_JSON_KEY", strict_json_loads, '{"x":1,"x":2}')

    def test_safe_integer_boundaries_and_boolean_type_are_distinct(self) -> None:
        parsed = strict_json_loads(
            json.dumps({"maximum": MAX_SAFE_INTEGER, "minimum": -MAX_SAFE_INTEGER, "flag": True})
        )
        self.assertEqual(MAX_SAFE_INTEGER, parsed["maximum"])
        self.assertEqual(-MAX_SAFE_INTEGER, parsed["minimum"])
        self.assertIs(parsed["flag"], True)
        self.assert_error(
            "INTEGER_OUT_OF_RANGE",
            strict_json_loads,
            f'{{"value":{MAX_SAFE_INTEGER + 1}}}',
        )
        self.assert_error(
            "INTEGER_OUT_OF_RANGE",
            strict_json_loads,
            f'{{"value":{-MAX_SAFE_INTEGER - 1}}}',
        )
        self.assert_error("INTEGER_OUT_OF_RANGE", strict_json_loads, '{"value":12345678901234567}')

    def test_all_float_spellings_are_rejected(self) -> None:
        for text in ('{"value":1.0}', '{"value":1e0}', '{"value":-0.5}'):
            with self.subTest(text=text):
                self.assert_error("FLOAT_NOT_ALLOWED", strict_json_loads, text)
        for text in ('{"value":NaN}', '{"value":Infinity}', '{"value":-Infinity}'):
            with self.subTest(text=text):
                self.assert_error("INVALID_JSON_NUMBER", strict_json_loads, text)
        self.assert_error("FLOAT_NOT_ALLOWED", canonical_json_bytes, {"value": 1.0})

    def test_depth_limit_is_enforced(self) -> None:
        value: object = "leaf"
        for _ in range(MAX_JSON_DEPTH + 1):
            value = [value]
        self.assert_error("JSON_TOO_DEEP", strict_json_loads, json.dumps({"value": value}))

    def test_unicode_values_are_allowed_but_schema_keys_and_surrogates_are_not(self) -> None:
        parsed = strict_json_loads('{"label":"snow 雪 and 😀"}')
        self.assertEqual("snow 雪 and 😀", parsed["label"])
        self.assertEqual(
            b'{"label":"snow \xe9\x9b\xaa and \xf0\x9f\x98\x80"}',
            canonical_json_bytes(parsed),
        )
        self.assert_error("NON_ASCII_SCHEMA_KEY", strict_json_loads, '{"é":1}')
        self.assert_error("INVALID_UNICODE", strict_json_loads, '{"value":"\ud800"}')
        self.assert_error("INVALID_UNICODE", canonical_json_bytes, {"value": "\udfff"})

    def test_canonical_encoding_sorts_keys_and_preserves_json_types(self) -> None:
        self.assertEqual(
            b'{"a":null,"b":true,"c":false,"n":0}',
            canonical_json_bytes({"n": 0, "c": False, "b": True, "a": None}),
        )

    def test_root_must_be_exactly_one_object(self) -> None:
        self.assert_error("JSON_OBJECT_REQUIRED", strict_json_loads, "[]")
        self.assert_error("MALFORMED_JSON", strict_json_loads, "{} {}")
        self.assert_error("JSON_BOM_NOT_ALLOWED", strict_json_loads, "\ufeff{}")

    def test_byte_limit_is_utf8_aware(self) -> None:
        self.assert_error("PACKET_TOO_LARGE", strict_json_loads, '{"x":"雪"}', max_bytes=9)


if __name__ == "__main__":
    unittest.main()
