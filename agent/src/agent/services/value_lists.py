"""
value_lists.py - Filter values checked against the catalog's complete value lists.

The Jeen catalog lists every value of a low-cardinality column (`— values (all): 'a', 'b'`). A SQL filter on such a
column whose value is not in that list matches no rows as written — usually the user's wording used verbatim
instead of the stored value (e.g. 'Fully Closed' for 'Fully Closed Airspace'). Used by the legacy enrichment step,
where the list becomes the candidate set.
"""

from typing import Dict, List, Optional, Set, Tuple

from agent.services.enrichment_models import AgentSQLTable, SQLFilterParams


def table_key(name: Optional[str]) -> str:
    """Comparable table name: the SQL and the catalog quote identifiers differently."""
    return (name or "").replace('"', "").lower()


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
        """The list for the filter's column in the filter's table.

        A named table is matched in full, or by its last part (unqualified SQL: `policies`); a table that matches
        no catalog table gets no list — never another table's. Only a filter with no table at all falls back to
        the column, when exactly one table has it.
        """
        col, table = f.source_column.lower(), table_key(f.source_table)
        owners = self._tables_by_column.get(col, set())
        if table:
            if table in owners:
                return self._lists.get((table, col))
            by_name = [t for t in owners if t.rsplit(".", 1)[-1] == table.rsplit(".", 1)[-1]]
            return self._lists.get((by_name[0], col)) if len(by_name) == 1 else None
        return self._lists.get((next(iter(owners)), col)) if len(owners) == 1 else None

    def unlisted(self, f: SQLFilterParams) -> List[str]:
        """The values of an `=` / `IN` filter that its column's complete list rules out (none without a list).

        Not checked: negated filters (NOT IN, NOT ... =) — the SQL rewriter never changes them, so a correction
        would only cost an LLM call — and LIKE, a deliberate partial match whose column may be wrapped in a
        function (`lower(col) LIKE '%closed%'`) the filter extractor doesn't keep.
        """
        if f.is_negated or f.operator.upper() not in ("=", "IN"):
            return []
        listed = self.for_filter(f)
        if not listed:
            return []
        values = [str(v) for v in (f.value if isinstance(f.value, list) else [f.value]) if v is not None]
        return [v for v in values if v not in listed]

