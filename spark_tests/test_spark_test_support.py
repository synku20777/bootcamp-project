from __future__ import annotations

import unittest
import warnings

from spark_tests.spark_test_support import install_pyspark_socket_warning_filter


class SparkTestSupportTests(unittest.TestCase):
    def test_socket_filter_is_limited_to_the_pyspark_transfer_warning(self) -> None:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", ResourceWarning)
            install_pyspark_socket_warning_filter()
            warnings.warn_explicit(
                "unclosed <socket.socket fd=9>",
                ResourceWarning,
                "socket.py",
                791,
                module="socket",
            )

        self.assertEqual(caught, [])

    def test_socket_filter_keeps_unrelated_resource_warnings_visible(self) -> None:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", ResourceWarning)
            install_pyspark_socket_warning_filter()
            warnings.warn("project-owned resource", ResourceWarning, stacklevel=1)

        self.assertEqual(len(caught), 1)
        self.assertEqual(str(caught[0].message), "project-owned resource")


if __name__ == "__main__":
    unittest.main()
