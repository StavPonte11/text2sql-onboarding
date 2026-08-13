---
name: spatial-analyst
description: Rules for handling geospatial queries in Trino, including WKT strings, spherical geography distances, and geometry construction.
---
# Spatial SQL Analyst Guidelines

When your query involves locations, points, polygons, or geographic distances, strictly adhere to these Trino Geo-Spatial rules:

## Geometry Construction
* **WKT to Geometry**: When constructing geometries from Well-Known Text (WKT), ALWAYS use `ST_GeometryFromText(wkt_string)`. 
* **NEVER** use `ST_GeomFromText()`. It does not exist in Trino and will fail.

## Distances and Projections
* **Spherical Geography**: Trino's `ST_Distance` calculates distance in the units of the spatial reference system. If your coordinates are in WGS84 (longitude/latitude in degrees), `ST_Distance` will return degrees, which is completely useless for physical distance measurements.
* **Meters/Kilometers**: To calculate physical distance on the Earth's surface (in meters), you MUST cast the geometries to spherical geography using `toSphericalGeography()`.
  * **Example**: `ST_Distance(toSphericalGeography(geom1), toSphericalGeography(geom2))`
  * This returns the distance in **meters**. Divide by 1000 if you need kilometers.

## Resolution
* **Use the Resolver**: If the user provides a Hebrew location name (e.g., "תל אביב" or "כביש 1"), NEVER try to guess its coordinates or WKT. Always use the `resolve_location_to_wkt` tool first, and then inject the exact returned WKT into your `ST_GeometryFromText()` call.
