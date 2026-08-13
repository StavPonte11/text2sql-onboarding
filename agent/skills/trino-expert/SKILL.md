---
name: trino-expert
description: Advanced rules and syntax for Trino SQL queries, including arrays, JSON, aggregations, and datetime formats.
---
# Trino SQL Expert Guidelines

When writing Trino SQL queries, strictly follow these syntax guidelines:

## Arrays and Collections
* **Searching Arrays**: When checking if a value exists in an array, ALWAYS use `contains(array, value)`. NEVER use `array_contains()`.
* **Unnesting**: If you need to join against an array column, use `CROSS JOIN UNNEST(array_col) AS t(element)`.

## Nulls and Aggregations
* **Avoiding Nulls**: Wrap each column used in an aggregation with `COALESCE(column, 0)` to avoid NULLs ruining your calculation.
* **Counts**: When counting entities, always apply `DISTINCT` on the identifier column (e.g., `COUNT(DISTINCT id)`).

## Sorting and Limits
* **Distinct Sorting**: When using `ORDER BY <col>`, always add `GROUP BY <col>` before to select distinct values if you are returning unique records.
* **Top Values**: When querying for top (max/min) values, return ALL records that share the top value rather than applying a strict `LIMIT 1`, unless a secondary sorting is explicitly requested by the user.

## Dates and Times
* **Format**: Always work with ISO 8601 format strings.
* **Casting**: Explicitly `CAST(date_string AS DATE)` or `CAST(timestamp_string AS TIMESTAMP)` when performing date math.

## Schema Linking
* **Complex Domains**: If the user's Hebrew request involves complex domain logic or a highly fragmented database schema, mentally map Hebrew terms to English tables/columns before drafting SQL.
* **Diverse Synthesis**: If multiple SQL logic paths seem plausible (e.g., unsure whether to use an INNER JOIN vs a LEFT JOIN), you can generate 2 or 3 variations, execute them all using `execute_trino_query` with `LIMIT 10`, and compare the results to deduce the correct approach.
