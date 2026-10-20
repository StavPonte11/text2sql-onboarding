---
name: spatial-analyst
description: Geospatial query patterns for Trino, including Hebrew geographical extraction, per-location decision trees, polygon & point handling, proximity & directional filtering, and spatial join acceleration.
---
# Spatial & Geographical SQL Reference for Trino

## 1. Hebrew Location Extraction & Disambiguation Rules

When analyzing user requests in Hebrew, apply these linguistic rules before resolving or searching:

### A. Preposition Stripping vs. Structural Words
- **Strip grammatical prefixes**: `ב-`, `ל-`, `מ-`, `כ-`, `ו-` (e.g. `בבינת ג'בייל` → `בינת ג'בייל`, `מהר דב` → `הר דב`).
- **NEVER strip structural words that belong to the proper place name**:
  - `אל-` (e.g. `אל-ח'יאם`)
  - `כפר` (e.g. `כפר קאסם`, `כפר כנא`)
  - `נחל` (e.g. `נחל קישון`, `נחל עיראק`)
  - `הר` (e.g. `הר דב`, `הר חרמון`)
  - `מחנה` (e.g. `מחנה נחשונים`, `מחנה בלאטה`)
  - `ואדי` (e.g. `ואדי עארה`)

### B. Expand Acronyms & Abbreviations
Expand geographical abbreviations before querying or resolving:
- `ת״א` → `תל אביב`
- `י-ם` → `ירושלים`
- `איו״ש` → `יהודה ושומרון`
- `ארה״ב` → `ארצות הברית`

### C. Disambiguate Non-Geographical Entities (False Positives)
- **Sports Teams**: `מכבי חיפה`, `הפועל ירושלים` → organization names, NOT location filters (unless the query asks about games taking place *in* the city).
- **Restaurants / Venues**: `מסעדת אוסלו` → venue name, ignore for city extraction.
- **Personal Names**: `גולן ושרון קבעו פגישה` → people's names, not regions.

### D. Comprehensive Geographical Scope
Locations are not just major cities. Recognize and extract:
- Populated cities, towns, villages (`כפרים`), kibbutzim, moshavim.
- Outposts, refugee camps (`מחנות פליטים`), military bases (`מחנות צבאיים`).
- Mountains, peaks, hills, valleys, rivers, wadis.
- Aggregate geopolitical regions (`עוטף עזה`, `גוש דן`, `דרום לבנון`, `בקעת הירדן`).

---

## 2. The Nuanced Per-Location Decision Strategy

Evaluate **EACH location mentioned in the query independently**. A single query may involve multiple locations that should be handled differently.

```
For EACH location mentioned:

1. Proximity or Directional Query? ("סביב", "מצפון ל-", "ברדיוס")
   ↳ Coordinates are inherently required (great_circle_distance or lat/lon condition).

2. Regional Area / Agglomeration? ("גוש דן", "עוטף עזה", "הגליל העליון")
   ↳ Text columns rarely contain these aggregate areas.
   ↳ Call `resolve_location_to_wkt` and filter coordinate columns with `ST_Contains`.

3. Specific City or Settlement? ("חיפה", "חאן יונס", "כפר קאסם")
   ↳ Inspect candidate table:
     • If an administrative column exists (`city_name`, `settlement`) AND contains the value:
       Prefer direct text equality (fast, indexed, avoids boundary edge clipping).
     • If the table ONLY has coordinates (`lat`/`lon`, `geom`):
       Call `resolve_location_to_wkt` and filter spatially.
     • If the city value cannot be found (catalog, `search_column_values` for large_categorical columns):
       Fall back to `resolve_location_to_wkt`.

4. The "No Blind OR" Rule:
   • Do NOT write `city = 'Haifa' OR ST_Contains(...)` blindly as a lazy habit.
   • ONLY combine with OR if the data is genuinely hybrid (e.g. telemetry tables where 
     some rows have manual city tags while others only have raw GPS sensor pings).
```

---

## 3. Handling Areas and Points

`resolve_location_to_wkt(location_name)` geocodes a place (Hebrew names welcome) and returns **area geometries only**
(`POLYGON` / `MULTIPOLYGON`, simplified to fit in SQL). It never returns a point. Use `data.sql_literal` — it is already
a quoted SQL string literal — directly inside `ST_GeometryFromText(...)`.

