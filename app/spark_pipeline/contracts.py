"""Shared pipeline contracts kept free of orchestration dependencies.

The execution harness and extracted stages both need the same controlled error
types and benchmark result shape.  Keeping those definitions in this small
leaf module prevents circular imports while ``pipeline`` re-exports them for
callers that depend on the original import path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pyspark.sql import DataFrame


class SparkPipelineError(RuntimeError):
    """A controlled Spark pipeline failure."""


class QualityFailure(SparkPipelineError):
    """Quality rules blocked downstream publication."""


class SourceValidationError(QualityFailure):
    """Source files or their manifest failed the pre-Spark contract."""


@dataclass(frozen=True, slots=True)
class BenchmarkSuiteResult:
    """Outputs handed from benchmark orchestration back to the job harness.

    ``curated`` intentionally remains a lazy DataFrame: the harness owns the
    subsequent publication action and therefore controls when Spark evaluates
    that plan.  The remaining fields are driver-side evidence documents.
    """

    benchmarks: list[dict[str, Any]]
    curated: DataFrame
    layout: dict[str, Any]
    plans: dict[str, Any]
    context_equivalence: dict[str, Any]
    broadcast_decisions: dict[str, bool]
    skew: dict[str, Any]
