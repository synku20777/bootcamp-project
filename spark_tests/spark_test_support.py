from __future__ import annotations

import warnings

import pyspark
from pyspark.sql import SparkSession


def install_pyspark_socket_warning_filter() -> None:
    version = tuple(int(part) for part in pyspark.__version__.split(".")[:2])
    if version < (4, 2):
        # PySpark 3.5 intentionally lets result-transfer sockets close during
        # garbage collection (SPARK-54247). Limit the compatibility filter to
        # that exact upstream warning so project-owned ResourceWarnings remain visible.
        warnings.filterwarnings(
            "ignore",
            message=r"unclosed <socket\.socket.*>",
            category=ResourceWarning,
            module=r"socket",
        )


def stop_test_spark_session(spark: SparkSession) -> None:
    try:
        spark.catalog.clearCache()
    finally:
        # SparkSession.stop() in PySpark 3.5 clears the JVM default/active
        # sessions and their corresponding Python registries as one operation.
        spark.stop()
