---
name: temporal-analyst
description: Trino date and time handling — use when queries involve timestamps, date filtering, relative time expressions (including Hebrew ones), date parsing from strings, or time zone conversions.
---
# Temporal SQL Reference for Trino

## Step 1 — Know the column type

Check `get_column_profile` (or the catalog) before writing any date filter:

- **Native `date` / `timestamp`** → compare the column directly against date/timestamp literals with half-open
  ranges. Never wrap it in a function in `WHERE` (that defeats partition pruning and min/max statistics).
- **`varchar` / text** → the value must be parsed first. Identify the format from example values or
  `get_sample_rows`, parse once (ideally in a CTE), then filter the parsed value.

| Example value | Format | Parse function |
|--------------|--------|----------------|
| `2024-01-15` | ISO date | `CAST(col AS DATE)` or `from_iso8601_date(col)` |
| `2024-01-15 14:30:00` | ISO datetime | `CAST(col AS TIMESTAMP)` |
| `2024-01-15T14:30:00` | ISO 8601 with T | `from_iso8601_timestamp(col)` |
| `2024-01-15T14:30:00Z` | ISO 8601 UTC | `from_iso8601_timestamp(col)` |
| `15/01/2024` | DD/MM/YYYY | `date_parse(col, '%d/%m/%Y')` |
| `01/15/2024` | MM/DD/YYYY | `date_parse(col, '%m/%d/%Y')` |
| `15-Jan-2024` | Text month | `date_parse(col, '%d-%b-%Y')` |
| `20240115` | YYYYMMDD | `date_parse(col, '%Y%m%d')` |
| `1705312200` | Unix seconds | `from_unixtime(CAST(col AS BIGINT))` |
| `1705312200000` | Unix milliseconds | `from_unixtime(CAST(col AS BIGINT) / 1000.0)` |
| `2024-01-15 14:30:00.000` | With milliseconds | `date_parse(col, '%Y-%m-%d %H:%i:%s.%f')` |

`date_parse` uses MySQL-style specifiers: `%i` is minutes, `%s` seconds, `%H` 24-hour.

---

## Step 2 — Filter with half-open ranges

```sql
SELECT count(*) FROM "catalog"."schema"."events"
WHERE event_at >= TIMESTAMP '2024-05-01 00:00:00'
  AND event_at <  TIMESTAMP '2024-05-02 00:00:00'
```

```sql
SELECT count(*) FROM "catalog"."schema"."events"
WHERE event_date >= DATE '2023-01-01'
  AND event_date <  DATE '2024-01-01'
```

For VARCHAR columns, parse in a CTE and filter the parsed value the same way:

```sql
WITH parsed AS (
  SELECT e.*, date_parse(e.created_str, '%d/%m/%Y') AS created_at
  FROM "catalog"."schema"."events" e
)
SELECT count(*) FROM parsed
WHERE created_at >= TIMESTAMP '2024-01-01 00:00:00'
  AND created_at <  TIMESTAMP '2024-02-01 00:00:00'
```

Avoid `BETWEEN '2024-01-01' AND '2024-01-31'` on timestamps — it misses everything after midnight on the 31st.

---

## Relative dates

`INTERVAL` values must be quoted string literals: `INTERVAL '1' DAY`, `INTERVAL '3' MONTH`
(never `INTERVAL 1 DAY` or `INTERVAL '1 day'`). `current_date`, `current_timestamp` and `now()` are all valid.

| Period | Sargable filter on a native column `d` |
|---|---|
| Today | `d >= current_date AND d < current_date + INTERVAL '1' DAY` |
| Yesterday | `d >= current_date - INTERVAL '1' DAY AND d < current_date` |
| Last 7 days (rolling) | `d >= current_date - INTERVAL '7' DAY` |
| This month | `d >= date_trunc('month', current_date) AND d < date_trunc('month', current_date) + INTERVAL '1' MONTH` |
| Last month | `d >= date_trunc('month', current_date) - INTERVAL '1' MONTH AND d < date_trunc('month', current_date)` |
| This year | `d >= date_trunc('year', current_date) AND d < date_trunc('year', current_date) + INTERVAL '1' YEAR` |
| Last 12 months | `d >= current_date - INTERVAL '12' MONTH` |

---

## Hebrew temporal expressions

