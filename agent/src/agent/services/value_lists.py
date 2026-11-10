"""
value_lists.py - Filter values checked against the catalog's complete value lists.

The Jeen catalog lists every value of a low-cardinality column (`— values (all): 'a', 'b'`). A SQL filter on such a
column whose value is not in that list matches no rows as written — usually the user's wording used verbatim
instead of the stored value (e.g. 'Fully Closed' for 'Fully Closed Airspace'). Used by the legacy enrichment step,
where the list becomes the candidate set.
"""

import re
from typing import Dict, List, Optional, Set, Tuple

from agent.services.enrichment_models import AgentSQLTable, SQLFilterParams


def table_key(name: Optional[str]) -> str:
    """Comparable table name: the SQL and the catalog quote identifiers differently."""
    return (name or "").replace('"', "").lower()


def _like_matches(pattern: str, value: str) -> bool:
    regex = "".join(".*" if ch == "%" else "." if ch == "_" else re.escape(ch) for ch in pattern)
    return re.fullmatch(regex, value, re.DOTALL) is not None


class CompleteValueLists:
    """The complete value list of each (table, column), from parsed catalog tables (`values_all`)."""

    def __init__(self, tables: List[AgentSQLTable]) -> None:
        self._lists: Dict[Tuple[str, str], List[str]] = {}
        self._tables_by_column: Dict[str, Set[str]] = {}
        for table in tables:
            t_name = table_key(table.name)
            for col_name, meta in table.columns.items():
                c_name = col_name.lower()
                self._tables_by_column.setdefault(c_name, set()).add(t_name)
                if isinstance(meta, dict) and meta.get("values_all"):
                    self._lists[(t_name, c_name)] = meta["values_all"]

    def for_filter(self, f: SQLFilterParams) -> Optional[List[str]]:
        """The list for the filter's column; a column name in several tables needs the filter's table to tell."""
        col, table = f.source_column.lower(), table_key(f.source_table)
        if (table, col) in self._lists:
            return self._lists[(table, col)]
        owners = self._tables_by_column.get(col, set())
        return self._lists.get((next(iter(owners)), col)) if len(owners) == 1 else None

    def unlisted(self, f: SQLFilterParams) -> List[str]:
        """The filter's values that its column's complete list rules out (none when there is no complete list)."""
        listed = self.for_filter(f)
        if not listed:
            return []
        values = [str(v) for v in (f.value if isinstance(f.value, list) else [f.value]) if v is not None]
        op = f.operator.upper()
        if op in ("=", "IN"):
            return [v for v in values if v not in listed]
        if op == "LIKE":
            return [v for v in values if not any(_like_matches(v, item) for item in listed)]
        return []

