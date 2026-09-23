import math


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Tính khoảng cách giữa 2 tọa độ (kinh độ, vĩ độ) theo đơn vị km
    sử dụng công thức Haversine.
    """
    earth_radius_km = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return round(earth_radius_km * c, 2)


def get_bounding_box(
    lat: float,
    lon: float,
    radius_km: float,
) -> tuple[float, float, float, float]:
    """
    Tính toán hình chữ nhật bao quanh (Bounding Box) cho toạ độ tâm và bán kính (km).
    - 1 độ vĩ tuyến ~ 111 km
    - 1 độ kinh tuyến ~ 111 * cos(lat) km
    Trả về: (min_lat, max_lat, min_lon, max_lon)
    """
    if radius_km <= 0:
        return lat, lat, lon, lon

    delta_lat = radius_km / 111.0
    cos_lat = math.cos(math.radians(lat))
    # Tránh chia cho 0 tại cực
    delta_lon = radius_km / (111.0 * max(0.01, abs(cos_lat)))

    min_lat = max(-90.0, lat - delta_lat)
    max_lat = min(90.0, lat + delta_lat)
    min_lon = max(-180.0, lon - delta_lon)
    max_lon = min(180.0, lon + delta_lon)

    return (
        round(min_lat, 6),
        round(max_lat, 6),
        round(min_lon, 6),
        round(max_lon, 6),
    )


def is_point_within_radius(
    center_lat: float,
    center_lon: float,
    point_lat: float,
    point_lon: float,
    radius_km: float,
) -> bool:
    """Kiểm tra một điểm có nằm trong bán kính ranh giới (Geofence radius) hay không."""
    return haversine_distance(center_lat, center_lon, point_lat, point_lon) <= radius_km


def is_point_in_polygon(
    point_lat: float,
    point_lon: float,
    polygon_coords: list[tuple[float, float]],
) -> bool:
    """
    Kiểm tra điểm toạ độ có nằm bên trong một vùng ranh giới đa giác (Geofence Polygon)
    sử dụng thuật toán Ray-Casting (Crossings Test).
    polygon_coords: danh sách các cặp (lat, lng) tạo thành các cạnh khép kín của polygon.
    """
    if len(polygon_coords) < 3:
        return False

    inside = False
    n = len(polygon_coords)

    p1_lat, p1_lon = polygon_coords[0]
    for i in range(1, n + 1):
        p2_lat, p2_lon = polygon_coords[i % n]
        if point_lon > min(p1_lon, p2_lon):
            if point_lon <= max(p1_lon, p2_lon):
                if point_lat <= max(p1_lat, p2_lat):
                    if p1_lon != p2_lon:
                        x_inters = (point_lon - p1_lon) * (p2_lat - p1_lat) / (p2_lon - p1_lon) + p1_lat
                        if p1_lat == p2_lat or point_lat <= x_inters:
                            inside = not inside
        p1_lat, p1_lon = p2_lat, p2_lon

    return inside
