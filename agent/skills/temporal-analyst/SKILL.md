---
name: temporal-analyst
description: Trino date and time handling — use when queries involve timestamps, date filtering, relative time expressions, date parsing from strings, or time zone conversions. Covers all common string timestamp formats in this database.
---
# Temporal SQL Reference for Trino

## The Core Problem: String-Stored Timestamps

Many columns in this database store dates and times as VARCHAR strings rather than native DATE or TIMESTAMP types.
Before writing any date filter, check `get_column_profile` to confirm the actual data type and look at example values.

If `column_type` is `varchar`, `text`, or `string` — you MUST use string-based parsing.

---

## Identifying the Format from Example Values

Run `get_sample_rows` or check the catalog's example values to identify the format before writing any date filter.

| Example value | Format | Parse function |
|--------------|--------|----------------|
| `2024-01-15` | ISO date | `CAST(col AS DATE)` or `from_iso8601_date(col)` |
| `2024-01-15 14:30:00` | ISO datetime | `CAST(col AS TIMESTAMP)` |
| `2024-01-15T14:30:00` | ISO 8601 with T | `from_iso8601_timestamp(col)` |
| `2024-01-15T14:30:00Z` | ISO 8601 UTC (with Z) | `from_iso8601_timestamp(col)` |
| `15/01/2024` | European DD/MM/YYYY | `date_parse(col, '%d/%m/%Y')` |
| `01/15/2024` | US MM/DD/YYYY | `date_parse(col, '%m/%d/%Y')` |
| `15-Jan-2024` | Text month | `date_parse(col, '%d-%b-%Y')` |
| `20240115` | Compact YYYYMMDD | `date_parse(col, '%Y%m%d')` |
| `1705312200` | Unix epoch (seconds) | `from_unixtime(CAST(col AS BIGINT))` |
| `1705312200000` | Unix epoch (milliseconds) | `from_unixtime(CAST(col AS BIGINT) / 1000.0)` |
| `2024-01-15 14:30:00.000` | With milliseconds | `date_parse(col, '%Y-%m-%d %H:%i:%s.%f')` |

---

## Safe Parsing & Filtering Patterns

### Sargability: Never Wrap Filtered Timestamp Columns in Functions
Wrapping a timestamp/date column in a function prevents Trino from pruning partitions and evaluating min/max metadata statistics:
```sql
-- BAD (slow, prevents partition pruning):
WHERE date(event_at) = DATE '2024-05-01'
WHERE date_format(event_at, '%Y-%m-%d') = '2024-05-01'

-- GOOD (sargable, fast half-open range):
WHERE event_at >= TIMESTAMP '2024-05-01 00:00:00'
  AND event_at <  TIMESTAMP '2024-05-02 00:00:00'
```

### Always cast string dates before comparing
```sql
-- WRONG: string comparison, not date comparison
WHERE date_col > '2024-01-01'

-- CORRECT for ISO date strings
WHERE CAST(date_col AS DATE) >= DATE '2024-01-01'

-- CORRECT for ISO timestamps with T/Z
WHERE from_iso8601_timestamp(date_col) >= TIMESTAMP '2024-01-01 00:00:00'

-- CORRECT for non-ISO strings (e.g. DD/MM/YYYY)
WHERE date_parse(date_col, '%d/%m/%Y') >= DATE '2024-01-01'
```

### Truncating to a period
```sql
-- Group by month
date_trunc('month', CAST(date_col AS TIMESTAMP))

-- Group by year
date_trunc('year', CAST(date_col AS DATE))

-- Extract just the year as integer
YEAR(CAST(date_col AS DATE))
EXTRACT(YEAR FROM CAST(date_col AS DATE))
```

### Range filters (inclusive lower, exclusive upper — always safer for TIMESTAMP)
```sql
-- CORRECT: year 2023
WHERE CAST(date_col AS DATE) >= DATE '2023-01-01'
  AND CAST(date_col AS DATE) < DATE '2024-01-01'

-- CORRECT: month of January 2024
WHERE CAST(date_col AS DATE) >= DATE '2024-01-01'
  AND CAST(date_col AS DATE) < DATE '2024-02-01'

-- AVOID: '2023-12-31' as upper bound for TIMESTAMP — misses 2023-12-31 23:59:59
```

---

## Relative Date Expressions

> [!IMPORTANT]
> In Trino, the `INTERVAL` value **MUST be a quoted string literal**:
> - Write: `INTERVAL '1' DAY` or `INTERVAL '24' HOUR`
> - NEVER write: `INTERVAL 1 DAY` or `INTERVAL '1 day'` or `INTERVAL '1 days'` (all trigger parser syntax errors).

