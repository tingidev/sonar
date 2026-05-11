"""Streaming progress output and final summary for `sonar scan`."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sonar.engine.describe import DescribeProgress
from sonar.index.bundle import ContextBundle


@dataclass(frozen=True)
class FailedTable:
    schema: str
    name: str
    error_reason: str


def print_discovery(table_count: int, schema_count: int) -> None:
    print(
        f"Discovered {table_count} tables in {schema_count} schemas. "
        "Generating semantic descriptions..."
    )


def print_table_progress(event: DescribeProgress) -> None:
    prefix = f"[{event.index + 1}/{event.total}] {event.schema}.{event.table}"
    if event.event == "started":
        print(f"{prefix} ...")
        return

    elapsed_s = (event.elapsed_ms or 0) / 1000
    if event.event == "ok":
        print(f"{prefix} ... ok ({elapsed_s:.1f}s)")
    elif event.event == "parse_retry":
        print(f"{prefix} ... ok via retry ({elapsed_s:.1f}s)")
    else:
        reason = event.error_reason or "unknown error"
        print(f"{prefix} ... failed: {reason} ({elapsed_s:.1f}s)")


def print_scan_summary(
    *,
    database_label: str,
    bundle: ContextBundle,
    bundle_dir: Path,
    elapsed_seconds: float,
    failures: tuple[FailedTable, ...] = (),
    cross_database_dropped: int = 0,
    cross_database_label: str | None = None,
    cross_dataset_dropped: int = 0,
) -> None:
    total = len(bundle.tables)
    failure_count = len(failures)
    success_count = total - failure_count

    print(f"\nScan complete in {elapsed_seconds:.1f}s")
    print(
        f"  {database_label}: {total} tables, "
        f"{len(bundle.relationships)} relationships"
    )
    print(f"  Bundle written to {bundle_dir}")
    print(f"  Descriptions: {success_count} ok, {failure_count} failed")

    if failures:
        print("\nFailed tables:")
        for failure in failures:
            print(f"  {failure.schema}.{failure.name}: {failure.error_reason}")
        print(
            "\nTo retry, re-run `sonar scan`. Failed tables are not cached and "
            "will be re-attempted on the next run."
        )

    if cross_database_dropped:
        bound = cross_database_label or database_label
        print(
            f"\n{cross_database_dropped} foreign keys reference tables outside "
            f"database {bound} and were excluded"
        )
    if cross_dataset_dropped:
        print(
            f"\n{cross_dataset_dropped} foreign keys reference tables outside "
            f"their dataset and were excluded"
        )
