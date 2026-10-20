---
name: trino-expert
description: Trino SQL syntax rules, domain conventions, categorical filter patterns, aggregation correctness, join strategies, and systematic debugging for this database.
---
# Trino SQL Expert Reference

## Core Rules Checklist

1. **Strings vs Identifiers**: Strings use single quotes `'abc'`. Identifiers with special chars or Hebrew use double quotes `"col"`. Never use backticks (\`col\`).
2. **Fully Qualified Names**: Always write full table names as given in the metadata: `"catalog"."schema"."table"`.
3. **Column Projection**: Never `SELECT *`. Select only the specific columns needed.
4. **Early Filtering**: Put filters in `WHERE` as early as possible (inside the innermost CTE); always filter the partition column on large tables.
5. **Row Limits**: Always add `LIMIT` to non-aggregate exploratory queries. Use `ORDER BY` + `LIMIT` for "top N" (or `RANK()` if ties must be preserved).
6. **Integer Division Truncation**: `5 / 2 = 2`. Use `CAST(a AS double) / b` or `1.0 * a / b` for ratios and percentages.
7. **Safe Division**: Divide safely: `x / NULLIF(y, 0)` to avoid division-by-zero crashes.
8. **GROUP BY Completeness**: Every non-aggregated column in `SELECT` must be included in `GROUP BY`.
9. **No Cross Joins & Distributed Join Order**: Every `JOIN` must have an explicit `ON` condition with keys. Put the large fact table on the left, and the small lookup/dimension table on the right.
10. **Distinct Cardinality**: Use `COUNT(DISTINCT x)` when exact is required; use `approx_distinct(x)` on large tables for speed.
11. **Case-Insensitive Matching**: Use `lower(col) LIKE lower('%text%')` or `regexp_like(col, '(?i)...')`. There is no `ILIKE`.
12. **NULL Checks**: Use `IS NULL` / `IS NOT NULL`. Never use `= NULL` or `!= NULL`.
13. **CTEs for Structure**: Use CTEs (`WITH x AS (...)`) for multi-step logic. Keep each CTE focused on a single responsibility.
14. **Strictly Read-Only**: Never write `INSERT`, `UPDATE`, `DELETE`, `DROP`, `CREATE`, `ALTER`. Read-only SELECT statements only.

## Common Table Expressions (`WITH` Clauses) — Preferred SQL Style
- Trino has first-class, highly optimized support for CTEs (`WITH cte_name AS (...)`).
- **Always prefer CTEs over deeply nested subqueries**:
  - Cleanly separates data preparation (filtering, date parsing, geometry) from final presentation.
  - Aggregates child tables before joining to completely eliminate 1-to-many join fan-out.
  - Enables window filtering (e.g. compute `ROW_NUMBER() / RANK() OVER (...)` in a CTE, filter `WHERE rn <= N` in the outer query).
  - Makes SQL self-documenting, modular, and easy to inspect.

## Arrays
- Check array membership: `contains(array_col, 'value')` — NEVER `array_contains()`
- Expand arrays: `CROSS JOIN UNNEST(array_col) AS t(element)`

## Aggregations & Nulls
- **Integer Division Truncates**: `5 / 2 = 2`! When calculating ratios, percentages, or rates, integer division silently truncates to 0.
  - Always cast the numerator: `CAST(a AS double) / NULLIF(b, 0)` or `1.0 * a / NULLIF(b, 0)`.
  - Always divide safely with `NULLIF(y, 0)` to prevent runtime division-by-zero errors.
- **Idiomatic Conditional Counts**: Use `count_if(cond)` instead of verbose `SUM(CASE WHEN cond THEN 1 ELSE 0 END)`.
  - Example: `count_if(status = 'active')`
- **Latest State Without Window / Self-Join**: Use `max_by(val, time)` or `min_by(val, time)` to find the latest value per entity:
  - Example: `SELECT entity_id, max_by(status, updated_at) AS last_status, max(updated_at) AS last_at FROM t GROUP BY entity_id`
- Wrap aggregated columns: `COALESCE(col, 0)` to prevent NULL poisoning
- Count unique entities: `COUNT(DISTINCT id)` — not `COUNT(*)`
- `COUNT(*)` counts rows; `COUNT(col)` skips NULLs; `COUNT(DISTINCT col)` counts unique non-null values
- `AVG()` ignores NULLs — if NULLs mean "zero", use `AVG(COALESCE(col, 0))`

## Dialect & Syntax Prohibitions
- **NO `ILIKE`**: Trino does NOT support `ILIKE`. Use `LOWER(col) LIKE LOWER('val')` or `regexp_like(col, '(?i)...')`.
- **NO Trailing Semicolons (`;`)**: Trino parser rejects queries ending in `;`. Never include trailing semicolons.
- **Identifier Quotes vs String Literals**:
  - Double quotes `"catalog"."schema"."table"` or `"column"` are for identifiers.
  - Single quotes `'string'` are for string literals.
  - NEVER write `'column_name' = 'val'` — that compares two strings, not the database column!
- **Prohibited Token**: Never include the literal token `'kill'` anywhere in SQL.
- **Distinct Ordered Results**:
  - To select distinct ordered values, use `SELECT DISTINCT col ... ORDER BY col`.
  - Do NOT blindly add `GROUP BY <col>` before `ORDER BY` if there are other unaggregated columns, as Trino will throw a `GROUP BY` validation error.

### Functions that DO NOT EXIST in Trino (Never Use)

| Do NOT Write | Write in Trino Instead |
|---|---|
| `ILIKE '%text%'` | `LOWER(col) LIKE LOWER('%text%')` or `regexp_like(col, '(?i)text')` |
| `DATEADD`, `DATE_SUB`, `ADDDATE` | `date_add('day', n, ts)` or `ts + INTERVAL '1' DAY` |
| `DATEDIFF(a,b)`, `TIMESTAMPDIFF` | `date_diff('day', a, b)` (unit first, then start, then end) |
| `GETDATE()`, `SYSDATE`, `NOW` without () | `current_timestamp`, `now()`, `current_date` |
| `TO_DATE`, `STR_TO_DATE`, `TO_TIMESTAMP` | `date_parse`, `CAST`, `from_iso8601_timestamp` |
| `TO_CHAR`, `STRFTIME` | `format_datetime`, `date_format` |
| `IFNULL`, `NVL`, `ISNULL` | `coalesce(a, b)` |
| `IIF`, `DECODE` | `CASE WHEN ...` or `if(cond, a, b)` |
| `TOP n` | `LIMIT n` (at end of query) |
| `LEN`, `CHARINDEX`, `INSTR` | `length(col)`, `strpos(col, 'sub')` |
| `MEDIAN`, `PERCENTILE_CONT` | `approx_percentile(x, 0.5)` |
| `GROUP_CONCAT`, `STRING_AGG` | `array_join(array_agg(x), ',')` |
| `array_contains(arr, val)` | `contains(arr, val)` |
| `ST_MakePoint`, `ST_GeomFromText` | `ST_Point(lon, lat)`, `ST_GeometryFromText(wkt)` |
| `ST_SetSRID`, `ST_Transform` | None needed (Trino geometry operates directly on coordinates) |
| `ST_DWithin(a, b, r)` | `ST_Distance(to_spherical_geography(a), to_spherical_geography(b)) <= r` |
| `::type` (Postgres cast) | `CAST(x AS type)` |
| `INTERVAL 7 DAY` | `INTERVAL '7' DAY` (value must be quoted string) |
| `EXTRACT(EPOCH FROM ts)` | `to_unixtime(ts)` |


## Sorting & Top-N
- When querying for "the highest" or "the top", return ALL records sharing that top value — not just `LIMIT 1` — unless the user explicitly asks for a single result
- Use `RANK() OVER (ORDER BY col DESC)` when ties must be preserved
- `ROW_NUMBER()` breaks ties arbitrarily — use only when exactly one record per group is needed

## Dates & Times
- Cast explicitly: `CAST(date_string AS DATE)`, `CAST(ts AS TIMESTAMP)`
- Use ISO 8601 strings for date literals: `DATE '2024-01-01'`
- Relative dates: `CURRENT_DATE`, `date_add('day', -N, CURRENT_DATE)`, `date_trunc('month', CURRENT_DATE)`
- Year filter: `col >= DATE '2024-01-01' AND col < DATE '2025-01-01'` (open-ended upper bound is safer than `<= '2024-12-31'` for timestamps)
- Month filter: `date_trunc('month', col) = DATE '2024-01-01'`

## Categorical Filters — The #1 Correctness Issue
The user says "מגורים". The database stores "Residential". They are NOT the same string.

Before writing ANY string equality filter (`WHERE col = '...'`):
1. Check `get_column_profile` — if `domainIsComplete: true`, the example values ARE the full value set
2. Use `search_column_values` with the user's term to find the exact database spelling
3. After writing SQL, pass it through `enrich_sql_filters` as an automatic correctness pass

For `semantic_type: large_categorical` columns: always use `search_column_values` — never assume or use LIKE blindly.

**Interpreting `search_column_values` results:**
- Use the `value` field from each result — that is the exact database string
- High `keyword_similarity` (close to 1.0) = good lexical match
- Low `semantic_distance` (close to 0.0) = good semantic match
- If multiple values match, find a filter or combination of filters (e.g. `col IN ('value1', 'value2', ...)` or a well-crafted LIKE) that covers all variants (unless the question targets a specific one).
- If no values match at all, the filter value may not exist in the data — investigate before writing a WHERE clause that will return 0 rows

## Join Strategy

### Always verify join keys before writing JOIN
- `get_sample_rows` on both sides to check that key formats match (e.g., both are integers, both use the same city_code format)
- `get_table_relationships` for known FK relationships

### Fan-out detection (aggregation correctness)
A 1-to-many JOIN inflates row counts. `COUNT(*)` after such a join is wrong.

Quick diagnostic: compare COUNT before and after the join:
```sql
-- How many flights exist?
SELECT COUNT(*) FROM "postgres"."public"."flights_table" WHERE launch_country = 'France'
-- How many rows after JOIN?  
SELECT COUNT(*) FROM "postgres"."public"."flights_table" f
JOIN "postgres"."public"."flight_alerts_table" a ON f.flight_id = a.flight_id
WHERE f.launch_country = 'France'
```
If second > first → fan-out → use `COUNT(DISTINCT f.flight_id)` or aggregate before joining.

### Join type selection & Distributed Execution Order
- **Big Table on Left, Small Table on Right**: In Trino distributed joins, the right table is broadcast to worker nodes. Always put the largest fact table on the LEFT and the smaller lookup/dimension table on the RIGHT.
- **No Cross Joins**: Every `JOIN` must have an explicit `ON` condition with matching keys.
- **INNER JOIN**: only rows that exist in both tables — use when the relationship is guaranteed.
- **LEFT JOIN**: keep all left-side rows even without a match — use for "per entity" metrics where some entities may have zero related records.
- **Anti-join** (NOT EXISTS / LEFT JOIN ... WHERE right.key IS NULL): entities with NO related records.

### Set Operations (`UNION ALL` vs `UNION`)
- Always prefer `UNION ALL` over `UNION`. In Trino, `UNION` triggers an expensive distributed distinct/deduplication pass across all worker nodes. Only use `UNION` if deduplication across result sets is strictly required.

### Partition Column Pruning
- If the catalog or table metadata indicates a partition column (e.g. `dt`, `date_key`, `p_date`), **always include a filter on the partition column directly in its native type** to avoid scanning entire clusters.


## Window Functions
```sql
-- Rank within group (e.g., top 3 cities per district)
ROW_NUMBER() OVER (PARTITION BY district ORDER BY area_kmr DESC) AS rn
-- Then filter: WHERE rn <= 3
```

## Zero-Row Investigation
If your query returns 0 rows unexpectedly, investigate in this order:
1. String filter values — `search_column_values` or `get_column_distinct_values`
2. Join key alignment — `get_sample_rows` on both tables to verify key format matches
3. Date range logic — is the column a `DATE` or `TIMESTAMP`? Are the casts correct?
4. NULL presence — `get_column_profile` shows null_ratio; high null columns often filter everything out
5. Remove filters one by one to isolate which one eliminates all rows

## SQL Exploration Patterns
When multiple logic paths seem plausible (INNER vs LEFT JOIN, different grouping strategies), run 2–3 variations with `LIMIT 10` and compare results to choose the correct one — don't guess.

When schema is ambiguous, map the user's Hebrew terms to English tables/columns mentally before drafting.

## Per-Capita / Per-Entity Patterns
Notice the franularity difference between the numerator and the denominator. For example in the question:
"alerts per person" = COUNT(alerts) / SUM(population) — not AVG(count_per_row).

When the denominator comes from a different table, aggregate it independently in a CTE, then divide:
```sql
WITH alert_counts AS (
  SELECT district, COUNT(*) AS alerts
  FROM alert_table GROUP BY district
),
populations AS (
  SELECT district, SUM(population) AS pop
  FROM city_table GROUP BY district
)
SELECT a.district, a.alerts * 1.0 / p.pop AS alerts_per_capita
FROM alert_counts a JOIN populations p ON a.district = p.district
```

## Trino Error → Fix Diagnostic Reference

When `execute_trino_query` returns an error, consult this lookup table:

| Error Message Contains | Cause | Corrective Action |
|---|---|---|
| `Cannot apply operator: timestamp = varchar` | Comparing timestamp to text | Convert with `TIMESTAMP '...'` literal or `CAST(varchar_col AS timestamp)`. |
| `Cannot apply operator: varchar = integer` | Column is varchar or literal is unquoted | Use `CAST(col AS integer)` / `try_cast` or quote the literal `'123'`. |
| `Value cannot be cast to timestamp` | Text has `T` / `Z` or custom format | Use `from_iso8601_timestamp(col)` for ISO format, or `date_parse(col, fmt)`. |
| `Invalid format` (in `date_parse`) | Wrong format specifiers | Trino uses MySQL codes (`%Y-%m-%d %H:%i:%s`; note `%i` for minutes, NOT `%M`). |
| `Function 'x' not registered` / `Unknown function` | Invented or non-Trino dialect function | Check "Functions that DO NOT EXIST in Trino" table above and substitute. |
| `Unexpected parameters for function x` | Wrong argument types | Cast parameters to types expected by the function. |
| `Column 'x' cannot be resolved` | Misspelled column or invalid alias | Check exact spelling in metadata profile; verify table alias prefix. |
| `Table 'x' does not exist` | Missing catalog/schema or wrong name | Use fully qualified name: `"catalog"."schema"."table"`. |
| `must be an aggregate expression or appear in GROUP BY` | Non-aggregated column in SELECT | Add the missing column to `GROUP BY` or wrap it in an aggregate function. |
| `mismatched input` / `extraneous input` | Dialect syntax error | Replace backticks with `"`, fix `INTERVAL '1' DAY` (must have quotes), remove `::`. |
| `Division by zero` | Zero denominator | Wrap divisor in `NULLIF(denominator, 0)`. |
| `Scalar sub-query has returned multiple rows` | Subquery returns >1 row | Add `LIMIT 1`, aggregate, or convert to a `JOIN`. |
| `Latitude must be between -90 and 90` | Inverted coordinate order | Coordinates swapped: use `ST_Point(lon, lat)` (longitude first). |
| `ST_Contains ... SphericalGeography` | Containment on geography | `ST_Contains` requires `geometry` (degrees). Convert with `to_geometry()`. |
| `Query exceeded per-node memory limit` | Too much data processed | Narrow the time/partition range, select fewer columns, or pre-aggregate in a CTE. |
| `Filter required on ... for at least one partition column` | Missing partition filter | Add a partition column filter in its native type (e.g. `WHERE dt = '...'`). |


