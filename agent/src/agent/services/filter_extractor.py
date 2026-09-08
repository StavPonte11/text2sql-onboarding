"""
filter_extractor.py - Extracts SQL filters and resolves column lineages.

Provides capabilities to parse Trino SQL dialect, qualify columns via database schema, 
resolve table/column aliases (including CTEs and UNNEST clauses), and compile 
a list of structured SQLFilterParams conditions.
"""

import logging
from typing import List, Any, Dict, Tuple, Optional, Literal
import sqlglot
import sqlglot.expressions as exp
from sqlglot.optimizer.qualify_columns import qualify_columns
from sqlglot.optimizer.scope import traverse_scope

from agent.services.enrichment_models import SQLFilterParams
from agent.services.sql_transformer import extract_literal_val

logger = logging.getLogger(__name__)

def get_leaf_comparisons(node: Optional[exp.Expression], is_negated: bool = False) -> List[Tuple[exp.Expression, bool]]:
    """Flattens AND/OR trees to extract all comparison operators."""
    if node is None:
        return []
    
    # Unwrap parentheses to evaluate the expressions inside
    if isinstance(node, exp.Paren):
        return get_leaf_comparisons(node.this, is_negated)
    
    if isinstance(node, exp.Not):
        return get_leaf_comparisons(node.this, not is_negated)
        
    if isinstance(node, (exp.And, exp.Or)):
        return get_leaf_comparisons(node.left, is_negated) + get_leaf_comparisons(node.right, is_negated)
    if isinstance(node, (exp.EQ, exp.NEQ, exp.GT, exp.LT, exp.GTE, exp.LTE, exp.Like, exp.ILike, exp.In, exp.Is, exp.Between)):
        return [(node, is_negated)]
    return []

def get_unaliased_table_name(node: exp.Table) -> str:
    unaliased = node.copy()
    unaliased.set("alias", None)
    return unaliased.sql(dialect="trino").lower()

class ScopeContextHelper:
    def __init__(self):
        self.table_alias_map: Dict[Tuple[int, str], str] = {}
        self.cte_select_map: Dict[Tuple[int, str], Tuple[str, str]] = {}
        self.unnest_map: Dict[Tuple[int, str], Tuple[str, str]] = {}
        
    def resolve_col_ref(self, current_scope: Any, table_alias: str, col_name: str) -> Tuple[str, str, bool]:
        """Traces alias mappings back to real database table and column names."""
        curr_scope = current_scope
        curr_table: str = table_alias.lower()
        curr_col: str = col_name.lower()
        is_unnest: bool = False
        
        # Fallback: if table alias is empty, resolve to the single table source in scope
        if not curr_table and curr_scope:
            scope_tables: List[str] = []
            for alias, src in curr_scope.sources.items():
                if isinstance(src, exp.Table) or (hasattr(src, "expression") and isinstance(src.expression, exp.Table)):
                    scope_tables.append(alias)
            if len(scope_tables) == 1:
                curr_table = scope_tables[0]
        
        visited = set()
        while curr_scope and (id(curr_scope), curr_table, curr_col) not in visited:
            visited.add((id(curr_scope), curr_table, curr_col))
            
            # A. Check unnest_map
            if (id(curr_scope), curr_table) in self.unnest_map:
                p_table, p_col = self.unnest_map[(id(curr_scope), curr_table)]
                curr_table = p_table
                curr_col = p_col
                is_unnest = True
                continue

            # B. Check cte_select_map / sources
            source = curr_scope.sources.get(curr_table)
            if source:
                if isinstance(source, exp.Table) or (hasattr(source, "expression") and isinstance(source.expression, exp.Table)):
                    target_node = source if isinstance(source, exp.Table) else source.expression
                    real_table: str = get_unaliased_table_name(target_node)
                    return real_table, curr_col, is_unnest 
                else:
                    # It's a CTE or subquery scope
                    inner_scope = source
                    mapping = self.cte_select_map.get((id(inner_scope), curr_col))
                    if mapping:
                        curr_table = mapping[0]
                        curr_col = mapping[1]
                        curr_scope = inner_scope
                    else:
                        break
            else:
                # C. Resolve from table_alias_map
                real_table = self.table_alias_map.get((id(curr_scope), curr_table))
                if real_table:
                    return real_table, curr_col, is_unnest
                break
                
        return curr_table, curr_col, is_unnest



