"""Build tracks.json for the radar spike from two public telemetry sources.

Sources (raw downloads are cached in spike/raw/):
  - NOAA ATN ERDDAP: Stanford TOPP state-space-model tracks (daily, 2002-2017)
  - IMOS AODN GeoServer: AATAMS satellite-tag QC state-space-model tracks
    (6-hourly, 2018-present; delayed-mode preferred, near-real-time fills in)

Usage:  python3 -I build.py            # download if missing, then build
        python3 -I build.py --refresh  # re-download everything
"""
import collections
import csv
import json
import math
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "raw")

ATN = "https://atn.ioos.us/erddap/tabledap/stanford_import_ssm_public.csv"
IMOS = ("https://geoserver-123.aodn.org.au/geoserver/ows?service=WFS&version=1.0.0"
        "&request=GetFeature&outputFormat=csv&typeName=imos:aatams_sattag_qc_{}_ssm_data"
        "&propertyName=sattag_program,device_id,common_name,release_site,state_country,"
        "age_class,sex,timestamp,qc_ssm_lon,qc_ssm_lat")

DOWNLOADS = {
    "atn_points.csv": ATN + "?topp_id,common_name,time,latitude,longitude",
    "atn_animals.csv": ATN + "?common_name,taxon,topp_id&distinct()",
    "imos_dm.csv": IMOS.format("dm"),
    "imos_nrt.csv": IMOS.format("nrt"),
}

SOURCES = {
    "atn": {"name": "Stanford TOPP via U.S. Animal Telemetry Network (NOAA IOOS)",
            "url": "https://atn.ioos.us/", "license": "Free use and redistribution (U.S. Government)"},
    "imos": {"name": "IMOS Animal Tracking Facility (AATAMS) via AODN",
             "url": "https://portal.aodn.org.au/", "license": "CC BY 4.0 (verify per dataset)"},
}

# Group colours in the UI are keyed by these
ATN_GROUP = {"Fish": "fish", "Shark": "shark", "Cetacean": "whale", "Pinniped": "seal",
             "Turtle": "turtle", "Seabird": "bird"}
IMOS_GROUP = {"seal": "seal", "turtle": "turtle"}

LIFE_STAGE = {"juvenille": "juvenile", "na": None, "": None}
SEX = {"f": "female", "m": "male"}

EPOCH = datetime(2002, 1, 1, tzinfo=timezone.utc)
# Flag a track as noisy when more than NOISY_SHARE of its steps exceed FAST_KM_PER_DAY.
# Light-level geolocation (tunas, marlins) produces these jumps; it flags ~1-2% of tracks.
FAST_KM_PER_DAY = 150
NOISY_SHARE = 0.10


def fetch(refresh):
    os.makedirs(RAW, exist_ok=True)
    for name, url in DOWNLOADS.items():
        path = os.path.join(RAW, name)
        if os.path.exists(path) and not refresh:
            continue
        print("downloading", name, "...", flush=True)
        urllib.request.urlretrieve(url, path)


def days(ts):
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (dt - EPOCH).total_seconds() / 86400


def species_name(s):
    s = s.strip()
    return s[:1].upper() + s[1:].lower() if s else s


def hav(a, b):
    r = 6371
    p1, p2 = math.radians(a[1]), math.radians(b[1])
    dl = math.radians(b[0] - a[0])
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1, math.sqrt(h)))


def load_atn():
    taxon = {}
    with open(os.path.join(RAW, "atn_animals.csv")) as f:
        rows = csv.DictReader(f)
        next(rows)  # ERDDAP units row
        for r in rows:
            taxon[r["topp_id"]] = r["taxon"]
    animals = {}
    with open(os.path.join(RAW, "atn_points.csv")) as f:
        rows = csv.reader(f)
        next(rows), next(rows)
        for tid, name, t, lat, lon in rows:
            juvenile = name.lower().startswith("juvenile ")
            a = animals.setdefault("atn:" + tid, {
                "src": "atn", "native_id": tid, "sp": species_name(name[9:] if juvenile else name),
                "grp": ATN_GROUP.get(taxon.get(tid), "other"),
                "sex": None, "stage": "juvenile" if juvenile else None, "site": None, "pts": []})
            a["pts"].append((days(t), float(lon), float(lat)))
    return animals