```sql
-- Today
CURRENT_DATE

-- Yesterday
CURRENT_DATE - INTERVAL '1' DAY
-- or: date_add('day', -1, CURRENT_DATE)

-- Last 7 days (rolling)
WHERE CAST(date_col AS DATE) >= CURRENT_DATE - INTERVAL '7' DAY

-- This month
WHERE date_trunc('month', CAST(date_col AS TIMESTAMP)) = date_trunc('month', CURRENT_TIMESTAMP)

-- Last month
WHERE date_trunc('month', CAST(date_col AS TIMESTAMP)) = date_trunc('month', date_add('month', -1, CURRENT_TIMESTAMP))

-- Last N months
WHERE CAST(date_col AS DATE) >= date_add('month', -N, CURRENT_DATE)

-- This year
WHERE YEAR(CAST(date_col AS DATE)) = YEAR(CURRENT_DATE)

-- Last year
WHERE YEAR(CAST(date_col AS DATE)) = YEAR(CURRENT_DATE) - 1
```

---

## Hebrew Temporal Expressions → SQL

| Hebrew | SQL equivalent |
|--------|---------------|
| "אתמול" (yesterday) | `CAST(col AS DATE) = CURRENT_DATE - INTERVAL '1' DAY` |
| "השבוע" (this week) | `CAST(col AS DATE) >= date_trunc('week', CURRENT_DATE)` |
| "החודש" (this month) | `date_trunc('month', CAST(col AS TIMESTAMP)) = date_trunc('month', CURRENT_TIMESTAMP)` |
| "החודש שעבר" (last month) | Use date_add('month', -1, ...) |
| "השנה" (this year) | `YEAR(CAST(col AS DATE)) = YEAR(CURRENT_DATE)` |
| "ב-2023" (in 2023) | `YEAR(CAST(col AS DATE)) = 2023` |
| "בינואר" (in January) | `MONTH(CAST(col AS DATE)) = 1` + year filter |
| "ברבעון הראשון" (Q1) | `MONTH(CAST(col AS DATE)) BETWEEN 1 AND 3` |
| "ב-12 החודשים האחרונים" (last 12 months) | `CAST(col AS DATE) >= date_add('month', -12, CURRENT_DATE)` |
| "לפני X ימים" (X days ago) | `CAST(col AS DATE) = date_add('day', -X, CURRENT_DATE)` |

---

## Time Zone Handling

If the column stores UTC and you need local time (Israel = UTC+3 / UTC+2 depending on DST):
```sql
-- Convert UTC timestamp string to Israel local time
AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Jerusalem'

-- Example
SELECT date_parse(col, '%Y-%m-%dT%H:%i:%s') AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Jerusalem'
```

---

## Duration and Interval Calculations

```sql
-- Difference in days between two date strings
date_diff('day',
  CAST(start_date_col AS DATE),
  CAST(end_date_col AS DATE)
)

-- Difference in hours between two timestamps
date_diff('hour',
  date_parse(start_col, '%Y-%m-%d %H:%i:%s'),
  date_parse(end_col, '%Y-%m-%d %H:%i:%s')
)
```

---

## Defensive Patterns

### When the format may vary or the column has NULLs
```sql
-- TRY_CAST returns NULL instead of throwing on bad values
WHERE TRY_CAST(date_col AS DATE) >= DATE '2024-01-01'

-- Check if parsing succeeds before using the column
SELECT *
FROM table
WHERE TRY_CAST(date_col AS DATE) IS NOT NULL
  AND CAST(date_col AS DATE) >= DATE '2024-01-01'
```

### When you don't know the format — investigate first
```sql
-- Sample 20 distinct values to see the actual format
SELECT DISTINCT date_col FROM "catalog"."schema"."table" LIMIT 20
```
Then use `get_column_profile` to see example values before writing the parse call.

---

## Common Mistakes to Avoid

- `WHERE date_col = '2024-01-15'` on a TIMESTAMP column — matches only midnight, not the whole day
- `WHERE date_col BETWEEN '2024-01-01' AND '2024-12-31'` on a TIMESTAMP — misses Dec 31 23:59
- `date_parse` with wrong format string — returns NULL silently or throws an error
- Using `NOW()` — Trino uses `CURRENT_TIMESTAMP` (no parentheses needed)
- Using `DATEDIFF` — Trino uses `date_diff('unit', start, end)`
- Using `DATE_FORMAT` — Trino uses `format_datetime(ts, 'pattern')`
