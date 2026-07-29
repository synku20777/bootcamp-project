from __future__ import annotations

import unittest
from copy import deepcopy
from decimal import Decimal

from app.world_bank import (
    WorldBankValidationError,
    canonical_observation_bytes,
    duplicate_report,
    normalize_decimal,
    observation_checksum,
    validate_candidate,
)


def _row(value: object = "123.400000000") -> dict[str, object]:
    return {
        "SNAPSHOT_ID": "wdi2-fixture",
        "SOURCE_ENTITY_CODE": "LVA",
        "CANONICAL_ISO2": "LV",
        "CANONICAL_ISO3": "LVA",
        "COUNTRY_NAME": "Latvia",
        "CODE_SYSTEM": "ISO-3166-1",
        "ENTITY_TYPE": "economy",
        "IS_AGGREGATE": False,
        "IDENTITY_MAPPING_STATUS": "matched_iso",
        "INDICATOR_CODE": "NY.GDP.PCAP.KD",
        "INDICATOR_NAME": "GDP per capita (constant 2015 US$)",
        "INDICATOR_UNIT": "constant 2015 US$",
        "SOURCE_ID": 2,
        "SOURCE_NAME": "World Development Indicators",
        "OBSERVATION_YEAR": 2019,
        "INDICATOR_VALUE": value,
        "SOURCE_LAST_UPDATED": "2026-07-01",
        "OBSERVATION_STATUS": None,
        "SOURCE_DECIMAL_PRECISION": 1,
        "RAW_VALUE_TEXT": str(value),
        "RETRIEVED_AT": "2026-07-29T12:00:00Z",
    }


class WorldBankCanonicalizationTests(unittest.TestCase):
    def test_golden_observation_checksum_is_platform_stable(self) -> None:
        self.assertEqual(
            observation_checksum([_row()]),
            "30ecf2520c6e69c69fe991ef1ef2f902e026810c3817aa54dc07e6a441b12e0d",
        )

    def test_decimal_canonicalization_collapses_equivalent_values(self) -> None:
        self.assertEqual(normalize_decimal("123.4"), "123.4")
        self.assertEqual(normalize_decimal(Decimal("123.400000000")), "123.4")
        self.assertEqual(normalize_decimal("-0.000000000"), "0")

    def test_observation_checksum_is_order_and_newline_independent(self) -> None:
        first = _row()
        second = _row("9")
        second["INDICATOR_CODE"] = "SP.POP.TOTL"
        second["INDICATOR_NAME"] = "Population, total"
        second["INDICATOR_UNIT"] = "people"
        self.assertEqual(
            observation_checksum([first, second]),
            observation_checksum([second, first]),
        )
        self.assertNotIn(b"\r\n", canonical_observation_bytes([first]))
        self.assertTrue(canonical_observation_bytes([first]).endswith(b"\n"))

    def test_duplicate_report_separates_conflicting_values(self) -> None:
        exact = deepcopy(_row())
        conflicting = deepcopy(_row("456"))
        report = duplicate_report([_row(), exact, conflicting])
        self.assertEqual(
            report["conflicting"],
            [
                {
                    "key": [
                        "wdi2-fixture",
                        "LVA",
                        "NY.GDP.PCAP.KD",
                        2019,
                    ],
                    "row_count": 3,
                }
            ],
        )
        self.assertEqual(report["exact"], [])

    def test_candidate_rejects_every_duplicate(self) -> None:
        with self.assertRaisesRegex(WorldBankValidationError, "duplicate"):
            validate_candidate([_row(), deepcopy(_row())])

    def test_candidate_rejects_conflicting_identity(self) -> None:
        row = _row()
        row["IDENTITY_MAPPING_STATUS"] = "conflicting_identity"
        with self.assertRaisesRegex(WorldBankValidationError, "conflicting identity"):
            validate_candidate([row])


if __name__ == "__main__":
    unittest.main()