def imos_group(name):
    n = name.lower()
    return "seal" if "seal" in n else "turtle" if "turtle" in n else "other"


def load_imos():
    animals = {}
    for kind in ("dm", "nrt"):  # dm first: delayed-mode QC wins when a device is in both
        seen_here = set()
        with open(os.path.join(RAW, f"imos_{kind}.csv")) as f:
            for r in csv.DictReader(f):
                key = "imos:" + r["device_id"]
                if kind == "nrt" and key in animals and key not in seen_here:
                    continue
                seen_here.add(key)
                a = animals.setdefault(key, {
                    "src": "imos", "native_id": r["device_id"], "sp": species_name(r["common_name"]),
                    "grp": imos_group(r["common_name"]),
                    "sex": SEX.get(r["sex"].lower()),
                    "stage": LIFE_STAGE.get(r["age_class"].lower(), r["age_class"].lower()),
                    "site": r["release_site"].strip().title().replace("D'Urville", "d'Urville") or None,
                    "pts": []})
                a["pts"].append((days(r["timestamp"]), float(r["qc_ssm_lon"]), float(r["qc_ssm_lat"])))
    return animals


def to_daily(pts):
    """Keep one position per UTC day (the first), so all sources share a cadence."""
    out, last_day = [], None
    for p in pts:
        d = int(p[0])
        if d != last_day:
            out.append(p)
            last_day = d
    return out


def build(animals):
    out, flagged = [], 0
    for key, a in animals.items():
        pts = to_daily(sorted(a["pts"]))
        if len(pts) < 2:
            continue
        steps = [(hav(pts[i][1:], pts[i + 1][1:]), pts[i + 1][0] - pts[i][0]) for i in range(len(pts) - 1)]
        km = sum(s for s, _ in steps)
        fast = sum(1 for s, dt in steps if s / max(dt, 0.01) > FAST_KM_PER_DAY)
        noisy = fast / len(steps) > NOISY_SHARE
        flagged += noisy
        path, prev = [], None
        for _, lon, lat in pts:  # unwrap longitude so paths don't jump across the antimeridian
            if prev is not None:
                while lon - prev > 180:
                    lon -= 360
                while lon - prev < -180:
                    lon += 360
            prev = lon
            path.append([round(lon, 2), round(lat, 2)])
        start = EPOCH.timestamp() + pts[0][0] * 86400
        end = EPOCH.timestamp() + pts[-1][0] * 86400
        out.append({
            "id": key, "src": a["src"], "sp": a["sp"], "grp": a["grp"],
            "sex": a["sex"], "stage": a["stage"], "site": a["site"],
            "s": datetime.fromtimestamp(start, timezone.utc).strftime("%Y-%m-%d"),
            "e": datetime.fromtimestamp(end, timezone.utc).strftime("%Y-%m-%d"),
            "km": round(km), "noisy": noisy,
            "p": path, "t": [round(p[0], 2) for p in pts],
        })
    return out, flagged


# Species photos: the lead image of each species' English Wikipedia article, with its
# Wikimedia Commons credit and license. These are representative species photos, never
# photos of the individual animal.
WIKI_TITLE = {"White shark": "Great white shark", "Galapagos shark": "Galápagos shark"}
UA = {"User-Agent": "ocean-radar-spike/0.1 (prototype)"}
FREE_LICENSE = ("cc", "public domain", "pd")


def wm_api(host, params):
    q = urllib.parse.urlencode({**params, "format": "json", "formatversion": 2})
    req = urllib.request.Request(f"https://{host}/w/api.php?{q}", headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def strip_html(s):
    s = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s or "")).strip()
    half = len(s) // 2
    return s[:half] if len(s) % 2 == 0 and s[:half] == s[half:] else s  # "XX" from nested markup


