"""Resolve every species label to a WoRMS AphiaID, a display name, a group and an IUCN status.

Writes table `species` and `stg_animal_species(animal_id, aphia_id)`.
Lookups are cached in data/raw/species_cache.json, so reruns are offline.
"""
from __future__ import annotations

import json
import re
import urllib.parse
from collections import Counter

import yaml

from .util import CONFIG, RAW, http_get, http_json, log

CACHE = RAW / "species_cache.json"
WORMS = "https://www.marinespecies.org/rest/AphiaRecordsByName/"
WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"
SENSITIVE_STATUSES = {"VU", "EN", "CR"}
IUCN_CODES = {"least concern": "LC", "near threatened": "NT", "vulnerable": "VU", "endangered": "EN",
              "critically endangered": "CR", "data deficient": "DD", "extinct in the wild": "EW"}
SEAL_FAMILIES = {"Phocidae", "Otariidae", "Odobenidae"}


def _group(rec: dict) -> str:
    cls, order, family = rec.get("class"), rec.get("order"), rec.get("family")
    if cls in ("Actinopteri", "Actinopterygii", "Teleostei"):
        return "fish"
    if cls == "Elasmobranchii":
        return "shark"
    if cls == "Mammalia":
        return "seal" if family in SEAL_FAMILIES else "whale"
    if cls == "Reptilia" or order == "Testudines":
        return "turtle"
    if cls == "Aves":
        return "bird"
    return "other"


def _worms(name: str) -> dict | None:
    url = WORMS + urllib.parse.quote(name) + "?like=false&marine_only=false"
    try:
        recs = json.loads(http_get(url, timeout=60) or b"[]")
    except (OSError, ValueError):
        return None
    if not recs:
        return None
    rec = next((r for r in recs if r.get("status") == "accepted"), recs[0])
    if rec.get("valid_AphiaID") and rec["valid_AphiaID"] != rec["AphiaID"]:
        valid = json.loads(http_get(f"https://www.marinespecies.org/rest/AphiaRecordByAphiaID/{rec['valid_AphiaID']}"))
        rec = valid or rec
    keep = ("AphiaID", "scientificname", "status", "kingdom", "class", "order", "family", "genus")
    return {k: rec.get(k) for k in keep}


def _iucn(names: list[str]) -> dict[str, str]:
    """IUCN status per scientific name, from Wikidata's P141 (preferred rank first)."""
    values = " ".join(json.dumps(n) for n in names)
    query = f"""
    SELECT ?name ?statusLabel ?rank WHERE {{
      VALUES ?name {{ {values} }}
      ?item wdt:P225 ?name ; p:P141 ?st .
      ?st ps:P141 ?status ; wikibase:rank ?rank .
      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
    }}"""
    res = http_json(WIKIDATA_SPARQL, {"query": query, "format": "json"}, timeout=120)
    best: dict[str, tuple[int, str]] = {}
    for b in res["results"]["bindings"]:
        name = b["name"]["value"]
        code = IUCN_CODES.get(b["statusLabel"]["value"].lower())
        if not code:
            continue
        score = 2 if b["rank"]["value"].endswith("PreferredRank") else 1
        if name not in best or score > best[name][0]:
            best[name] = (score, code)
    return {n: c for n, (_, c) in best.items()}


def _clean_label(label: str | None) -> str:
    label = re.sub(r"\s+", " ", (label or "").strip())
    return re.sub(r"^juvenile ", "", label, flags=re.IGNORECASE)


def build(con, offline: bool = False) -> None:
    cfg = yaml.safe_load((CONFIG / "species_overrides.yaml").read_text())
    by_label = {k.lower(): v for k, v in (cfg.get("label_to_scientific") or {}).items()}
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {"worms": {}, "iucn": {}}

    rows = con.execute("SELECT animal_id, species_label, scientific_name FROM stg_animal").fetchall()
    sci_of: dict[str, str] = {}
    labels: dict[str, Counter] = {}
    for animal_id, label, sci in rows:
        clean = _clean_label(label)
        name = by_label.get(clean.lower()) or (sci or "").strip() or None
        if not name:
            raise SystemExit(f"No scientific name for label {label!r} ({animal_id}); add it to species_overrides.yaml")
        sci_of[animal_id] = name
        labels.setdefault(name, Counter())[clean[:1].upper() + clean[1:].lower()] += 1

    missing = [n for n in labels if n not in cache["worms"]]
    if missing and offline:
        raise SystemExit(f"Offline but species not cached: {missing}")
    for n in missing:
        cache["worms"][n] = _worms(n)
    unresolved = [n for n in labels if not cache["worms"].get(n)]
    if unresolved:
        raise SystemExit(f"WoRMS could not resolve: {unresolved}; fix in species_overrides.yaml")

    accepted = sorted({cache["worms"][n]["scientificname"] for n in labels})
    if not offline and any(a not in cache["iucn"] for a in accepted):
        cache["iucn"].update({a: None for a in accepted})
        cache["iucn"].update(_iucn(accepted))
    CACHE.write_text(json.dumps(cache, indent=1, sort_keys=True))

    sens_cfg = yaml.safe_load((CONFIG / "sensitivity.yaml").read_text()) or {}
    sens_override = sens_cfg.get("override") or {}
    common_override = cfg.get("common_name") or {}

    species_rows: dict[int, tuple] = {}
    for name, counter in labels.items():
        rec = cache["worms"][name]
        sci = rec["scientificname"]
        status = cache["iucn"].get(sci)
        sensitive = sens_override.get(sci, status in SENSITIVE_STATUSES)
        common = common_override.get(sci) or counter.most_common(1)[0][0]
        prev = species_rows.get(rec["AphiaID"])
        if prev is None:
            species_rows[rec["AphiaID"]] = (rec["AphiaID"], sci, common, _group(rec), status, bool(sensitive),
                                            rec.get("family"))
    con.execute("""CREATE OR REPLACE TABLE species (aphia_id INTEGER, scientific_name VARCHAR, common_name VARCHAR,
                   grp VARCHAR, iucn_status VARCHAR, sensitive BOOLEAN, family VARCHAR)""")
    con.executemany("INSERT INTO species VALUES (?, ?, ?, ?, ?, ?, ?)", list(species_rows.values()))
    con.execute("CREATE OR REPLACE TABLE stg_animal_species (animal_id VARCHAR, aphia_id INTEGER)")
    con.executemany("INSERT INTO stg_animal_species VALUES (?, ?)",
                    [(a, cache["worms"][n]["AphiaID"]) for a, n in sci_of.items()])
    groups = Counter(r[3] for r in species_rows.values())
    no_status = [r[1] for r in species_rows.values() if not r[4]]
    log(f"species: {len(species_rows)} resolved; groups {dict(groups)}"
        + (f"; no IUCN status: {', '.join(no_status)}" if no_status else ""))
