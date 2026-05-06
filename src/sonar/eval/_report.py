"""Report formatters — human-readable text and structured JSON for all eval modes."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from typing import Any

from sonar.eval._types import RelationshipEdge
from sonar.eval.descriptions import DescriptionQualityReport, TableScore
from sonar.eval.diff import DescriptionChange, DiffReport
from sonar.eval.quality import ConfidenceSummary, QualityReport
from sonar.eval.relationships import RelationshipReport, TableBreakdown
from sonar.eval.search import SearchReport

# ---------------------------------------------------------------------------
# Common envelope + JSON
# ---------------------------------------------------------------------------


def _envelope(mode: str, bundle: str, metrics: dict[str, Any], details: Any) -> dict[str, Any]:
    return {"mode": mode, "bundle": bundle, "metrics": metrics, "details": details}


def _to_jsonable(obj: Any) -> Any:
    if is_dataclass(obj):
        return _to_jsonable(asdict(obj))
    if isinstance(obj, dict):
        return {str(k): _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    return obj


def _dump_json(payload: dict[str, Any]) -> str:
    return json.dumps(_to_jsonable(payload), indent=2, sort_keys=False)


# ---------------------------------------------------------------------------
# Quality report
# ---------------------------------------------------------------------------


def format_quality_human(report: QualityReport) -> str:
    lines: list[str] = []
    lines.append(f"Bundle: {report.bundle_dir}")
    lines.append(f"Tables: {report.table_count}")
    lines.append(
        f"Description coverage: {_pct(report.description_coverage)} "
        f"({report.description_present}/{report.table_count} present, "
        f"{report.description_null} null)"
    )
    lines.append(
        f"Relationship coverage: {_pct(report.relationship_coverage)} "
        f"({report.relationship_count} edges)"
    )
    lines.append(f"Orphan tables: {len(report.orphan_tables)}")
    if report.orphan_tables:
        for schema, name in report.orphan_tables[:10]:
            lines.append(f"  - {schema}.{name}")
        if len(report.orphan_tables) > 10:
            lines.append(f"  ... +{len(report.orphan_tables) - 10} more")

    lines.append("")
    lines.append("Graph reachability:")
    lines.append(f"  Components: {report.graph.components}")
    lines.append(
        f"  Largest component: {report.graph.largest_component_size} tables "
        f"({_pct(report.graph.largest_component_fraction)})"
    )
    lines.append(f"  Mean reachable: {report.graph.mean_reachable:.1f} tables")

    lines.append("")
    lines.append("PII distribution (columns):")
    for risk in ("none", "low", "medium", "high"):
        count = report.pii_distribution.get(risk, 0)
        lines.append(f"  {risk}: {count}")

    if report.table_confidence is not None:
        lines.append("")
        lines.append("Table confidence:")
        lines.append(f"  {_format_confidence(report.table_confidence)}")
    if report.column_confidence is not None:
        lines.append("Column confidence:")
        lines.append(f"  {_format_confidence(report.column_confidence)}")

    return "\n".join(lines)


def format_quality_json(report: QualityReport) -> str:
    metrics = {
        "table_count": report.table_count,
        "description_coverage": report.description_coverage,
        "relationship_coverage": report.relationship_coverage,
        "relationship_count": report.relationship_count,
        "orphan_count": len(report.orphan_tables),
        "components": report.graph.components,
        "largest_component_size": report.graph.largest_component_size,
        "largest_component_fraction": report.graph.largest_component_fraction,
        "mean_reachable": report.graph.mean_reachable,
        "pii_distribution": dict(report.pii_distribution),
        "table_confidence": _confidence_to_dict(report.table_confidence),
        "column_confidence": _confidence_to_dict(report.column_confidence),
    }
    details = {
        "orphan_tables": [{"schema": s, "name": n} for (s, n) in report.orphan_tables],
    }
    return _dump_json(_envelope("quality", report.bundle_dir, metrics, details))


def _confidence_to_dict(c: ConfidenceSummary | None) -> dict[str, float] | None:
    if c is None:
        return None
    return {
        "mean": c.mean,
        "minimum": c.minimum,
        "maximum": c.maximum,
        "count": c.count,
    }


def _format_confidence(c: ConfidenceSummary) -> str:
    return f"mean={c.mean:.2f}  min={c.minimum:.2f}  max={c.maximum:.2f}  n={c.count}"


# ---------------------------------------------------------------------------
# Relationship report
# ---------------------------------------------------------------------------


def format_relationships_human(report: RelationshipReport, label: str) -> str:
    lines: list[str] = []
    lines.append(f"Database: {label}")
    lines.append(
        f"Declared FKs: {report.declared_count}  "
        f"Inferred edges: {report.inferred_count}  "
        f"Matched: {report.matched_count}"
    )
    lines.append(
        f"Recall: {_fmt_metric(report.recall)}  "
        f"Precision: {_fmt_metric(report.precision)}  "
        f"F1: {_fmt_metric(report.f1)}"
    )

    if report.missed:
        lines.append("")
        lines.append(f"Missed declared FKs ({len(report.missed)}):")
        for edge in report.missed[:20]:
            lines.append(f"  - {_edge_text(edge)}")
        if len(report.missed) > 20:
            lines.append(f"  ... +{len(report.missed) - 20} more")

    if report.false_positive:
        lines.append("")
        lines.append(f"False-positive inferences ({len(report.false_positive)}):")
        for edge in report.false_positive[:20]:
            lines.append(f"  - {_edge_text(edge)}")
        if len(report.false_positive) > 20:
            lines.append(f"  ... +{len(report.false_positive) - 20} more")

    if report.per_table:
        lines.append("")
        lines.append("Per-table breakdown (declared / matched / missed / false-positive):")
        for tb in report.per_table:
            lines.append(
                f"  {tb.schema}.{tb.name}: "
                f"{tb.declared} / {tb.inferred_correct} / {tb.missed} / {tb.false_positive}"
            )

    return "\n".join(lines)


def format_relationships_json(report: RelationshipReport, label: str) -> str:
    metrics = {
        "declared_count": report.declared_count,
        "inferred_count": report.inferred_count,
        "matched_count": report.matched_count,
        "recall": report.recall,
        "precision": report.precision,
        "f1": report.f1,
    }
    details = {
        "missed": [_edge_dict(e) for e in report.missed],
        "false_positive": [_edge_dict(e) for e in report.false_positive],
        "per_table": [_breakdown_dict(tb) for tb in report.per_table],
    }
    return _dump_json(_envelope("relationships", label, metrics, details))


def _edge_text(edge: RelationshipEdge) -> str:
    return (
        f"{edge.source_schema}.{edge.source_table}.{edge.source_column}"
        f" -> {edge.target_schema}.{edge.target_table}.{edge.target_column}"
    )


def _edge_dict(edge: RelationshipEdge) -> dict[str, str]:
    return {
        "source_schema": edge.source_schema,
        "source_table": edge.source_table,
        "source_column": edge.source_column,
        "target_schema": edge.target_schema,
        "target_table": edge.target_table,
        "target_column": edge.target_column,
    }


def _breakdown_dict(tb: TableBreakdown) -> dict[str, Any]:
    return {
        "schema": tb.schema,
        "name": tb.name,
        "declared": tb.declared,
        "inferred_correct": tb.inferred_correct,
        "missed": tb.missed,
        "false_positive": tb.false_positive,
    }


# ---------------------------------------------------------------------------
# Search report
# ---------------------------------------------------------------------------


def format_search_human(report: SearchReport, bundle_dir: str) -> str:
    lines: list[str] = []
    lines.append(f"Bundle: {bundle_dir}")
    lines.append(f"Queries: {report.query_count}")
    lines.append(
        f"MRR: {report.mean_reciprocal_rank:.3f}  "
        f"Mean precision@k: {report.mean_precision_at_k:.3f}  "
        f"Mean recall@k: {report.mean_recall_at_k:.3f}"
    )
    lines.append("")
    lines.append("Per-query results:")
    for q in report.per_query:
        hits = sum(1 for r in q.returned if r in set(q.expected))
        lines.append(
            f"  '{q.query}': "
            f"P@k={q.precision_at_k:.2f} R@k={q.recall_at_k:.2f} RR={q.reciprocal_rank:.2f}  "
            f"({hits}/{len(q.expected)} expected)"
        )
    return "\n".join(lines)


def format_search_json(report: SearchReport, bundle_dir: str) -> str:
    metrics = {
        "query_count": report.query_count,
        "mean_reciprocal_rank": report.mean_reciprocal_rank,
        "mean_precision_at_k": report.mean_precision_at_k,
        "mean_recall_at_k": report.mean_recall_at_k,
    }
    details = [
        {
            "query": q.query,
            "expected": list(q.expected),
            "returned": list(q.returned),
            "precision_at_k": q.precision_at_k,
            "recall_at_k": q.recall_at_k,
            "reciprocal_rank": q.reciprocal_rank,
        }
        for q in report.per_query
    ]
    return _dump_json(_envelope("search", bundle_dir, metrics, details))


# ---------------------------------------------------------------------------
# Diff report
# ---------------------------------------------------------------------------


def format_diff_human(report: DiffReport, current_dir: str, other_dir: str) -> str:
    lines: list[str] = []
    lines.append(f"Current: {current_dir}")
    lines.append(f"Other:   {other_dir}")

    lines.append("")
    lines.append(f"Tables added: {len(report.tables_added)}")
    for schema, name in report.tables_added[:20]:
        lines.append(f"  + {schema}.{name}")
    lines.append(f"Tables removed: {len(report.tables_removed)}")
    for schema, name in report.tables_removed[:20]:
        lines.append(f"  - {schema}.{name}")

    lines.append("")
    declared_added = [r for r in report.relationships_added if r.kind == "declared"]
    inferred_added = [r for r in report.relationships_added if r.kind == "inferred"]
    declared_removed = [r for r in report.relationships_removed if r.kind == "declared"]
    inferred_removed = [r for r in report.relationships_removed if r.kind == "inferred"]
    lines.append(
        f"Relationships added: {len(report.relationships_added)} "
        f"(declared={len(declared_added)}, inferred={len(inferred_added)})"
    )
    for edge in report.relationships_added[:10]:
        lines.append(f"  + [{edge.kind}] {_diff_edge_text(edge)}")
    lines.append(
        f"Relationships removed: {len(report.relationships_removed)} "
        f"(declared={len(declared_removed)}, inferred={len(inferred_removed)})"
    )
    for edge in report.relationships_removed[:10]:
        lines.append(f"  - [{edge.kind}] {_diff_edge_text(edge)}")

    lines.append("")
    lines.append(f"Descriptions added (null -> present): {len(report.descriptions_added)}")
    for schema, name in report.descriptions_added[:20]:
        lines.append(f"  + {schema}.{name}")
    lines.append(f"Descriptions removed (present -> null): {len(report.descriptions_removed)}")
    for schema, name in report.descriptions_removed[:20]:
        lines.append(f"  - {schema}.{name}")
    lines.append(f"Descriptions changed: {len(report.descriptions_changed)}")
    for change in report.descriptions_changed[:20]:
        flags: list[str] = []
        if change.text_changed:
            flags.append("text")
        if change.grain_changed:
            flags.append("grain")
        if change.confidence_delta != 0.0:
            flags.append(f"conf{change.confidence_delta:+.2f}")
        if change.domain_hints_added or change.domain_hints_removed:
            flags.append("hints")
        lines.append(f"  ~ {change.schema}.{change.name} [{','.join(flags) or 'unchanged'}]")

    return "\n".join(lines)


def format_diff_json(report: DiffReport, current_dir: str, other_dir: str) -> str:
    metrics = {
        "tables_added": len(report.tables_added),
        "tables_removed": len(report.tables_removed),
        "relationships_added": len(report.relationships_added),
        "relationships_removed": len(report.relationships_removed),
        "descriptions_added": len(report.descriptions_added),
        "descriptions_removed": len(report.descriptions_removed),
        "descriptions_changed": len(report.descriptions_changed),
    }
    details = {
        "other": other_dir,
        "tables_added": [{"schema": s, "name": n} for s, n in report.tables_added],
        "tables_removed": [{"schema": s, "name": n} for s, n in report.tables_removed],
        "relationships_added": [_diff_edge_dict(e) for e in report.relationships_added],
        "relationships_removed": [_diff_edge_dict(e) for e in report.relationships_removed],
        "descriptions_added": [{"schema": s, "name": n} for s, n in report.descriptions_added],
        "descriptions_removed": [{"schema": s, "name": n} for s, n in report.descriptions_removed],
        "descriptions_changed": [_change_dict(c) for c in report.descriptions_changed],
    }
    return _dump_json(_envelope("diff", current_dir, metrics, details))


def _diff_edge_text(edge: RelationshipEdge) -> str:
    return (
        f"{edge.source_schema}.{edge.source_table}.{edge.source_column}"
        f" -> {edge.target_schema}.{edge.target_table}.{edge.target_column}"
    )


def _diff_edge_dict(edge: RelationshipEdge) -> dict[str, str]:
    return {
        "source_schema": edge.source_schema,
        "source_table": edge.source_table,
        "source_column": edge.source_column,
        "target_schema": edge.target_schema,
        "target_table": edge.target_table,
        "target_column": edge.target_column,
        "kind": edge.kind,
    }


def _change_dict(change: DescriptionChange) -> dict[str, Any]:
    return {
        "schema": change.schema,
        "name": change.name,
        "text_changed": change.text_changed,
        "grain_changed": change.grain_changed,
        "confidence_delta": change.confidence_delta,
        "domain_hints_added": list(change.domain_hints_added),
        "domain_hints_removed": list(change.domain_hints_removed),
    }


# ---------------------------------------------------------------------------
# Description quality report
# ---------------------------------------------------------------------------


def format_descriptions_human(report: DescriptionQualityReport, bundle_dir: str) -> str:
    lines: list[str] = []
    lines.append(f"Bundle: {bundle_dir}")
    lines.append(
        f"Scored: {report.scored_count}  "
        f"Skipped (null): {report.skipped_null}  "
        f"Judge failures: {report.judge_failures}"
    )
    if report.scored_count > 0:
        lines.append(
            f"Mean accuracy: {report.mean_accuracy:.2f}  "
            f"Mean completeness: {report.mean_completeness:.2f}  "
            f"Mean specificity: {report.mean_specificity:.2f}"
        )
    if report.flagged:
        lines.append("")
        lines.append(f"Flagged tables ({len(report.flagged)}, any dimension < 0.5):")
        for s in report.flagged:
            lines.append(_score_line(s))

    lines.append("")
    lines.append(
        "Note: same-model judge — use for relative comparison between runs, "
        "not absolute quality."
    )
    return "\n".join(lines)


def format_descriptions_json(report: DescriptionQualityReport, bundle_dir: str) -> str:
    metrics = {
        "scored_count": report.scored_count,
        "skipped_null": report.skipped_null,
        "judge_failures": report.judge_failures,
        "mean_accuracy": report.mean_accuracy,
        "mean_completeness": report.mean_completeness,
        "mean_specificity": report.mean_specificity,
    }
    details = {
        "flagged": [_score_dict(s) for s in report.flagged],
        "per_table": [_score_dict(s) for s in report.per_table],
    }
    return _dump_json(_envelope("descriptions", bundle_dir, metrics, details))


def _score_line(s: TableScore) -> str:
    return (
        f"  {s.schema}.{s.name}: "
        f"acc={s.accuracy:.2f}  "
        f"comp={s.completeness:.2f}  "
        f"spec={s.specificity:.2f}"
    )


def _score_dict(s: TableScore) -> dict[str, Any]:
    return {
        "schema": s.schema,
        "name": s.name,
        "accuracy": s.accuracy,
        "completeness": s.completeness,
        "specificity": s.specificity,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pct(fraction: float) -> str:
    return f"{fraction * 100:.1f}%"


def _fmt_metric(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.3f}"
