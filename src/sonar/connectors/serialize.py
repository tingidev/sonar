"""Connector-agnostic row coercion for sampled rows."""

import datetime as _datetime
import decimal as _decimal
import uuid as _uuid
from typing import Any

_BINARY_PLACEHOLDER = "<binary>"


def _coerce_value(value: Any) -> Any:
    if isinstance(value, _uuid.UUID):
        return str(value)
    if isinstance(value, (_datetime.datetime, _datetime.date)):
        return value.isoformat()
    if isinstance(value, _decimal.Decimal):
        return float(value)
    if isinstance(value, bytes):
        return _BINARY_PLACEHOLDER
    return value


def _serialize_row(row: dict) -> dict:
    return {k: _coerce_value(v) for k, v in row.items()}
