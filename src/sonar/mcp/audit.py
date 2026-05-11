"""Audit logger for live-backed MCP tool invocations.

Records are emitted on the dedicated `sonar.mcp.audit` logger so operators can
route audit events (sample calls, rejections, DB errors) to a separate sink
from generic server-ops logging. Records never include row content, query text
beyond identifier names, or credential fragments — see mcp-server design D9.
"""

from __future__ import annotations

import logging
from typing import Literal

_AUDIT = logging.getLogger("sonar.mcp.audit")

SampleOutcome = Literal[
    "ok", "rejected_cap", "rejected_invalid_limit", "rejected_unknown_table", "db_error"
]


def emit_sample_audit(
    *,
    outcome: SampleOutcome,
    schema: str,
    table: str,
    limit_requested: int | None,
    limit_effective: int | None,
    rows_returned: int | None,
) -> None:
    """Emit one structured audit record for a `sample` tool invocation.

    All identifying fields travel on the record's `extra` dict so downstream
    handlers (JSON formatters, GxP audit sinks) can render them without parsing
    the log message.
    """
    _AUDIT.info(
        "mcp_sample",
        extra={
            "tool": "sample",
            "outcome": outcome,
            "schema": schema,
            "table": table,
            "limit_requested": limit_requested,
            "limit_effective": limit_effective,
            "rows_returned": rows_returned,
        },
    )
