"""Geolocation helpers — Haversine, geofence, IP range check."""

from __future__ import annotations

import ipaddress
import math
from typing import Optional, Tuple

EARTH_RADIUS_M = 6_371_000  # metres


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return distance in metres between two lat/lon points."""
    rlat1, rlon1, rlat2, rlon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = rlat2 - rlat1
    dlon = rlon2 - rlon1
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def check_geofence(
    student_lat: Optional[float],
    student_lon: Optional[float],
    fence_lat: Optional[float],
    fence_lon: Optional[float],
    fence_radius_m: float,
) -> Tuple[bool, Optional[float]]:
    """Return (within_fence, distance_m).

    If any coordinate is None, skip the check and return (True, None).
    """
    if any(v is None for v in (student_lat, student_lon, fence_lat, fence_lon)):
        return True, None
    dist = haversine(student_lat, student_lon, fence_lat, fence_lon)  # type: ignore[arg-type]
    return dist <= fence_radius_m, round(dist, 1)


def check_ip_range(ip_str: Optional[str], allowed_ranges: Optional[str]) -> Optional[bool]:
    """Check whether *ip_str* falls within any of the comma-separated CIDR ranges.

    Returns None if check cannot be performed (missing data), True/False otherwise.
    """
    if not ip_str or not allowed_ranges:
        return None
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return None
    for cidr in allowed_ranges.split(","):
        cidr = cidr.strip()
        if not cidr:
            continue
        try:
            network = ipaddress.ip_network(cidr, strict=False)
            if addr in network:
                return True
        except ValueError:
            continue
    return False
