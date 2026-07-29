from __future__ import annotations

import csv
import hashlib
import io
from collections.abc import Iterable, Mapping
from typing import Any

from app.world_bank import NULL_TOKEN, normalize_decimal, normalize_text

COUNTRY_CONTEXT_FINGERPRINT_COLUMNS = (
    "ISO3",
    "POPULATION_2020_CONTEXT",
    "POPULATION_DENSITY_2019",
    "POPULATION_AGE_65_PLUS_PCT_2019",
    "REAL_GDP_PER_CAPITA_2019",
    "HEALTH_EXPENDITURE_PER_CAPITA_PPP_2019",
    "SNAPSHOT_ID",
)
_DECIMAL_COLUMNS = set(COUNTRY_CONTEXT_FINGERPRINT_COLUMNS[1:-1])


def country_context_fingerprint(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Fingerprint the cross-engine country-context projection, not SQL layout."""
    ordered = sorted(rows, key=lambda row: normalize_text(row.get("ISO3")))
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(COUNTRY_CONTEXT_FINGERPRINT_COLUMNS)
    for row in ordered:
        values = []
        for column in COUNTRY_CONTEXT_FINGERPRINT_COLUMNS:
            value = row.get(column)
            normalized = (
                normalize_decimal(value)
                if column in _DECIMAL_COLUMNS
                else normalize_text(value)
            )
            values.append(normalized if normalized else NULL_TOKEN)
        writer.writerow(values)
    payload = stream.getvalue().encode("utf-8")
    return {
        "protocol": "country-context-csv-sha256-v1",
        "row_count": len(ordered),
        "content_sha256": hashlib.sha256(payload).hexdigest(),
        "canonical_bytes": len(payload),
    }
