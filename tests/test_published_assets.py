"""Checks on what is actually published in web/public/data (run after a build).

The embargo test reads the emitted files, not the warehouse, so it catches any code path that
leaks a position newer than the embargo allows.
"""
import json
from array import array
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

DATA = Path(__file__).resolve().parent.parent / "web" / "public" / "data"
pytestmark = pytest.mark.skipif(not (DATA / "index.json").exists(), reason="run the pipeline first")


@pytest.fixture(scope="module")
def index():
    return json.loads((DATA / "index.json").read_text())


def _cuts(index):
    epoch = datetime.fromisoformat(index["epoch"]).replace(tzinfo=UTC)
    now = datetime.fromisoformat(index["generated"])
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    to_day = lambda d: (d - epoch).total_seconds() / 86400
    return (to_day(now - timedelta(days=index["embargo_days"]["default"])),
            to_day(now - timedelta(days=index["embargo_days"]["sensitive"])))


def _limit(index, animal, cuts):
    return cuts[1] if index["species"][str(animal["sp"])]["sensitive"] else cuts[0]


def test_no_chunk_position_inside_embargo(index):
    cuts = _cuts(index)
    for year in index["years"]:
        header = json.loads((DATA / "chunks" / f"{year}.json").read_text())
        cols = array("f")
        with open(DATA / "chunks" / f"{year}.bin", "rb") as f:
            cols.fromfile(f, 3 * header["n"])
        days = cols[2 * header["n"]:]
        for i, start, length in header["animals"]:
            limit = _limit(index, index["animals"][i], cuts)
            newest = max(days[start:start + length])
            assert newest <= limit + 1e-3, f"{index['animals'][i]['id']} has a position inside the embargo"


def test_no_shard_position_inside_embargo(index):
    cuts = _cuts(index)
    by_id = {a["id"]: a for a in index["animals"]}
    for shard in sorted((DATA / "animals").glob("*.json")):
        for animal_id, detail in json.loads(shard.read_text()).items():
            limit = _limit(index, by_id[animal_id], cuts)
            assert max(p[0] for p in detail["track"]) <= limit + 1e-3, animal_id
            assert by_id[animal_id]["e"] <= limit + 1e-2, animal_id


def test_every_animal_has_source_and_license(index):
    for a in index["animals"]:
        src = index["sources"][a["src"]]
        assert src["name"] and src["license"], a["id"]


def test_every_photo_is_credited_and_freely_licensed(index):
    for variants in index["photos"].values():
        for p in variants.values():
            assert p["credit"] and p["license"]
            assert p["license"].lower().startswith(("cc", "public domain", "pd"))
            assert (DATA / p["src"]).exists()


def test_shard_files_stay_small():
    for f in DATA.rglob("*"):
        if f.is_file():
            assert f.stat().st_size < 5_000_000, f"{f} is too large for static hosting chunks"