### A. Areas (cities, regions, districts)
```sql
SELECT count(*) FROM "catalog"."schema"."events" e
WHERE ST_Contains(ST_GeometryFromText('POLYGON((34.75 32.05, 34.85 32.05, 34.85 32.12, 34.75 32.12, 34.75 32.05))'),
                  ST_Point(e.lon, e.lat))
```
On large tables, pre-filter with a cheap numeric bounding box first:
```sql
SELECT count(*) FROM "catalog"."schema"."events" e
WHERE e.lat BETWEEN 32.05 AND 32.12
  AND e.lon BETWEEN 34.75 AND 34.85
  AND ST_Contains(ST_GeometryFromText('POLYGON((34.75 32.05, 34.85 32.05, 34.85 32.12, 34.75 32.12, 34.75 32.05))'),
                  ST_Point(e.lon, e.lat))
```

### B. Points (landmarks, peaks, bases, user-given coordinates)
Points come from coordinates the user gives or from coordinate columns in a reference table — not from
`resolve_location_to_wkt`. A point has zero area, so "in / at / near" a landmark means a radius
(default ~500 m – 1 km; state the radius in `assumptions`):
```sql
SELECT count(*) FROM "catalog"."schema"."events" e
WHERE great_circle_distance(e.lat, e.lon, 33.30, 35.75) <= 0.5
```
```sql
SELECT count(*) FROM "catalog"."schema"."events" e
WHERE ST_Distance(to_spherical_geography(ST_Point(e.lon, e.lat)),
                  to_spherical_geography(ST_Point(35.75, 33.30))) <= 500
```
If the landmark is only known by name, `resolve_location_to_wkt` gives the surrounding area polygon; use its
centroid `ST_Centroid(ST_GeometryFromText(...))` as the point, and say so in `assumptions`.

---

## 4. Proximity & Directional Intent Patterns

When the user asks for relative spatial relations, translate the intent into precise coordinate math:

| Phrasing | Semantic Meaning | Recommended SQL Pattern |
|---|---|---|
| **"ברדיוס X ק"מ מ..."** | Explicit radius distance | `great_circle_distance(lat, lon, target_lat, target_lon) <= X` |
| **"סביב..." / "באזור..." (for a Point/City)** | Implicit proximity | For a Point: `great_circle_distance(...) <= 1.0` (1 km)<br>For a Polygon: `ST_Contains(...)` or buffer $\le 2\text{ km}$ |
| **"מצפון ל..."** (North of) | Directional filter | `lat > target_lat` (with reasonable lon bounding box) |
| **"מדרום ל..."** (South of) | Directional filter | `lat < target_lat` (with reasonable lon bounding box) |
| **"ממזרח ל..." / "ממערב ל..."** | Directional filter | `lon > target_lon` / `lon < target_lon` |

---

## 5. Critical Coordinate Order & Syntax Rules

- **`ST_Point(lon, lat)`**: **LONGITUDE FIRST** ($X = \text{lon}, Y = \text{lat}$).
- **WKT format**: also **`POINT(lon lat)`** — e.g. `'POINT(34.78 32.08)'` (Tel Aviv).
- **`great_circle_distance(lat1, lon1, lat2, lon2)`**: **LATITUDE FIRST**, and returns **km**.
- **`bing_tile_at(lat, lon, zoom)`**: **LATITUDE FIRST**.
- **Israel Sanity Check**: In Israel, Longitude $\approx 34\text{--}36$ and Latitude $\approx 29\text{--}33$. If coordinates show `lon: 32` and `lat: 34`, they are accidentally inverted!

### Geometry Functions
- **Always**: `ST_GeometryFromText(wkt_string)`
- **Never**: `ST_GeomFromText()` or `ST_PolygonFromText()` — they do not exist in Trino.
- `ST_Distance` on WGS84 geometries returns **degrees**, not meters. For meters, wrap both sides in `to_spherical_geography(...)`.
- `ST_Contains` works on `geometry` (degrees), **NOT** on `SphericalGeography`.

### Spatial Joins Acceleration
Put `ST_Contains` directly in the `JOIN ... ON` clause so Trino invokes its internal spatial join optimization:
```sql
SELECT z.zone_name, count(*) AS event_count
FROM "catalog"."schema"."events" e
JOIN "catalog"."schema"."zones" z
  ON ST_Contains(ST_GeometryFromText(z.wkt), ST_Point(e.lon, e.lat))
GROUP BY z.zone_name
```