class FilterExtractor:
    """
    Extends SQL parsing to extract explicit leaf filter predicates from WHERE clauses
    and maps them to database source columns using qualified scope context resolution.
    """

    @staticmethod
    def extract(sql: str, schema: Dict[str, Dict[str, str]]) -> Optional[List[SQLFilterParams]]:
        """
        Parses draft SQL, resolves aliases/CTEs/UNNEST nodes, and extracts target filters.

        Args:
            sql: The raw draft SQL query string.
            schema: A flat dictionary representation of the schema 
                    e.g. {'dataverse.orders': {'order_status': 'string'}}.

        Returns:
            A list of SQLFilterParams containing details on each leaf filter predicate, or None if extraction fails.
        """
        try:
            from agent.utils.sql import replace_unquoted_char
            # 1. Trino catalog workaround: replace '@' in table references with '$'
            sql_processed: str = replace_unquoted_char(sql, "@", "$")
            
            # Parse query using standard Trino dialect
            expression: exp.Expression = sqlglot.parse_one(sql_processed, dialect="trino")
            
            # 2. Normalize: Transform all Identifier nodes to lowercase
            def lowercase_identifiers(node: exp.Expression) -> exp.Expression:
                if isinstance(node, exp.Identifier):
                    node.set("this", node.name.lower())
                return node
            
            expression = expression.transform(lowercase_identifiers)
            
            def nest_schema(flat_schema: Dict[str, Dict[str, str]]) -> Dict[str, Any]:
                nested: Dict[str, Any] = {}
                if not flat_schema:
                    return nested
                # sqlglot requires a uniform nesting level. Filter out short names (e.g. city_info_table) 
                # if fully qualified names (postgres.public.city_info_table) exist.
                max_parts = max(len(t.split(".")) for t in flat_schema.keys())
                for table_name, columns in flat_schema.items():
                    parts: List[str] = table_name.split(".")
                    if len(parts) != max_parts:
                        continue
                    parts = [p.lower() for p in parts]
                    col_dict: Dict[str, str] = {c.lower(): str(t).lower() for c, t in columns.items()}
                    
                    curr: Dict[str, Any] = nested
                    for part in parts[:-1]:
                        if part not in curr:
                            curr[part] = {}
                        curr = curr[part]
                    curr[parts[-1]] = col_dict
                return nested
 
            # Use qualify_columns with the nested schema to resolve ambiguous references
            normalized_schema: Dict[str, Any] = nest_schema(schema) if schema else {}
            qualified_expression: exp.Expression = qualify_columns(expression, schema=normalized_schema)
            
            # 3. Resolve Scope structures
            context_helper = ScopeContextHelper()
            
            scopes = list(traverse_scope(qualified_expression))
            
            # First pass: map real tables, CTE names, and UNNEST aliases in each scope
            for scope in scopes:
                scope_id: int = id(scope)
                
                # Check for sources (tables / CTEs) in this scope
                for alias, source in scope.sources.items():
                    alias_lower: str = alias.lower()
                    if isinstance(source, exp.Table):
                        context_helper.table_alias_map[(scope_id, alias_lower)] = get_unaliased_table_name(source)
                    elif hasattr(source, "expression") and isinstance(source.expression, exp.Table):
                        context_helper.table_alias_map[(scope_id, alias_lower)] = get_unaliased_table_name(source.expression)

                # Look for Unnest nodes in the scope
                for unnest in scope.expression.find_all(exp.Unnest):
                    alias_node = unnest.args.get("alias")
                    if alias_node:
                        alias_name: str = alias_node.name.lower()
                        # What column is it unnesting?
                        cols = list(unnest.find_all(exp.Column))
                        if cols:
                            parent_col: exp.Column = cols[0]
                            parent_table: str = parent_col.table.lower() if parent_col.table else ""
                            context_helper.unnest_map[(scope_id, alias_name)] = (parent_table, parent_col.name.lower())
            
            # Second pass: trace subqueries/CTEs to build cte_select_map
            for scope in scopes:
                scope_id = id(scope)
                for alias, source in scope.sources.items():
                    alias_lower = alias.lower()
                    if hasattr(source, "expression") and not isinstance(source, exp.Table):
                        inner_scope = source
                        for expr in inner_scope.expression.expressions:
                            if isinstance(expr, exp.Alias):
                                col_alias: str = expr.alias.lower()
                                if isinstance(expr.this, exp.Column):
                                    inner_table: str = expr.this.table.lower() if expr.this.table else ""
                                    inner_col: str = expr.this.name.lower()
                                    context_helper.cte_select_map[(id(inner_scope), col_alias)] = (inner_table, inner_col)
                            elif isinstance(expr, exp.Column):
                                col_name: str = expr.name.lower()
                                inner_table = expr.table.lower() if expr.table else ""
                                context_helper.cte_select_map[(id(inner_scope), col_name)] = (inner_table, col_name)

            # 4. Extract Predicates and Resolve Columns
            filters: List[SQLFilterParams] = []

            for scope in scopes:
                where_clause = scope.expression.args.get("where")
                if not where_clause:
                    continue
                
                leaves = get_leaf_comparisons(where_clause.this)
                for leaf, is_negated in leaves:
                    cols_in_lhs = list(leaf.this.find_all(exp.Column))
                    if not cols_in_lhs:
                        continue
                    col_node: exp.Column = cols_in_lhs[0]
                    
                    # Verify RHS does not contain column references
                    rhs_keys: List[str] = ["expression", "expressions", "low", "high"]
                    has_rhs_column: bool = False
                    for key in rhs_keys:
                        arg = leaf.args.get(key)
                        if arg is not None:
                            if isinstance(arg, list):
                                for item in arg:
                                    if list(item.find_all(exp.Column)):
                                        has_rhs_column = True
                                        break
                            else:
                                if list(arg.find_all(exp.Column)):
                                    has_rhs_column = True
                                    break
                    if has_rhs_column:
                        continue
                    
                    # Extract operator type
                    op: str = leaf.key.upper()
                    if isinstance(leaf, (exp.Like, exp.ILike)):
                        op = "LIKE"
                    elif isinstance(leaf, exp.EQ):
                        op = "="
                    elif isinstance(leaf, exp.NEQ):
                        op = "!="
                    elif isinstance(leaf, exp.GT):
                        op = ">"
                    elif isinstance(leaf, exp.GTE):
                        op = ">="
                    elif isinstance(leaf, exp.LT):
                        op = "<"
                    elif isinstance(leaf, exp.LTE):
                        op = "<="
                    elif isinstance(leaf, exp.In):
                        op = "IN"
                    elif isinstance(leaf, exp.Is):
                        op = "IS"
                    elif isinstance(leaf, exp.Between):
                        op = "BETWEEN"
                    
                    # Extract values
                    if isinstance(leaf, exp.In):
                        value: Any = [extract_literal_val(val) for val in leaf.expressions]
                    elif isinstance(leaf, exp.Between):
                        value = [extract_literal_val(leaf.args.get("low")), extract_literal_val(leaf.args.get("high"))]
                    elif isinstance(leaf, exp.Is):
                        value = None
                        op = "IS NULL" if isinstance(leaf.expression, exp.Null) else op
                    else:
                        value = extract_literal_val(leaf.expression)
                    
                    col_alias: str = col_node.table.lower() if col_node.table else ""
                    col_name: str = col_node.name.lower()
                    
                    source_table, source_column, is_unnest = context_helper.resolve_col_ref(scope, col_alias, col_name)
                    source_table_original: str = source_table.replace("$", "@")
                    
                    # Determine match type mapping logic
                    match_type: Literal["exact", "prefix", "suffix", "substring", "in_list", "null", "range", "inequality"] = "exact"
                    if op == "=":
                        match_type = "exact"
                    elif op in (">", ">=", "<", "<=", "!="):
                        match_type = "inequality"
                    elif op == "LIKE":
                        val_str: str = str(value)
                        if val_str.startswith("%") and val_str.endswith("%"):
                            match_type = "substring"
                        elif val_str.startswith("%"):
                            match_type = "suffix"
                        elif val_str.endswith("%"):
                            match_type = "prefix"
                        else:
                            match_type = "exact"
                    elif op == "IN":
                        match_type = "in_list"
                    elif "NULL" in op or value is None:
                        match_type = "null"
                    elif op == "BETWEEN":
                        match_type = "range"
                    
                    filters.append(
                        SQLFilterParams(
                            source_table=source_table_original,
                            source_column=source_column,
                            operator=op,
                            value=value,
                            original_expression=replace_unquoted_char(leaf.sql(dialect="trino"), "$", "@"),
                            is_unnest=is_unnest,
                            is_negated=is_negated,
                            match_type=match_type
                        )
                    )
            return filters
            
        except Exception as e:
            logger.error(f"Error extracting filters from SQL: {e}", exc_info=True)
            return None
