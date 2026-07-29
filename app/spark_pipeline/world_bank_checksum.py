from __future__ import annotations

from pyspark.sql import DataFrame

from app.world_bank import OBSERVATION_CHECKSUM_COLUMNS, observation_checksum


def spark_observation_checksum(indicators: DataFrame) -> str:
    """Apply the shared protocol after Spark has parsed source scalar types.

    The snapshot is intentionally small, so moving only the checksum projection
    to the driver is preferable to maintaining a second, subtly different CSV
    canonicalizer in Spark SQL.
    """
    rows = (
        row.asDict(recursive=True)
        for row in indicators.select(*OBSERVATION_CHECKSUM_COLUMNS).toLocalIterator()
    )
    return observation_checksum(rows)