def fetch_photos(species, refresh):
    path = os.path.join(RAW, "photos.json")
    cache = {} if refresh or not os.path.exists(path) else json.load(open(path))
    todo = [s for s in species if s not in cache]
    for i in range(0, len(todo), 20):
        batch = todo[i:i + 20]
        titles = {s: WIKI_TITLE.get(s, s) for s in batch}
        q = wm_api("en.wikipedia.org", {"action": "query", "titles": "|".join(titles.values()),
                                         "redirects": 1, "prop": "pageimages", "piprop": "name"})["query"]
        hop = {x["from"]: x["to"] for x in q.get("normalized", []) + q.get("redirects", [])}
        pages = {p["title"]: p for p in q["pages"]}
        files = {}
        for s, t in titles.items():
            while t in hop:
                t = hop[t]
            p = pages.get(t, {})
            if p.get("pageimage"):
                files[s] = ("File:" + p["pageimage"], t)
            else:
                cache[s] = None
        if not files:
            continue
        info = wm_api("commons.wikimedia.org", {
            "action": "query", "titles": "|".join(f for f, _ in files.values()), "prop": "imageinfo",
            "iiprop": "url|extmetadata", "iiurlwidth": 480,
            "iiextmetadatafilter": "Artist|LicenseShortName|LicenseUrl"})["query"]
        hop = {x["from"]: x["to"] for x in info.get("normalized", [])}
        by_file = {p["title"]: p for p in info["pages"]}
        for s, (f, article) in files.items():
            ii = (by_file.get(hop.get(f, f), {}).get("imageinfo") or [None])[0]
            meta = (ii or {}).get("extmetadata", {})
            lic = meta.get("LicenseShortName", {}).get("value", "")
            if not ii or not lic.lower().startswith(FREE_LICENSE):
                cache[s] = None
                continue
            cache[s] = {"src": ii["thumburl"], "file": ii["descriptionurl"],
                        "credit": strip_html(meta.get("Artist", {}).get("value")) or "Unknown",
                        "license": lic, "license_url": meta.get("LicenseUrl", {}).get("value"),
                        "wiki": "https://en.wikipedia.org/wiki/" + urllib.parse.quote(article.replace(" ", "_"))}
    with open(path, "w") as f:
        json.dump(cache, f, indent=1, ensure_ascii=False)
    return {s: cache.get(s) for s in species}


def main():
    refresh = "--refresh" in sys.argv
    fetch(refresh)
    animals = {**load_atn(), **load_imos()}
    tracks, flagged = build(animals)
    photos = fetch_photos(sorted({t["sp"] for t in tracks}), refresh)
    missing = [s for s, p in photos.items() if not p]
    print(f"photos: {len(photos) - len(missing)}/{len(photos)}" + (f"; missing: {', '.join(missing)}" if missing else ""))
    data = {"epoch": EPOCH.strftime("%Y-%m-%d"), "sources": SOURCES, "photos": photos, "tracks": tracks}
    path = os.path.join(HERE, "tracks.json")
    with open(path, "w") as f:
        json.dump(data, f, separators=(",", ":"))
    by_src = collections.Counter(t["src"] for t in tracks)
    by_grp = collections.Counter(t["grp"] for t in tracks)
    print(f"{len(tracks)} animals {dict(by_src)}; groups {dict(by_grp)}; "
          f"{flagged} flagged noisy; {os.path.getsize(path) // 1024} KB")
    species = collections.Counter(t["sp"] for t in tracks)
    print(len(species), "species:", ", ".join(f"{s} {n}" for s, n in species.most_common()))

    with open(os.path.join(HERE, "template.html")) as f:
        html = f.read().replace("__DATA__", json.dumps(data, separators=(",", ":")))
    with open(os.path.join(HERE, "radar.html"), "w") as f:
        f.write(html)


if __name__ == "__main__":
    main()
