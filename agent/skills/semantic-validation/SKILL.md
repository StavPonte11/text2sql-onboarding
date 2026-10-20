---
name: semantic-validation
description: Semantic correctness checks for Text-to-SQL — use when a query has executed successfully but you need to verify it actually answers the user's intent, especially for aggregations, joins, and complex conditions.
---
# Semantic Validation Reference

## When to apply semantic validation

Apply at least a mental checklist — and targeted diagnostic queries — when:
- The query uses **JOINs** (risk: fan-out multiplication)
- The query uses **aggregations**: `COUNT`, `SUM`, `AVG`, `MIN`, `MAX`
- The question asks for **"per X"** metrics (risk: wrong grouping unit)
- The question uses **"most"**, **"top N"**, **"highest"**, **"total"**
- The filter involves **dates, ranges, or boundaries**
- The result is **0 rows** or an **unexpectedly round/large number**
- The question involves **"customers/entities with NO related records"** (anti-join risk)

---

## The Output Grain Test

Before writing SQL, answer: **What does one output row represent?**

Match this to the user's question:
- "כמה X היו בY?" → one row = COUNT of X (scalar)
- "מה X עשו בY?" → one row = (metric, X) pair
- "הכי הרבה X ב-Y" → one row = (X, metric) pair
- "X לנפש" → one row = (entity, per-capita metric)

If the grain is wrong, every aggregation in the query is suspect.

---

## Join Fan-Out Check (Critical for aggregations)

A JOIN on a 1-to-many relationship **multiplies rows**. `COUNT(*)` after such a join counts inflated rows, not entities.

**The diagnostic**: After joining, verify the row count on each side independently.

```sql
-- Step 1: count entities in the left table
SELECT COUNT(*) FROM "catalog"."schema"."left_table"
WHERE <filter>;

-- Step 2: count rows after the JOIN
SELECT COUNT(*) FROM "catalog"."schema"."left_table" l
JOIN "catalog"."schema"."right_table" r ON l.id = r.left_id
WHERE <filter>;
```

If Step 2 > Step 1, rows are being duplicated. Fix options:
- Use `COUNT(DISTINCT l.id)` instead of `COUNT(*)`
- Use a subquery/CTE to aggregate first, then join
- Join on a truly unique key

**Example pattern:**
```sql
-- WRONG: counts alert rows, not flights
SELECT COUNT(*) FROM flights f JOIN alerts a ON f.id = a.flight_id

-- CORRECT: count distinct flights that have alerts
SELECT COUNT(DISTINCT f.id) FROM flights f JOIN alerts a ON f.id = a.flight_id
```

---

## Aggregation Semantics

| User says | Probably means | Watch out for |
|-----------|----------------|----------------|
| "כמה" (how many) | COUNT of entities | COUNT(*) after 1-to-many join |
| "ממוצע" (average) | AVG() | Is it avg-of-averages vs avg-of-rows? |
| "סה"כ" (total) | SUM() | NULL rows excluded from SUM |
| "הגבוה ביותר" (highest) | MAX() or ORDER BY ... LIMIT 1 | Ties — should you return all? |
| "לנפש" (per capita/person) | division by count | Division by zero on NULL |
| "לפי X" (by X) | GROUP BY X | Did you group at the right grain? |

---

## Filter Boundary Checks

Date/time ranges:
- **"בחודש ינואר"** → `BETWEEN '2024-01-01' AND '2024-01-31'` or `date_trunc('month', col) = DATE '2024-01-01'`
- **"ב-2023"** → `YEAR(col) = 2023` or `col >= DATE '2023-01-01' AND col < DATE '2024-01-01'`
- Check if the column is `DATE` or `TIMESTAMP` — compare types matter

Categorical filters:
- After `search_column_values`, use the **exact string returned** — never modify it
- If `LIKE` is necessary (large_categorical prefix pattern), use `LIKE 'Tel Aviv%'` not `LIKE '%Tel Aviv%'` unless you need interior match

---

## Anti-join Patterns (entities with NO related records)

```sql
-- CORRECT: entities with no matching records
SELECT c.id FROM customers c
WHERE NOT EXISTS (
  SELECT 1 FROM orders o WHERE o.customer_id = c.id
)

-- ALSO CORRECT (but watch for NULL-join behavior)
SELECT c.id FROM customers c
LEFT JOIN orders o ON o.customer_id = c.id
WHERE o.id IS NULL
```

Do NOT use `c.id NOT IN (SELECT customer_id FROM orders)` if `customer_id` can be NULL — this returns 0 rows when any NULL exists.

---

## The Intent-Preservation Quick-Check

Before finalizing the response, mentally translate your SQL back to plain language and compare filter-by-filter to the user's question:

1. Does each WHERE clause correspond to a condition the user mentioned?
2. Did you include a condition that the user did NOT mention?
3. Is the GROUP BY at the right entity level?
4. Does the SELECT list include what the user asked for (and nothing unasked)?
5. Are ties handled (RANK vs ROW_NUMBER vs LIMIT)?

If any answer is "no" or "unsure" — revise the SQL before executing.

---

## When results are suspicious — diagnostic queries

**0 rows returned:**
1. Strip WHERE clauses one at a time to find which one eliminates all rows
2. `search_column_values` on any string filter you wrote
3. Sample the join keys: `get_sample_rows` on both sides — do the key formats match?

**Count too high (round number like 10000 or identical to table size):**
1. Run `SELECT COUNT(*) FROM table` — if equal, your WHERE clause may be doing nothing
2. Check for Cartesian product: does the JOIN have a proper ON condition?

**Count too low (0 or 1 when many are expected):**
1. Check for strict INNER JOIN when LEFT JOIN is needed
2. Check for NULL key: `WHERE key IS NOT NULL` accidentally excluded nulls?

**Average looks wrong:**
1. Compare with a manual spot-check: `SELECT col FROM table WHERE condition LIMIT 10`
2. Verify the denominator: are you dividing by count of rows or count of entities?
