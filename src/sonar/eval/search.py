"""Search relevance evaluation against curated YAML ground truth."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from sonar.index.bundle import ContextBundle
from sonar.search import search_tool


class GroundTruthError(Exception):
    """Raised when a ground-truth YAML file fails to parse or validate."""


@dataclass(frozen=True)
class GroundTruthQuery:
    query: str
    expected: tuple[str, ...]


@dataclass(frozen=True)
class QueryResult:
    query: str
    expected: tuple[str, ...]
    returned: tuple[str, ...]
    precision_at_k: float
    recall_at_k: float
    reciprocal_rank: float


@dataclass(frozen=True)
class SearchReport:
    query_count: int
    mean_reciprocal_rank: float
    mean_precision_at_k: float
    mean_recall_at_k: float
    per_query: tuple[QueryResult, ...]


def load_ground_truth(path: Path) -> list[GroundTruthQuery]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise GroundTruthError(f"failed to parse YAML at {path}: {exc}") from exc
    return parse_ground_truth(raw, source=str(path))


def parse_ground_truth(raw: object, *, source: str = "<input>") -> list[GroundTruthQuery]:
    if not isinstance(raw, dict) or "queries" not in raw:
        raise GroundTruthError(
            f"ground-truth file {source}: expected top-level mapping with a 'queries' key"
        )
    queries_raw = raw["queries"]
    if not isinstance(queries_raw, list) or not queries_raw:
        raise GroundTruthError(
            f"ground-truth file {source}: 'queries' must be a non-empty list"
        )
    out: list[GroundTruthQuery] = []
    for i, item in enumerate(queries_raw):
        if not isinstance(item, dict):
            raise GroundTruthError(
                f"ground-truth file {source}: query #{i} is not a mapping"
            )
        query = item.get("query")
        expected = item.get("expected")
        if not isinstance(query, str) or not query:
            raise GroundTruthError(
                f"ground-truth file {source}: query #{i} missing 'query' string"
            )
        if not isinstance(expected, list) or not all(isinstance(t, str) for t in expected):
            raise GroundTruthError(
                f"ground-truth file {source}: query #{i} ('{query}') "
                "'expected' must be a list of strings"
            )
        out.append(GroundTruthQuery(query=query, expected=tuple(expected)))
    return out


def evaluate_search(
    bundle: ContextBundle,
    ground_truth: list[GroundTruthQuery],
    *,
    limit: int = 20,
) -> SearchReport:
    per_query: list[QueryResult] = []
    for gt in ground_truth:
        results = search_tool(bundle, gt.query, limit=limit)
        returned = tuple(f"{r['schema']}.{r['table']}" for r in results)
        expected_set = set(gt.expected)

        if returned:
            hits = sum(1 for r in returned if r in expected_set)
            precision = hits / len(returned)
        else:
            precision = 0.0

        if expected_set:
            recall = sum(1 for e in expected_set if e in returned) / len(expected_set)
        else:
            recall = 0.0

        rr = 0.0
        for rank, key in enumerate(returned, start=1):
            if key in expected_set:
                rr = 1.0 / rank
                break

        per_query.append(
            QueryResult(
                query=gt.query,
                expected=gt.expected,
                returned=returned,
                precision_at_k=precision,
                recall_at_k=recall,
                reciprocal_rank=rr,
            )
        )

    n = len(per_query)
    mrr = sum(q.reciprocal_rank for q in per_query) / n if n else 0.0
    mean_p = sum(q.precision_at_k for q in per_query) / n if n else 0.0
    mean_r = sum(q.recall_at_k for q in per_query) / n if n else 0.0

    return SearchReport(
        query_count=n,
        mean_reciprocal_rank=mrr,
        mean_precision_at_k=mean_p,
        mean_recall_at_k=mean_r,
        per_query=tuple(per_query),
    )