The Israeli week starts on **Sunday**, but Trino's `date_trunc('week', ...)` returns **Monday**. Sunday-based week start:
`date_add('day', -(day_of_week(current_date) % 7), current_date)` (`day_of_week`: Monday = 1 … Sunday = 7).

| Hebrew | Meaning | Filter on a native column `d` |
|--------|---------|-------------------------------|
| "היום" | today | `d >= current_date AND d < current_date + INTERVAL '1' DAY` |
| "אתמול" | yesterday | `d >= current_date - INTERVAL '1' DAY AND d < current_date` |
| "השבוע" | this week (Sun–Sat) | `d >= date_add('day', -(day_of_week(current_date) % 7), current_date)` |
| "השבוע שעבר" | last week (Sun–Sat) | `d >= date_add('day', -(day_of_week(current_date) % 7) - 7, current_date) AND d < date_add('day', -(day_of_week(current_date) % 7), current_date)` |
| "החודש" | this month | see "This month" above |
| "החודש שעבר" | last month | see "Last month" above |
| "השנה" | this year | see "This year" above |
| "ב-2023" | in 2023 | `d >= DATE '2023-01-01' AND d < DATE '2024-01-01'` |
| "בינואר 2024" | January 2024 | `d >= DATE '2024-01-01' AND d < DATE '2024-02-01'` |
| "ברבעון הראשון 2024" | Q1 2024 | `d >= DATE '2024-01-01' AND d < DATE '2024-04-01'` |
| "ב-12 החודשים האחרונים" | last 12 months | `d >= current_date - INTERVAL '12' MONTH` |
| "לפני X ימים" | X days ago | `d >= current_date - INTERVAL 'X' DAY AND d < current_date - INTERVAL 'X' DAY + INTERVAL '1' DAY` |
| "בשבועיים הראשונים של יולי 2026" | July 1–14, 2026 | `d >= DATE '2026-07-01' AND d < DATE '2026-07-15'` |

If a month is given without a year, assume the most recent past occurrence relative to today and state it in `assumptions`.

---

## Grouping by period (SELECT / GROUP BY — functions are fine here)

```sql
SELECT date_trunc('month', event_at) AS month, count(*) AS events
FROM "catalog"."schema"."events"
GROUP BY 1
ORDER BY 1
```

`year(d)`, `month(d)`, `day_of_week(d)` and `extract(year FROM d)` are fine in `SELECT` and `GROUP BY` — just not in
`WHERE` on a native column you are filtering.

---

## Time zones

A naive `timestamp` has no zone. To treat it as UTC and show Israel local time:

```sql
SELECT with_timezone(event_at, 'UTC') AT TIME ZONE 'Asia/Jerusalem' AS event_at_israel
FROM "catalog"."schema"."events"
```

For a value that already carries a zone (`timestamp with time zone`, e.g. from `from_iso8601_timestamp('...Z')`),
`col AT TIME ZONE 'Asia/Jerusalem'` is enough. Israel is UTC+2 in winter and UTC+3 in summer (DST).

---

## Durations

```sql
SELECT date_diff('day', start_date, end_date) AS days,
       date_diff('hour', started_at, ended_at) AS hours
FROM "catalog"."schema"."trips"
```

Unit first, then start, then end.

---

## Defensive parsing

`try_cast(col AS DATE)` and `try(date_parse(col, '%d/%m/%Y'))` return NULL instead of failing on malformed values.
When the format is unknown, look before parsing:

```sql
SELECT DISTINCT created_str FROM "catalog"."schema"."events" LIMIT 20
```

---

## Common mistakes

- `WHERE ts_col = '2024-01-15'` on a timestamp — matches only midnight, not the whole day.
- `WHERE date(ts_col) = DATE '2024-05-01'` — correct but not sargable; use a half-open range.
- Wrong `date_parse` format string — returns an error or NULL; `%i` (not `%M`) is minutes.
- `DATEDIFF(a, b)` does not exist — use `date_diff('day', a, b)`.
- `date_format(ts, '%Y-%m')` (MySQL-style specifiers) and `format_datetime(ts, 'yyyy-MM')` (Joda-style patterns)
  both exist; don't mix the two pattern styles.
- `date_trunc('week', ...)` is Monday-based — use the Sunday formula above for Hebrew "השבוע".
