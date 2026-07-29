from __future__ import annotations

import argparse
import unittest

from scripts.update_covid_denominator import (
    DenominatorUpdateError,
    _version,
    apply_update,
    rollback_version,
)


class DenominatorUpdateTests(unittest.TestCase):
    def test_version_rejects_sql_or_path_syntax(self) -> None:
        self.assertEqual(_version("v2-reviewed"), "v2-reviewed")
        for invalid in ("", "../../v2", "v2;DROP", " version"):
            with self.assertRaises(argparse.ArgumentTypeError):
                _version(invalid)

    def test_mutating_commands_require_explicit_approval(self) -> None:
        with self.assertRaisesRegex(DenominatorUpdateError, "--approve"):
            apply_update(None, {"accepted": True}, approve=False)
        with self.assertRaisesRegex(DenominatorUpdateError, "--approve"):
            rollback_version(None, "v1", approve=False)


if __name__ == "__main__":
    unittest.main()
