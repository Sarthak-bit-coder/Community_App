"""Free geocoding with OpenStreetMap Nominatim (no API key).

Nominatim's usage policy allows about 1 request per second with an identifying User-Agent,
so calls are throttled. Successful lookups are cached in memory (failures are retried later).
"""
import hashlib
import threading
import time

import requests

_lock = threading.Lock()
_last_call = [0.0]
_zip_cache, _place_cache = {}, {}
HEADERS = {"User-Agent": "NeighborhoodWatchApp/1.0"}


def _get(params):
    with _lock:
        wait = 1.1 - (time.time() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.time()
    try:
        r = requests.get("https://nominatim.openstreetmap.org/search",
                         params={**params, "format": "json", "limit": 1, "addressdetails": 1},
                         headers=HEADERS, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[geo] lookup failed for {params}: {e}", flush=True)
        return None


def zip_info(zip_code):
    """Return {'lat', 'lng', 'city', 'state'} for a US ZIP, or None."""
    if zip_code in _zip_cache:
        return _zip_cache[zip_code]
    res = _get({"postalcode": zip_code, "countrycodes": "us"})
    if res is None:          # network error: don't cache, try again next time
        return None
    info = None
    if res:
        addr = res[0].get("address", {})
        info = {"lat": float(res[0]["lat"]), "lng": float(res[0]["lon"]),
                "city": addr.get("city") or addr.get("town") or addr.get("village") or addr.get("county") or "",
                "state": addr.get("state", "")}
    _zip_cache[zip_code] = info
    return info


def _jitter(lat, lng, seed):
    """Spread pins that only know their ZIP by up to ~250 m so they don't stack on one point."""
    h = hashlib.md5(seed.encode()).digest()
    return lat + (h[0] / 255 - 0.5) * 0.0045, lng + (h[1] / 255 - 0.5) * 0.0055


def locate(place, zip_code, seed=None):
    """Coordinates for a street/landmark inside a ZIP; falls back to (a spot near) the ZIP center."""
    info = zip_info(zip_code) if zip_code else None
    if place:
        key = (place.lower(), zip_code)
        if key not in _place_cache:
            q = f"{place}, {info['city']}, {info['state']}" if info and info["city"] else f"{place} {zip_code}"
            res = _get({"q": q, "countrycodes": "us"})
            if res is not None:
                _place_cache[key] = (float(res[0]["lat"]), float(res[0]["lon"])) if res else None
        if _place_cache.get(key):
            return _place_cache[key]
    if info:
        return _jitter(info["lat"], info["lng"], seed) if seed else (info["lat"], info["lng"])
    return None, None
