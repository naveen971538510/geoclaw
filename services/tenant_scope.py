"""
Row-level tenant filtering.

Design:
- Rows with user_id IS NULL are "shared / system" and visible to all users.
- Rows with user_id = X are private to user X.
- Anonymous callers (request.state.user_id is None) see ONLY shared rows.
- Authenticated user X sees shared rows UNION their own rows.
- INSERT paths should stamp request.state.user_id when present.

The `scope_where()` / `scope_join()` helpers produce a parameter-safe WHERE
fragment that callers splice into their SQL. They never interpolate values
into the SQL string — parameters go through psycopg2 as %s.
"""
from __future__ import annotations

from typing import Any, List, Optional, Tuple


def current_user_id(request) -> Optional[int]:
    uid = getattr(getattr(request, "state", None), "user_id", None)
    try:
        return int(uid) if uid else None
    except Exception:
        return None


def scope_where(user_id: Optional[int], alias: str = "", placeholder: str = "%s") -> Tuple[str, List[Any]]:
    """
    Return (sql_fragment, params) to filter a SELECT to rows visible to user_id.

    Usage:
        clause, params = scope_where(current_user_id(request))
        rows = query_all(f"SELECT * FROM geoclaw_signals WHERE {clause} ORDER BY ts DESC", params)

    - If user_id is None: returns "user_id IS NULL" (only shared rows).
    - If user_id is set: returns "(user_id IS NULL OR user_id = %s)" with param.

    ``placeholder`` defaults to ``%s`` (psycopg2 / intelligence.db style). Pass
    ``?`` when splicing into SQL routed through services/db_helpers.query, which
    uses SQLite ``?`` placeholders and converts them to ``%s`` internally on
    the Postgres path.
    """
    col = f"{alias}.user_id" if alias else "user_id"
    if user_id is None:
        return f"{col} IS NULL", []
    return f"({col} IS NULL OR {col} = {placeholder})", [int(user_id)]


def and_scope(existing_where: str, user_id: Optional[int], alias: str = "", placeholder: str = "%s") -> Tuple[str, List[Any]]:
    """
    Combine an existing WHERE clause with the tenant scope.

    Pass the existing clause WITHOUT "WHERE" — returned fragment also has no WHERE.
    """
    tenant_clause, tenant_params = scope_where(user_id, alias=alias, placeholder=placeholder)
    existing = (existing_where or "").strip()
    if not existing:
        return tenant_clause, tenant_params
    return f"({existing}) AND {tenant_clause}", tenant_params
