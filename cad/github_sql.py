"""In-memory table engine used as a GitHub-backed Postgres stand-in for CAD SQL."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

_NOW_RE = re.compile(r"\bNOW\(\)", re.I)
_INTERVAL = re.compile(
    r"NOW\(\)\s*\+\s*INTERVAL\s+'(\d+)\s*(minutes|minute|hours|hour|seconds|second|days|day)'",
    re.I,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _norm(sql: str) -> str:
    return re.sub(r"\s+", " ", sql.strip().rstrip(";"))


def _parse_dt(value: Any) -> Any:
    if isinstance(value, str) and len(value) >= 19 and value[4] == "-":
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    return value


def load_state(raw: dict | None) -> dict[str, Any]:
    tables = (raw or {}).get("tables") or {}
    seq = (raw or {}).get("seq") or {}
    for name, rows in list(tables.items()):
        cleaned = []
        for row in rows or []:
            cleaned.append({k: _parse_dt(v) for k, v in dict(row).items()})
        tables[name] = cleaned
    return {"tables": tables, "seq": {k: int(v) for k, v in seq.items()}}


def dump_state(state: dict[str, Any]) -> dict[str, Any]:
    tables = {}
    for name, rows in (state.get("tables") or {}).items():
        out = []
        for row in rows:
            item = {}
            for k, v in row.items():
                if isinstance(v, datetime):
                    item[k] = v.isoformat()
                elif isinstance(v, UUID):
                    item[k] = str(v)
                else:
                    item[k] = v
            out.append(item)
        tables[name] = out
    return {"tables": tables, "seq": state.get("seq") or {}}


def _table(state: dict, name: str) -> list[dict]:
    tables = state.setdefault("tables", {})
    return tables.setdefault(name, [])


def _next_id(state: dict, table: str) -> int:
    seq = state.setdefault("seq", {})
    seq[table] = int(seq.get(table) or 0) + 1
    return seq[table]


def _subst_expr(expr: str, args: tuple) -> Any:
    expr = expr.strip()
    if re.fullmatch(r"\$\d+", expr):
        return args[int(expr[1:]) - 1]
    m = _INTERVAL.search(expr)
    if m:
        n = int(m.group(1))
        unit = m.group(2).lower()
        delta = timedelta(minutes=n) if "min" in unit else timedelta(hours=n) if "hour" in unit else timedelta(days=n) if "day" in unit else timedelta(seconds=n)
        return utcnow() + delta
    if _NOW_RE.search(expr) and "INTERVAL" not in expr.upper():
        return utcnow()
    if expr.upper() in {"TRUE", "FALSE"}:
        return expr.upper() == "TRUE"
    if expr.upper() == "NULL":
        return None
    if (expr.startswith("'") and expr.endswith("'")) or (expr.startswith('"') and expr.endswith('"')):
        return expr[1:-1]
    if re.fullmatch(r"-?\d+", expr):
        return int(expr)
    if expr.upper().startswith("NOW() +"):
        return utcnow() + timedelta(hours=12)
    return expr


def _split_csv(text: str) -> list[str]:
    vals: list[str] = []
    buf = ""
    depth = 0
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            vals.append(buf)
            buf = ""
        else:
            buf += ch
    if buf.strip():
        vals.append(buf)
    return vals


def _col(name: str) -> str:
    name = name.strip().strip('"')
    if "." in name:
        name = name.split(".")[-1]
    return name.lower()


def _match(row: dict, clause: str, args: tuple) -> bool:
    clause = clause.strip()
    if not clause:
        return True
    parts = re.split(r"\s+AND\s+", clause, flags=re.I)
    if len(parts) > 1:
        return all(_match(row, p, args) for p in parts)
    or_parts = re.split(r"\s+OR\s+", clause, flags=re.I)
    if len(or_parts) > 1:
        return any(_match(row, p, args) for p in or_parts)

    m = re.match(r"(.+?)\s+ILIKE\s+(\$\d+|'.*')", clause, re.I)
    if m:
        val = str(row.get(_col(m.group(1)), "") or "")
        pat = str(_subst_expr(m.group(2), args)).replace("%", ".*").replace("_", ".")
        return re.search(pat, val, re.I) is not None

    m = re.match(r"(.+?)\s+NOT IN\s*\((.+)\)", clause, re.I)
    if m:
        raw = [_subst_expr(x.strip(), args) for x in m.group(2).split(",")]
        return row.get(_col(m.group(1))) not in raw

    m = re.match(r"(.+?)\s+IN\s*\((.+)\)", clause, re.I)
    if m:
        raw = [_subst_expr(x.strip(), args) for x in m.group(2).split(",")]
        return row.get(_col(m.group(1))) in raw

    m = re.match(r"(.+?)\s+IS NOT NULL", clause, re.I)
    if m:
        return row.get(_col(m.group(1))) is not None

    m = re.match(r"(.+?)\s+IS NULL", clause, re.I)
    if m:
        return row.get(_col(m.group(1))) is None

    m = re.match(r"(.+?)\s*(=|!=|<>|>=|<=|>|<)\s*(.+)", clause)
    if m:
        left = row.get(_col(m.group(1)))
        op = m.group(2)
        right = _subst_expr(m.group(3), args)
        if isinstance(left, datetime) and isinstance(right, str):
            right = _parse_dt(right)
        if isinstance(right, datetime) and isinstance(left, str):
            left = _parse_dt(left)
        if op == "=":
            if left == right:
                return True
            if left is not None and right is not None and str(left) == str(right):
                return True
            return False
        if op in ("!=", "<>"):
            return left != right
        if left is None or right is None:
            return False
        if op == ">":
            return left > right
        if op == "<":
            return left < right
        if op == ">=":
            return left >= right
        if op == "<=":
            return left <= right
    return True


def _order(rows: list[dict], clause: str | None) -> list[dict]:
    if not clause:
        return rows
    desc = bool(re.search(r"\bDESC\b", clause, re.I))
    col = _col(re.sub(r"\s+(ASC|DESC)\b", "", clause, flags=re.I))
    def key(r: dict):
        v = r.get(col)
        return (v is None, v)
    return sorted(rows, key=key, reverse=desc)


def _limit(rows: list[dict], sql: str) -> list[dict]:
    m = re.search(r"\bLIMIT\s+(\d+)\b", sql, re.I)
    if m:
        return rows[: int(m.group(1))]
    return rows


def _from_table(sql: str) -> str:
    m = re.search(r"\bFROM\s+(\w+)", sql, re.I)
    if m:
        return m.group(1)
    m = re.search(r"\bUPDATE\s+(\w+)", sql, re.I)
    if m:
        return m.group(1)
    m = re.search(r"\bINTO\s+(\w+)", sql, re.I)
    if m:
        return m.group(1)
    raise ValueError(f"No table in SQL: {sql[:80]}")


def _where(sql: str) -> str:
    m = re.search(r"\bWHERE\s+(.+?)(?:\s+GROUP BY|\s+ORDER BY|\s+LIMIT|\s+RETURNING|\s+FOR UPDATE|$)", sql, re.I)
    return m.group(1) if m else ""


def _join_select(state: dict, sql: str, args: tuple) -> list[dict]:
    """Nested-loop joins for CAD queries."""
    from_m = re.search(r"\bFROM\s+(\w+)(?:\s+(\w+))?", sql, re.I)
    if not from_m:
        return []
    left_table, left_alias = from_m.group(1), (from_m.group(2) or from_m.group(1))
    rows = [dict(r) for r in _table(state, left_table)]

    def _join_val(row: dict, other: dict, expr: str, l_alias: str, r_alias: str) -> Any:
        parts = expr.strip().split(".")
        if len(parts) == 2:
            alias, col = parts[0], _col(parts[1])
            if alias.lower() == r_alias.lower():
                return other.get(col)
            return row.get(col)
        col = _col(expr)
        if col in other and col not in row:
            return other.get(col)
        return row.get(col)

    for jm in re.finditer(
        r"\b(LEFT\s+)?JOIN\s+(\w+)(?:\s+(\w+))?\s+ON\s+([\w.]+)\s*=\s*([\w.]+)",
        sql,
        re.I,
    ):
        is_left = bool(jm.group(1))
        rtable = jm.group(2)
        ralias = jm.group(3) or rtable
        right_rows = _table(state, rtable)
        merged = []
        for row in rows:
            matched = False
            for rr in right_rows:
                lv = _join_val(row, rr, jm.group(4), left_alias, ralias)
                rv = _join_val(row, rr, jm.group(5), left_alias, ralias)
                if lv == rv and lv is not None:
                    combined = dict(rr)
                    combined.update(row)
                    merged.append(combined)
                    matched = True
            if not matched and is_left:
                merged.append(dict(row))
        rows = merged
        left_alias = ralias
    rows = [r for r in rows if _match(r, _where(sql), args)]
    rows = _order(rows, _order_clause(sql))
    return _limit(rows, sql)


def _order_clause(sql: str) -> str | None:
    m = re.search(r"\bORDER BY\s+(.+?)(?:\s+LIMIT|$)", sql, re.I)
    return m.group(1) if m else None


def _project(rows: list[dict], sql: str) -> list[dict]:
    m = re.match(r"SELECT\s+(.+?)\s+FROM\b", sql, re.I)
    if not m:
        return rows
    cols = m.group(1).strip()
    if cols == "*" or re.search(r"\.\*", cols):
        return rows
    if re.search(r"COUNT\s*\(\s*\*\s*\)", cols, re.I) and "GROUP BY" not in sql.upper():
        return [{"count": len(rows)}]
    names = []
    for part in cols.split(","):
        part = part.strip()
        alias_m = re.search(r"\s+AS\s+(\w+)$", part, re.I)
        if alias_m:
            names.append(alias_m.group(1))
        elif part != "*":
            names.append(_col(part.split()[0]))
    out = []
    for row in rows:
        item = {}
        for name in names:
            if name.lower() == "n":
                continue
            item[name] = row.get(name) if name in row else row.get(_col(name))
        if item:
            out.append(item)
        else:
            out.append(row)
    return out or rows


def run(state: dict[str, Any], sql: str, args: tuple = (), mode: str = "fetch") -> Any:
    sql = _norm(sql)
    upper = sql.upper()

    if upper.startswith("SELECT 1") and "FROM" not in upper:
        return 1 if mode == "fetchval" else [{"?column?": 1}]

    if "INFORMATION_SCHEMA.TABLES" in upper:
        return True if mode == "fetchval" else [{"exists": True}]

    if upper.startswith("SELECT") and " JOIN " in upper:
        rows = _join_select(state, sql, args)
        rows = _project(rows, sql)
        if mode == "fetchval":
            return None if not rows else next(iter(rows[0].values()))
        if mode == "fetchrow":
            return rows[0] if rows else None
        return rows

    if upper.startswith("SELECT") and "COUNT(" in upper and "GROUP BY" in upper:
        table = _from_table(sql)
        rows = [r for r in _table(state, table) if _match(r, _where(sql), args)]
        g = re.search(r"GROUP BY\s+(.+?)(?:\s+ORDER|\s+LIMIT|$)", sql, re.I)
        keys = [_col(x) for x in g.group(1).split(",")] if g else []
        buckets: dict[tuple, int] = defaultdict(int)
        for row in rows:
            buckets[tuple(row.get(k) for k in keys)] += 1
        out = []
        for k, n in buckets.items():
            item = {keys[i]: k[i] for i in range(len(keys))}
            item["n"] = n
            out.append(item)
        if mode == "fetchval":
            return out[0]["n"] if out else 0
        if mode == "fetchrow":
            return out[0] if out else None
        return out

    if upper.startswith("SELECT") and re.search(r"COUNT\s*\(", upper):
        table = _from_table(sql)
        n = sum(1 for r in _table(state, table) if _match(r, _where(sql), args))
        if mode == "fetchval":
            return n
        return [{"count": n}] if mode == "fetch" else {"count": n}

    if upper.startswith("SELECT"):
        table = _from_table(sql)
        rows = [dict(r) for r in _table(state, table) if _match(r, _where(sql), args)]
        rows = _order(rows, _order_clause(sql))
        rows = _limit(rows, sql)
        rows = _project(rows, sql)
        if mode == "fetchval":
            if not rows:
                return None
            row = rows[0]
            return next(iter(row.values())) if row else None
        if mode == "fetchrow":
            return rows[0] if rows else None
        return rows

    if upper.startswith("INSERT"):
        table = _from_table(sql)
        cols_m = re.search(r"INSERT INTO \w+\s*\(([^)]+)\)\s*VALUES\s*\(", sql, re.I)
        if not cols_m:
            raise ValueError("INSERT parse failed")
        cols = [_col(c) for c in cols_m.group(1).split(",")]
        start = cols_m.end()
        depth = 1
        i = start
        while i < len(sql) and depth:
            if sql[i] == "(":
                depth += 1
            elif sql[i] == ")":
                depth -= 1
            i += 1
        vals = _split_csv(sql[start : i - 1])
        rest = sql[i:]
        row = {}
        for col, expr in zip(cols, vals):
            val = _subst_expr(expr.strip(), args)
            if isinstance(val, UUID):
                val = str(val)
            row[col] = val
        if "id" not in row:
            row["id"] = _next_id(state, table)
        if table == "cad_sessions" and not row.get("id"):
            row["id"] = str(uuid4())
        rows = _table(state, table)
        conflict = re.search(r"ON CONFLICT\s*\(([^)]+)\)\s*DO UPDATE SET\s+(.+?)(?:\s+RETURNING|$)", sql, re.I)
        conflict_nothing = bool(re.search(r"ON CONFLICT[^\n]+DO NOTHING", sql, re.I))
        if conflict:
            keys = [_col(c) for c in conflict.group(1).split(",")]
            existing = next((r for r in rows if all(r.get(k) == row.get(k) for k in keys)), None)
            if existing:
                set_sql = conflict.group(1) and conflict.group(2)
                for assign in _split_csv(set_sql):
                    if "=" not in assign:
                        continue
                    k, v = assign.split("=", 1)
                    key = _col(k)
                    val = v.strip()
                    if "EXCLUDED." in val.upper():
                        existing[key] = row.get(_col(val.split(".")[-1]))
                    elif "::int" in val.lower() or "+ 1" in val:
                        existing[key] = str(int(existing.get(key) or 0) + 1)
                    else:
                        existing[key] = _subst_expr(val, args)
                row = existing
            else:
                rows.append(row)
        elif conflict_nothing:
            # unique on first col
            if any(r.get(cols[0]) == row.get(cols[0]) for r in rows):
                pass
            else:
                rows.append(row)
        else:
            rows.append(row)
        if "RETURNING" in upper:
            if mode == "fetchval":
                return row.get("id")
            return row
        return "INSERT 0 1"

    if upper.startswith("UPDATE"):
        table = _from_table(sql)
        set_m = re.search(r"\bSET\s+(.+?)\s+WHERE\s+", sql, re.I)
        if not set_m:
            set_m = re.search(r"\bSET\s+(.+?)(?:\s+RETURNING|$)", sql, re.I)
        assigns = set_m.group(1) if set_m else ""
        where = _where(sql)
        updated = []
        for row in _table(state, table):
            if not _match(row, where, args):
                continue
            for assign in _split_csv(assigns):
                if "=" not in assign:
                    continue
                k, v = assign.split("=", 1)
                key = _col(k)
                val = v.strip()
                if re.match(r"COALESCE\(", val, re.I):
                    inner = val[val.find("(") + 1 : val.rfind(")")]
                    parts = [p.strip() for p in inner.split(",")]
                    cur = row.get(_col(parts[0]))
                    row[key] = cur if cur is not None else _subst_expr(parts[1], args)
                else:
                    row[key] = _subst_expr(val, args)
            updated.append(row)
        if "RETURNING" in upper:
            if mode == "fetchval":
                return updated[0].get("id") if updated else None
            if mode == "fetchrow":
                return updated[0] if updated else None
            return updated
        return f"UPDATE {len(updated)}"

    if upper.startswith("CREATE") or upper.startswith("DROP") or upper.startswith("ALTER"):
        return "OK"

    raise ValueError(f"Unsupported SQL: {sql[:120]}")
