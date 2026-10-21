---
name: spatial-analyst
description: Geospatial SQL in Trino — using the location-analyst plan, geometry placeholders, coordinate order, distance units and spatial join acceleration.
---
# Spatial & Geographical SQL Reference for Trino

## 1. Who does what

Places are handled by the `location-analyst` subagent (delegate with `task`; give it the question verbatim and the candidate tables). It:
1. extracts and normalises Hebrew place names (prefixes, acronyms, aliases, teams/venues/people excluded),
2. decides per place whether a verified categorical column answers the question or geometry is needed,
3. resolves geometry only for spatial operations, with its real type (point / line / area — never a bounding box as a boundary),
and returns a `LocationPlan`. Each usage has a `status`:

| status | meaning | what to do |
|---|---|---|
| `ready` | `predicate_sql` is checked and rendered | put it in WHERE (AND), qualify columns with your alias |
| `ambiguous` | e.g. "near" without a distance, several places match | ask the user (`needs_clarification`) or state a justified assumption |
| `needs_parent_judgment` | directional relations | choose the reference (center vs. extent) yourself and state it in `assumptions` |
| `unresolved` / `unsupported` | value/column not verified, no geometry, wrong geometry type | do not filter on it; read `issues` |

Never OR a categorical and a spatial predicate for the same place unless `combination_note` justifies it by the data (e.g. some rows only have coordinates).

## 2. Geometry placeholders

Geometry never travels through the conversation. `predicate_sql` refers to it as a quoted placeholder,
`ST_GeometryFromText('@khan_yunis_relation_1234_multipolygon_wkt@')`. Copy it exactly; `execute_trino_query`,
`validate_sql_syntax` and the final-answer check substitute the real WKT, and the SQL shown to the user is the substituted one.
An unknown placeholder is rejected with `INVALID_INPUT`.

```sql
SELECT count(*) FROM "catalog"."schema"."events" e
WHERE ST_Contains(ST_GeometryFromText('POLYGON((34.75 32.05, 34.85 32.05, 34.85 32.12, 34.75 32.12, 34.75 32.05))'),
                  ST_Point(e.lon, e.lat))
```
On large tables, add a cheap numeric pre-filter from the feature's `bbox` (an extent, so only as an extra AND, never instead of the geometry):
```sql
SELECT count(*) FROM "catalog"."schema"."events" e
WHERE e.lat BETWEEN 32.05 AND 32.12
  AND e.lon BETWEEN 34.75 AND 34.85
  AND ST_Contains(ST_GeometryFromText('POLYGON((34.75 32.05, 34.85 32.05, 34.85 32.12, 34.75 32.12, 34.75 32.05))'),
                  ST_Point(e.lon, e.lat))
```

## 3. Distances

- Point to point: `great_circle_distance(lat1, lon1, lat2, lon2)` — **latitude first**, result in **km**.
```sql
SELECT count(*) FROM "catalog"."schema"."events" e
WHERE great_circle_distance(e.lat, e.lon, 33.30, 35.75) <= 2
```
- `ST_Distance(to_spherical_geography(a), to_spherical_geography(b))` gives metres **only for points**.
- `ST_Distance` / `ST_Buffer` on plain geometry work in **degrees** — never compare them with metres.
- Distance to a boundary, river or area: the plan supplies a metre-accurate buffer polygon (built in Python) and an `ST_Contains` / `ST_Intersects` predicate. Do not rebuild it with degree buffers.
- No distance given ("ליד", "באזור"): there is no universal default radius — ask, or state a justified assumption.

**If the Utils-Server tools are available:** check a spatial expression with `trino_validate_spatial_expression`
before putting it in a query. It reports whether the expression is valid and the functions exist on this Trino, with
close-name suggestions for typos, and can evaluate it on one row of a table.

## 4. Coordinate order & syntax

- **`ST_Point(lon, lat)`**: **LONGITUDE FIRST** (X = lon, Y = lat). WKT too: `'POINT(34.78 32.08)'` (Tel Aviv).
- **`great_circle_distance(lat1, lon1, lat2, lon2)`** and **`bing_tile_at(lat, lon, zoom)`**: **LATITUDE FIRST**.
- Israel sanity check: longitude ≈ 34–36, latitude ≈ 29–33. `lon: 32, lat: 34` means the columns are swapped.
- Always `ST_GeometryFromText(wkt)` — `ST_GeomFromText()` / `ST_PolygonFromText()` do not exist in Trino.
- `ST_Contains` works on `geometry`, **not** on `SphericalGeography`.
- `ST_Point(NULL, …)` is NULL, so rows without coordinates never match a spatial predicate — say so when it matters.

## 5. Directional relations ("מצפון ל…")

There is no automatic rendering. Decide whether the reference is the place's center or its whole extent (`bbox` from the plan), e.g. "north of Gaza" usually means north of its northern edge (`lat > max_lat`), not of its center. Add a longitude band only when the question implies one, and state the choice in `assumptions`.

## 6. Spatial join acceleration

Put `ST_Contains` directly in the `JOIN ... ON` clause so Trino uses its spatial join:
```sql
SELECT z.zone_name, count(*) AS event_count
FROM "catalog"."schema"."events" e
JOIN "catalog"."schema"."zones" z
  ON ST_Contains(ST_GeometryFromText(z.wkt), ST_Point(e.lon, e.lat))
GROUP BY z.zone_name
```
