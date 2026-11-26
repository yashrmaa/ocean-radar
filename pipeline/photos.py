"""Representative species photos from Wikipedia/Wikimedia Commons, with credit and license.

The lead image of each species' English Wikipedia article (looked up by scientific name), or a
Commons file named in species_overrides.yaml `photo:` (per variant: default/female/male/juvenile).
Only Creative Commons and public-domain images are accepted. Thumbnails are downloaded at build
time and served from our own site, never hot-linked.

Table `photos(aphia_id, variant, thumb, file_url, credit, license, license_url, wiki_url)`.
"""
import json
import re
import urllib.parse

import yaml

from .util import CONFIG, RAW, cached_download, http_json, log, replace_table

CACHE = RAW / "photos.json"
THUMBS = RAW / "photos"
FREE = ("cc", "public domain", "pd")
WIDTH = 640


def _strip_html(s: str | None) -> str:
    s = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s or "")).strip()
    half = len(s) // 2
    return s[:half] if len(s) % 2 == 0 and s[:half] == s[half:] else s


def _api(host: str, params: dict) -> dict:
    return http_json(f"https://{host}/w/api.php", {**params, "format": "json", "formatversion": 2})


def _lead_images(titles: list[str]) -> dict[str, tuple[str, str]]:
    """Wikipedia title -> (Commons file title, resolved article title)."""
    out = {}
    for i in range(0, len(titles), 20):
        batch = titles[i:i + 20]
        q = _api("en.wikipedia.org", {"action": "query", "titles": "|".join(batch), "redirects": 1,
                                       "prop": "pageimages", "piprop": "name"})["query"]
        hop = {x["from"]: x["to"] for x in q.get("normalized", []) + q.get("redirects", [])}
        pages = {p["title"]: p for p in q["pages"]}
        for t in batch:
            r = t
            while r in hop:
                r = hop[r]
            if pages.get(r, {}).get("pageimage"):
                out[t] = ("File:" + pages[r]["pageimage"], r)
    return out


def _file_info(files: list[str]) -> dict[str, dict]:
    out = {}
    for i in range(0, len(files), 20):
        batch = files[i:i + 20]
        q = _api("commons.wikimedia.org", {"action": "query", "titles": "|".join(batch), "prop": "imageinfo",
                                            "iiprop": "url|extmetadata", "iiurlwidth": WIDTH,
                                            "iiextmetadatafilter": "Artist|LicenseShortName|LicenseUrl"})["query"]
        hop = {x["from"]: x["to"] for x in q.get("normalized", [])}
        pages = {p["title"]: p for p in q["pages"]}
        for f in batch:
            ii = (pages.get(hop.get(f, f), {}).get("imageinfo") or [None])[0]
            if not ii:
                continue
            m = ii.get("extmetadata", {})
            lic = m.get("LicenseShortName", {}).get("value", "")
            if not lic.lower().startswith(FREE):
                continue
            out[f] = {"thumb_url": ii["thumburl"], "file_url": ii["descriptionurl"],
                      "credit": _strip_html(m.get("Artist", {}).get("value")) or "Unknown",
                      "license": lic, "license_url": m.get("LicenseUrl", {}).get("value")}
    return out


def build(con, offline: bool = False) -> None:
    overrides = (yaml.safe_load((CONFIG / "species_overrides.yaml").read_text()).get("photo") or {})
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    species = con.execute("""SELECT aphia_id, scientific_name FROM species
                             WHERE aphia_id IN (SELECT aphia_id FROM published_animals)""").fetchall()

    wanted = {}  # (aphia, variant) -> (commons file or None, wiki title)
    for aphia, sci in species:
        for variant, file in (overrides.get(sci) or {}).items():
            wanted[(aphia, variant)] = (file, sci)
        wanted.setdefault((aphia, "default"), (None, sci))
    keys = [f"{a}:{v}" for a, v in wanted]
    if not offline and any(k not in cache for k in keys):
        leads = _lead_images([sci for (a, v), (f, sci) in wanted.items() if f is None])
        files = {k: (f or leads.get(sci, (None,))[0], leads.get(sci, (None, sci))[1])
                 for k, ((f, sci)) in zip(keys, wanted.values())}
        info = _file_info(sorted({f for f, _ in files.values() if f}))
        for k, (f, article) in files.items():
            if f and f in info:
                cache[k] = {**info[f], "wiki_url": "https://en.wikipedia.org/wiki/"
                            + urllib.parse.quote(article.replace(" ", "_"))}
            else:
                cache[k] = None
        CACHE.write_text(json.dumps(cache, indent=1, ensure_ascii=False))

    rows, missing = [], []
    for (aphia, variant), (_, sci) in wanted.items():
        rec = cache.get(f"{aphia}:{variant}")
        if not rec:
            missing.append(f"{sci} ({variant})")
            continue
        path = urllib.parse.urlparse(rec["thumb_url"]).path
        ext = path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else "jpg"
        ext = ext if ext in ("jpg", "jpeg", "png", "webp", "gif") else "jpg"
        thumb = THUMBS / f"{aphia}-{variant}.{ext}"
        if not offline:
            cached_download(rec["thumb_url"], thumb)
        rows.append((aphia, variant, thumb.name, rec["file_url"], rec["credit"], rec["license"],
                     rec["license_url"], rec["wiki_url"]))
    replace_table(con, "photos", ["aphia_id", "variant", "thumb", "file_url", "credit", "license",
                                  "license_url", "wiki_url"], rows)
    log(f"photos: {len(rows)} images" + (f"; missing: {', '.join(missing)}" if missing else ""))
