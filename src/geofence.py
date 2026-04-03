from typing import List, Tuple, Dict, Any


Point = Tuple[float, float]  # (lat, lon)


def point_in_polygon(point: Point, polygon: List[Point]) -> bool:
    lat, lon = point
    inside = False
    j = len(polygon) - 1
    for i in range(len(polygon)):
        yi, xi = polygon[i]  # lat, lon
        yj, xj = polygon[j]
        intersect = ((xi > lon) != (xj > lon)) and (lat < (yj - yi) * (lon - xi) / (xj - xi + 1e-12) + yi)
        if intersect:
            inside = not inside
        j = i
    return inside


def polygon_bbox(polygon: List[Point]) -> Tuple[float, float, float, float]:
    lats = [p[0] for p in polygon]
    lons = [p[1] for p in polygon]
    return (min(lons), min(lats), max(lons), max(lats))  # minLon,minLat,maxLon,maxLat


def bbox_intersects(a: Tuple[float, float, float, float], b: Tuple[float, float, float, float]) -> bool:
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def geojson_to_rings(geojson: Dict[str, Any]) -> List[List[Point]]:
    t = str((geojson or {}).get("type") or "")
    rings: List[List[Point]] = []
    if t == "Polygon":
        coords = geojson.get("coordinates") or []
        if coords:
            outer = coords[0]
            ring = [(float(lat), float(lon)) for lon, lat in outer]
            rings.append(ring)
    if t == "MultiPolygon":
        mcoords = geojson.get("coordinates") or []
        for poly in mcoords:
            if not poly:
                continue
            outer = poly[0]
            ring = [(float(lat), float(lon)) for lon, lat in outer]
            rings.append(ring)
    return rings


def rings_to_geojson_polygon(ring: List[Point]) -> Dict[str, Any]:
    coords = [[float(lon), float(lat)] for (lat, lon) in ring]
    if coords and coords[0] != coords[-1]:
        coords.append(coords[0])
    return {"type": "Polygon", "coordinates": [coords]}
