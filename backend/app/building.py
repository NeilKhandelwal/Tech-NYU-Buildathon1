"""Building history from NYC Open Data (CONTRACT.md `buildingLookup`): open HPD violations
and last season's 311 heat complaints at the resident's address.

Socrata needs no key but can be slow, so: a seed file for the demo building (the demo never
waits on the network), an in-process cache, short timeouts, and a failed lookup returns
`found: False` instead of raising.
"""
import asyncio
import json
import logging
import re
import time
from datetime import date
from pathlib import Path

import httpx

logger = logging.getLogger("backend")

VIOLATIONS = "https://data.cityofnewyork.us/resource/wvxf-dwi5.json"  # HPD violations
COMPLAINTS = "https://data.cityofnewyork.us/resource/erm2-nwe9.json"  # 311 service requests
TIMEOUT_S = 8
CACHE_TTL_S = 24 * 3600
SEED = Path(__file__).with_name("seed_buildings.json")
BOROUGHS = {"BRONX", "BROOKLYN", "MANHATTAN", "QUEENS", "STATEN ISLAND"}
# Open Data spells suffixes out ("CRESTON AVENUE"); residents and agents abbreviate
SUFFIXES = {"AVE": "AVENUE", "AV": "AVENUE", "ST": "STREET", "RD": "ROAD", "BLVD": "BOULEVARD",
            "PL": "PLACE", "PKWY": "PARKWAY", "DR": "DRIVE", "LN": "LANE", "CT": "COURT",
            "TER": "TERRACE", "E": "EAST", "W": "WEST", "N": "NORTH", "S": "SOUTH"}

_cache: dict[str, tuple[float, dict]] = {}


def parse(address: str, borough: str | None = None) -> tuple[str, str, str | None] | None:
    """'2800 Creston Ave, Bronx' -> ('2800', 'CRESTON AVENUE', 'BRONX'); None if no number."""
    parts = [p.strip().upper() for p in address.split(",")]
    m = re.match(r"^(\d+(?:-\d+)?)\s+(.+)$", parts[0])
    if not m:
        return None
    words = [SUFFIXES.get(w.strip("."), w.strip(".")) for w in m.group(2).split()]
    boro = (borough or "").strip().upper() or next((p for p in parts[1:] if p in BOROUGHS), None)
    return m.group(1), " ".join(words), boro


def key(address: str, borough: str | None = None) -> str:
    p = parse(address, borough)
    return " ".join(x for x in p if x) if p else address.strip().upper()


def last_season(today: date) -> tuple[str, str]:
    """The last complete heat season (Oct 1 – May 31) before `today`, as ISO dates [start, end)."""
    end_year = today.year if today.month >= 6 else today.year - 1
    return f"{end_year - 1}-10-01", f"{end_year}-06-01"


def esc(s: str) -> str:
    return s.replace("'", "''")  # SoQL string literal


def source_urls(house: str, street: str) -> list[str]:
    q = f"?$where=housenumber='{house}' AND streetname='{street}'"
    return [VIOLATIONS + q.replace(" ", "%20"),
            "https://data.cityofnewyork.us/Housing-Development/Housing-Maintenance-Code-Violations/wvxf-dwi5"]


def not_found(address: str) -> dict:
    return {"address": address, "found": False, "open_violations": 0, "open_class_c": 0,
            "heat_violations": 0, "heat_complaints_last_season": 0, "repeat_offender": False,
            "recent": [], "source_urls": [], "source": "none"}


def summarize(address: str, house: str, street: str, rows: list[dict],
              complaints: int) -> dict:
    open_rows = [r for r in rows if r.get("violationstatus") == "Open"]
    open_c = sum(1 for r in open_rows if r.get("class") == "C")
    heat = sum(1 for r in rows if re.search(r"27-20(29|31)", r.get("novdescription") or ""))
    return {
        "address": address, "found": bool(rows) or complaints > 0,
        "open_violations": len(open_rows), "open_class_c": open_c, "heat_violations": heat,
        "heat_complaints_last_season": complaints,
        # either signal alone marks a building the city has heard about before
        "repeat_offender": open_c >= 3 or heat >= 2 or complaints >= 10,
        "recent": [{"date": (r.get("inspectiondate") or "")[:10], "class": r.get("class"),
                    "status": r.get("violationstatus"),
                    "description": (r.get("novdescription") or "")[:200]} for r in rows[:5]],
        "source_urls": source_urls(house, street), "source": "nyc_open_data",
    }


async def fetch(house: str, street: str, boro: str | None) -> tuple[list[dict], int]:
    where = f"housenumber='{esc(house)}' AND streetname='{esc(street)}'"
    if boro:
        where += f" AND boro='{esc(boro)}'"
    start, end = last_season(date.today())
    cwhere = (f"incident_address='{esc(house)} {esc(street)}' AND complaint_type='HEAT/HOT WATER'"
              f" AND created_date>='{start}' AND created_date<'{end}'")
    if boro:
        cwhere += f" AND borough='{esc(boro)}'"
    async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
        v, c = await asyncio.gather(
            client.get(VIOLATIONS, params={"$where": where, "$order": "inspectiondate DESC",
                                           "$limit": 1000}),
            client.get(COMPLAINTS, params={"$where": cwhere, "$select": "count(*)"}))
    v.raise_for_status()
    c.raise_for_status()
    return v.json(), int(c.json()[0]["count"])


def seeded(k: str) -> dict | None:
    try:
        return json.loads(SEED.read_text()).get(k)
    except (OSError, ValueError):
        return None


async def building_lookup(address: str, borough: str | None = None) -> dict:
    """Never raises. Order: seed (demo building) -> cache -> Open Data -> not found."""
    k = key(address, borough)
    if (seed := seeded(k)) is not None:
        return {**seed, "address": address, "source": "seed"}
    if k in _cache and time.time() - _cache[k][0] < CACHE_TTL_S:
        return {**_cache[k][1], "source": "cache"}
    p = parse(address, borough)
    if p is None:
        return not_found(address)
    house, street, boro = p
    try:
        rows, complaints = await fetch(house, street, boro)
    except Exception as e:
        logger.warning("building lookup for %s failed: %s", k, e)
        return not_found(address)
    out = summarize(address, house, street, rows, complaints)
    _cache[k] = (time.time(), out)
    return out
