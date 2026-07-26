from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# Support the documented direct-file invocation as well as ``python -m``.
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from app.logging_config import (  # noqa: E402
    configure_logging,
    sanitized_exception_info,
)
from app.spark_pipeline.identifiers import valid_batch_id  # noqa: E402
from app.spark_pipeline.pipeline import (  # noqa: E402
    QualityFailure,
    run_benchmark,
    run_ingest_profile,
)

logger = logging.getLogger(__name__)


def _identifier(value: str) -> str:
    return valid_batch_id(value)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create immutable Spark Bronze data and profiling evidence."
    )
    parser.add_argument("--bronze-root", type=Path, default=Path("data/bronze"))
    parser.add_argument("--output-root", type=Path, default=Path("outputs/spark"))
    parser.add_argument(
        "--exact-distinct-max-rows",
        type=int,
        default=int(os.getenv("SPARK_EXACT_DISTINCT_MAX_ROWS", "1000000")),
    )
    parser.add_argument(
        "--evidence-path",
        type=Path,
        default=Path("reports/spark/evidence.json"),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest = subparsers.add_parser("ingest-profile")
    ingest.add_argument("--source-batch-id", required=True, type=_identifier)
    ingest.add_argument("--ingestion-id", required=True, type=_identifier)
    ingest.add_argument("--benchmark-run-id", required=True, type=_identifier)
    ingest.add_argument("--source-root", type=Path, default=Path("data/source"))
    ingest.add_argument("--curated-root", type=Path, default=Path("data/curated"))

    benchmark = subparsers.add_parser("benchmark")
    benchmark.add_argument("--ingestion-id", required=True, type=_identifier)
    benchmark.add_argument("--benchmark-run-id", required=True, type=_identifier)
    return parser.parse_args()


def main() -> None:
    configure_logging("spark-bronze", os.getenv("LOG_LEVEL", "INFO"))
    args = parse_args()
    try:
        if args.command == "ingest-profile":
            run_ingest_profile(
                source_batch_id=args.source_batch_id,
                ingestion_id=args.ingestion_id,
                benchmark_run_id=args.benchmark_run_id,
                source_root=args.source_root,
                bronze_root=args.bronze_root,
                curated_root=args.curated_root,
                output_root=args.output_root,
                evidence_path=args.evidence_path,
                exact_distinct_max_rows=args.exact_distinct_max_rows,
            )
        else:
            run_benchmark(
                ingestion_id=args.ingestion_id,
                benchmark_run_id=args.benchmark_run_id,
                bronze_root=args.bronze_root,
                output_root=args.output_root,
                evidence_path=args.evidence_path,
            )
    except QualityFailure as exc:
        logger.warning("spark_quality_failed", extra={"detail": str(exc)})
        raise SystemExit(2) from exc
    except Exception as exc:
        logger.exception(
            "spark_pipeline_failed",
            extra={"error_type": type(exc).__name__},
            exc_info=sanitized_exception_info(exc),
        )
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
