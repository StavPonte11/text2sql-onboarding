"""
sql_transformer.py - Modifies comparison values in the SQL AST.

Alters filter operators and literals in a parsed query's AST, ensuring 
logical siblings (AND/OR trees) are fully preserved.
"""

import logging
from typing import Any, List
import sqlglot
import sqlglot.expressions as exp
from agent.services.enrichment_models import TransformationPlan

logger = logging.getLogger(__name__)

def extract_literal_val(node: Any) -> Any:
    """Translates sqlglot AST literal/boolean node values to Python primitives."""
    if node is None:
        return None
    if isinstance(node, exp.Literal):
        if node.is_string:
            return node.this
        try:
            if "." in node.this:
                return float(node.this)
            return int(node.this)
        except ValueError:
            return node.this
    elif isinstance(node, exp.Null):
        return None
    elif isinstance(node, exp.Boolean):
        return node.this
    return node.sql()


class SQLTransformer:
    """
    Programmatically transforms draft queries inside the sqlglot AST representation,
    mapping values to database-safe canonical values.
    """

    @staticmethod
    def _get_unaliased_table_name(node: exp.Table) -> str:
        unaliased = node.copy()
        unaliased.set("alias", None)
        return unaliased.sql(dialect="trino").lower()

    @staticmethod
    def _is_node_negated(curr_node: exp.Expression) -> bool:
        negated = False
        curr = curr_node.parent
        while curr is not None:
            if isinstance(curr, exp.Not):
                negated = not negated
            curr = curr.parent
        return negated

    @staticmethod
    def _normalize_op(op_str: str) -> str:
        op_clean = op_str.upper().strip()
        if op_clean == "EQ":
            return "="
        if op_clean in ("NEQ", "<>"):
            return "!="
        if op_clean == "GTE":
            return ">="
        if op_clean == "LTE":
            return "<="
        if op_clean == "GT":
            return ">"
        if op_clean == "LT":
            return "<"
        return op_clean

    @staticmethod
    def _make_literal(val_str: str) -> exp.Expression:
        return exp.Literal.string(val_str)

    @staticmethod
    def _build_expr(op_str: str, lhs: exp.Expression, refined_vals: List[str]) -> exp.Expression:
        op_clean = SQLTransformer._normalize_op(op_str)
        if op_clean == "IN":
            return exp.In(this=lhs, expressions=[SQLTransformer._make_literal(v) for v in refined_vals])
        elif op_clean == "BETWEEN" and len(refined_vals) >= 2:
            return exp.Between(this=lhs, low=SQLTransformer._make_literal(refined_vals[0]), high=SQLTransformer._make_literal(refined_vals[-1]))
        elif op_clean == "IS NULL" or (op_clean == "IS" and not refined_vals):
            return exp.Is(this=lhs, expression=exp.Null())
        elif op_clean in ("IS NOT NULL", "IS NOT"):
            return exp.IsNot(this=lhs, expression=exp.Null())
        
        val = refined_vals[0] if refined_vals else ""
        lit = SQLTransformer._make_literal(val)
        
        if op_clean == "=":
            return exp.EQ(this=lhs, expression=lit)
        elif op_clean == "!=":
            return exp.NEQ(this=lhs, expression=lit)
        elif op_clean == ">":
            return exp.GT(this=lhs, expression=lit)
        elif op_clean == ">=":
            return exp.GTE(this=lhs, expression=lit)
        elif op_clean == "<":
            return exp.LT(this=lhs, expression=lit)
        elif op_clean == "<=":
            return exp.LTE(this=lhs, expression=lit)
        elif op_clean == "LIKE":
            return exp.Like(this=lhs, expression=lit)
        elif op_clean == "ILIKE":
            return exp.ILike(this=lhs, expression=lit)
        
        return exp.EQ(this=lhs, expression=lit)


    @staticmethod
    def apply(sql: str, plan: TransformationPlan) -> str:
        """
        Parses the query, transforms targeted literal values, and outputs back Trino SQL.

        Args:
            sql: The original raw SQL query string.
            plan: The structured TransformationPlan detailing replacement values.

        Returns:
            The modified SQL query string.
        """
        try:
            from agent.utils.sql import replace_unquoted_char
            # 1. Trino catalog workaround: replace '@' in table references with '$'
            sql_processed: str = replace_unquoted_char(sql, "@", "$")
            
            # 2. Parse SQL using Trino dialect
            expression: exp.Expression = sqlglot.parse_one(sql_processed, dialect="trino")
            
            alias_to_table = {}
            for table_node in expression.find_all(exp.Table):
                alias = table_node.alias.lower() if table_node.alias else ""
                t_name = SQLTransformer._get_unaliased_table_name(table_node)
                if alias:
                    alias_to_table[alias] = t_name
                alias_to_table[t_name] = t_name
            

            def transform_node(node: exp.Expression) -> exp.Expression:
                """Transform handler applied recursively to AST leaf comparison nodes."""
                if not isinstance(node, (exp.EQ, exp.NEQ, exp.GT, exp.LT, exp.GTE, exp.LTE, exp.Like, exp.ILike, exp.In, exp.Is, exp.Between)):
                    return node
                
                if SQLTransformer._is_node_negated(node):
                    return node
                
                # Check column target in LHS
                cols = list(node.this.find_all(exp.Column))
                if not cols:
                    return node
                col_name: str = cols[0].name.lower()
                col_table: str = cols[0].table.lower() if cols[0].table else ""
                resolved_table = alias_to_table.get(col_table, col_table)
                
                # Extract values from RHS
                current_vals: List[Any] = []
                is_string_comparison = False
                
                if isinstance(node, exp.In):
                    current_vals = [extract_literal_val(v) for v in node.expressions]
                    for v in node.expressions:
                        if isinstance(v, exp.Literal) and v.is_string:
                            is_string_comparison = True
                            break
                elif isinstance(node, exp.Between):
                    low, high = node.args.get("low"), node.args.get("high")
                    current_vals = [extract_literal_val(low), extract_literal_val(high)]
                    if (isinstance(low, exp.Literal) and low.is_string) or (isinstance(high, exp.Literal) and high.is_string):
                        is_string_comparison = True
                elif isinstance(node, exp.Is):
                    current_vals = [None]
                else:
                    current_vals = [extract_literal_val(node.expression)]
                    if isinstance(node.expression, exp.Literal) and node.expression.is_string:
                        is_string_comparison = True
                    
                # Search for matching transformation plan items for this node
                node_op = node.key.upper()
                if isinstance(node, (exp.Like, exp.ILike)):
                    node_op = "LIKE"
                elif isinstance(node, exp.EQ):
                    node_op = "="
                elif isinstance(node, exp.NEQ):
                    node_op = "!="
                elif isinstance(node, exp.GT):
                    node_op = ">"
                elif isinstance(node, exp.GTE):
                    node_op = ">="
                elif isinstance(node, exp.LT):
                    node_op = "<"
                elif isinstance(node, exp.LTE):
                    node_op = "<="
                elif isinstance(node, exp.In):
                    node_op = "IN"
                elif isinstance(node, exp.Is):
                    node_op = "IS NULL" if isinstance(node.expression, exp.Null) else "IS"

                matching_tfs = []
                for tf in plan.enrichment_details:
                    if tf.column.lower() != col_name:
                        continue
                    if SQLTransformer._normalize_op(node_op) != SQLTransformer._normalize_op(tf.old_operator):
                        continue
                    if tf.table and col_table:
                        if tf.table.lower() != resolved_table:
                            continue
                    
                    orig_val_clean: str = tf.original_value.replace("%", "").strip().lower()
                    for val in current_vals:
                        val_clean: str = "null" if val is None else str(val).replace("%", "").strip().lower()
                        if val_clean == orig_val_clean:
                            matching_tfs.append(tf)
                            break

                if not matching_tfs:
                    return node

                refined_values = []
                any_change = False
                target_operator = matching_tfs[0].new_operator if matching_tfs else node_op

                for val in current_vals:
                    val_clean: str = "null" if val is None else str(val).replace("%", "").strip().lower()
                    matched_tf = None
                    for tf in matching_tfs:
                        if tf.original_value.replace("%", "").strip().lower() == val_clean:
                            matched_tf = tf
                            break
                    
                    if matched_tf:
                        if matched_tf.changed_filter:
                            refined_values.extend(matched_tf.refined_values)
                            any_change = True
                            target_operator = matched_tf.new_operator
                        else:
                            if val is not None:
                                refined_values.append(str(val))
                            else:
                                refined_values.append("null")
                    else:
                        # Value was in the original list but not in any transformation plan
                        if val is not None:
                            refined_values.append(str(val))
                        else:
                            refined_values.append("null")

                if not any_change:
                    return node

                if len(refined_values) > 1 and SQLTransformer._normalize_op(node_op) != "BETWEEN":
                    target_operator = "IN"

                logger.info(
                    f"Applying transformation: column '{col_name}', "
                    f"current_values {current_vals}, operator '{node_op}' -> "
                    f"new_operator '{target_operator}', values {refined_values}"
                )

                return SQLTransformer._build_expr(target_operator, node.this, refined_values)
                
            # Apply transformations recursively
            modified_ast: exp.Expression = expression.transform(transform_node)
            
            # Generate SQL string back using Trino dialect
            refined_sql: str = modified_ast.sql(dialect="trino")
            
            # 4. Revert Trino catalog workaround using the safe unquoted replacement
            refined_sql_final: str = replace_unquoted_char(refined_sql, "$", "@")
            return refined_sql_final
            
        except Exception as e:
            logger.error(f"Error applying SQL transformation: {e}", exc_info=True)
            return sql
