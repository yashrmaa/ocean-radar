"""Shared paths, HTTP helpers and small geo utilities."""
from __future__ import annotations

import json
import math
import os
import time
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = Path(__file__).resolve().parent / "config"
DATA = ROOT / "data"
RAW = DATA / "raw"
WAREHOUSE = DATA / "warehouse.duckdb"
WEB_DATA = ROOT / "web" / "public" / "data"
STORIES = ROOT / "stories"

# Generic client name: no personal details in requests to outside services.
USER_AGENT = "ocean-radar/0.1 (non-commercial prototype)"

# All published times are expressed as days since this epoch.
EPOCH = datetime(2002, 1, 1, tzinfo=UTC)


def log(*args) -> None:
    print("·", *args, flush=True)


def http_get(url: str, timeout: int = 600, retries: int = 5) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(15 * (attempt + 1))  # servers here sometimes return 503 under load
    raise RuntimeError("unreachable")


def cached_download(url: str, dest: Path, refresh: bool = False) -> Path:
    if dest.exists() and dest.stat().st_size > 0 and not refresh:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(http_get(url))
    os.replace(tmp, dest)
    return dest


def http_json(base: str, params: dict | None = None, timeout: int = 60):
    url = base + ("?" + urllib.parse.urlencode(params) if params else "")
    return json.loads(http_get(url, timeout=timeout))


def replace_table(con, name: str, columns: list[str], rows: list[tuple]) -> None:
    """Create or replace a DuckDB table from Python rows (fast, via Arrow)."""
    import pyarrow as pa

    cols = list(zip(*rows)) if rows else [[] for _ in columns]
    tbl = pa.table({c: list(v) for c, v in zip(columns, cols)})
    con.register("_rows", tbl)
    con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM _rows")
    con.unregister("_rows")


def haversine_km(lon1, lat1, lon2, lat2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(min(1.0, math.sqrt(h)))


def to_days(dt: datetime) -> float:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return (dt - EPOCH).total_seconds() / 86400
