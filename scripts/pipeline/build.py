# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "h3>=4.1",
#   "shapely>=2.0",
#   "geopandas>=1.0",
#   "pyogrio>=0.9",
#   "pandas>=2.2",
#   "numpy>=1.26",
#   "scikit-learn>=1.4",
#   "rasterio>=1.3",
# ]
# ///
"""Geocircle data pipeline.

Reads raw downloads from data-raw/ and writes everything the game loads into public/data/.
Run from the project root:  uv run scripts/pipeline/build.py [step ...]
Steps: land, geo, population, stats, points, livestock, landcells, bycountry, apad, puzzles, publish, schedule, countries, candidates, selftest
(default: all, in that order). publish only re-merges puzzles/curated.json into puzzles.json;
schedule fills public/data/schedule.json up to 60 days ahead.

Formats (see lib/cells.ts and lib/land.ts for the readers):
  fine-cell file  <name>.bin           16-byte records: u32 lo, u32 hi, f32 value, u16 country, u16 0
                  <name>.index.json    {"res": r, "groups": {r3: [offset, count, total, maxR_km]}}
                  <name>.bycountry.json {country_index: {r3: total}} for puzzles limited to some countries
  land bitmaps    land-r{5,6,7}.bin    one bit per child (sorted ids) of each parent cell
                  land-r{5,6,7}.json   {"res", "parentRes", "groups": {parent: [byteOffset, nBytes]}, "full": [parent...]}
"""
from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import json
import math
import re
import os
import shutil
import sys
import zipfile
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import geopandas as gpd
import h3.api.basic_int as h3
import numpy as np
import pandas as pd
import shapely
import warnings

warnings.filterwarnings("ignore", message="Geometry is in a geographic CRS")
from shapely.geometry import Point, Polygon, box

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data-raw"
OUT = ROOT / "public" / "data"
CACHE = ROOT / "scripts" / "pipeline" / ".cache"
EARTH_R = 6371.0088
REC = np.dtype([("lo", "<u4"), ("hi", "<u4"), ("value", "<f4"), ("country", "<u2"), ("pad", "<u2")])


def log(*a):
    print("·", *a, flush=True)


def hav_km(lat1, lng1, lat2, lng2):
    lat1, lng1, lat2, lng2 = map(np.radians, (lat1, lng1, lat2, lng2))
    h = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lng2 - lng1) / 2) ** 2
    return 2 * EARTH_R * np.arcsin(np.minimum(1, np.sqrt(h)))


def write_json(path: Path, obj, round_floats: int | None = None):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, separators=(",", ":"))
    log(f"wrote {path.relative_to(ROOT)} ({path.stat().st_size / 1e6:.2f} MB)")


def all_cells(res: int) -> list[int]:
    out = []
    for c in h3.get_res0_cells():
        out.extend(h3.cell_to_children(c, res))
    return out


def cell_poly(c: int) -> Polygon:
    return Polygon([(lng, lat) for lat, lng in h3.cell_to_boundary(c)])


def crosses_dateline_or_pole(c: int) -> bool:
    b = h3.cell_to_boundary(c)
    lngs = [p[1] for p in b]
    lat = h3.cell_to_latlng(c)[0]
    return (max(lngs) - min(lngs) > 180) or abs(lat) > 80 or min(abs(abs(l) - 180) for l in lngs) < 3


# ---------------------------------------------------------------- land

def load_land_pieces() -> list:
    """Natural Earth land + minor islands, split into pieces of at most ~256 vertices for fast tests."""
    land = gpd.read_file(f"zip://{RAW / 'ne' / 'ne_10m_land.zip'}")
    isl = gpd.read_file(f"zip://{RAW / 'ne' / 'ne_10m_minor_islands.zip'}")
    geoms = shapely.make_valid(np.concatenate([land.geometry.values, isl.geometry.values]))
    polys = []
    for g in geoms:
        polys.extend(shapely.get_parts(g))
    polys = [p for p in polys if p.geom_type == "Polygon" and not p.is_empty]
    pieces = []

    def split(g):
        if g.is_empty:
            return
        if shapely.get_num_coordinates(g) <= 256:
            pieces.append(g)
            return
        x0, y0, x1, y1 = g.bounds
        if x1 - x0 >= y1 - y0:
            m = (x0 + x1) / 2
            halves = [box(x0, y0, m, y1), box(m, y0, x1, y1)]
        else:
            m = (y0 + y1) / 2
            halves = [box(x0, y0, x1, m), box(x0, m, x1, y1)]
        for h in halves:
            part = shapely.clip_by_rect(g, *h.bounds)
            for p in shapely.get_parts(part):
                if p.geom_type == "Polygon" and p.area > 0:
                    split(p)

    for p in polys:
        split(p)
    log(f"land: {len(polys)} polygons -> {len(pieces)} pieces")
    return pieces


_TREE = None
_PIECES = None


def _init_worker(pieces):
    global _TREE, _PIECES
    _PIECES = pieces
    _TREE = shapely.STRtree(pieces)


def _land_children(args):
    """Return (r3, land r7 children as sorted list, is_full) for one r3 cell."""
    r3, cap_wkb = args
    cap = shapely.from_wkb(cap_wkb)
    cand = _TREE.query(cap, predicate="intersects")
    kids = sorted(h3.cell_to_children(r3, 7))
    if len(cand) == 0:
        return r3, [], False
    if any(_PIECES[i].contains(cap) for i in cand):
        return r3, kids, True
    if crosses_dateline_or_pole(r3):
        pts = [Point(lng, lat) for lat, lng in (h3.cell_to_latlng(k) for k in kids)]
        hit = np.unique(_TREE.query(pts, predicate="intersects")[0])
    else:
        polys = [cell_poly(k) for k in kids]
        hit = np.unique(_TREE.query(polys, predicate="intersects")[0])
    land = [kids[i] for i in hit]
    return r3, land, len(land) == len(kids)


def cap_polygon(c: int, factor: float) -> Polygon:
    """Polygon around a cell that safely holds all its descendants: the hex scaled up from its centre."""
    lat, lng = h3.cell_to_latlng(c)
    pts = []
    for blat, blng in h3.cell_to_boundary(c):
        dl = blng - lng
        if dl > 180:
            dl -= 360
        if dl < -180:
            dl += 360
        pts.append((lng + dl * factor, lat + (blat - lat) * factor))
    return Polygon(pts)


def descendant_factor() -> float:
    """How far r7 descendants of an r3 cell reach past the r3 hex, as a scale factor (measured, plus margin)."""
    worst = 1.0
    for c in all_cells(3)[::997][:40]:
        lat, lng = h3.cell_to_latlng(c)
        circ = max(hav_km(lat, lng, b[0], b[1]) for b in h3.cell_to_boundary(c))
        far = 0
        for k in h3.cell_to_children(c, 7):
            for b in h3.cell_to_boundary(k):
                far = max(far, hav_km(lat, lng, b[0], b[1]))
        worst = max(worst, far / circ)
    return worst * 1.15


def pack_bitmaps(parent_res: int, res: int, land_by_parent: dict[int, set[int]], name: str):
    groups, full, chunks, off = {}, [], [], 0
    for p in sorted(land_by_parent):
        land = land_by_parent[p]
        kids = sorted(h3.cell_to_children(p, res))
        if len(land) == len(kids):
            full.append(h3.int_to_str(p))
            continue
        bits = np.zeros(len(kids), dtype=bool)
        idx = {k: i for i, k in enumerate(kids)}
        for k in land:
            bits[idx[k]] = True
        b = np.packbits(bits, bitorder="little").tobytes()
        groups[h3.int_to_str(p)] = [off, len(b)]
        chunks.append(b)
        off += len(b)
    (OUT / f"{name}.bin").write_bytes(b"".join(chunks))
    write_json(OUT / f"{name}.json", {"res": res, "parentRes": parent_res, "groups": groups, "full": full})
    log(f"{name}: {len(groups)} partial + {len(full)} full parents, {off / 1e6:.2f} MB bitmap")


def step_land():
    pieces = load_land_pieces()
    factor = descendant_factor()
    log(f"land: descendant cap factor {factor:.3f}")
    r3s = all_cells(3)
    tasks = [(c, shapely.to_wkb(cap_polygon(c, factor))) for c in r3s]
    r7_by_r3: dict[int, list[int]] = {}
    with ProcessPoolExecutor(max_workers=os.cpu_count(), initializer=_init_worker, initargs=(pieces,)) as ex:
        for i, (r3, land, _full) in enumerate(ex.map(_land_children, tasks, chunksize=64)):
            if land:
                r7_by_r3[r3] = land
            if i % 5000 == 0:
                log(f"land: {i}/{len(tasks)} r3 cells")
    r7_count = sum(len(v) for v in r7_by_r3.values())
    log(f"land: {r7_count:,} r7 land cells under {len(r7_by_r3):,} r3 parents")
    assert 20e6 < r7_count < 45e6, r7_count

    # r7 by r3 parent; r6 by r2; r5 by r1 ("any child is land" upward).
    pack_bitmaps(3, 7, {k: set(v) for k, v in r7_by_r3.items()}, "land-r7")
    r6_by_r2: dict[int, set[int]] = defaultdict(set)
    r5_by_r1: dict[int, set[int]] = defaultdict(set)
    count7 = {4: defaultdict(int), 3: defaultdict(int), 2: defaultdict(int)}
    for r3, kids in r7_by_r3.items():
        for k in kids:
            r6 = h3.cell_to_parent(k, 6)
            r6_by_r2[h3.cell_to_parent(r6, 2)].add(r6)
            r5 = h3.cell_to_parent(k, 5)
            r5_by_r1[h3.cell_to_parent(r5, 1)].add(r5)
            count7[4][h3.cell_to_parent(k, 4)] += 1
        count7[3][r3] += len(kids)
        count7[2][h3.cell_to_parent(r3, 2)] += len(kids)
    pack_bitmaps(2, 6, r6_by_r2, "land-r6")
    pack_bitmaps(1, 5, r5_by_r1, "land-r5")

    # Zoomed-out layers: a hex is land when at least 25% of its r7 descendants are.
    for res in (2, 3, 4):
        n_desc = 7 ** (7 - res)
        cells = sorted(h3.int_to_str(c) for c, n in count7[res].items() if n >= 0.25 * n_desc)
        write_json(OUT / f"land-r{res}.json", cells)
        log(f"land-r{res}: {len(cells):,} cells")

    # Self-check: decode a few bitmaps back.
    idx = json.loads((OUT / "land-r7.json").read_text())
    blob = (OUT / "land-r7.bin").read_bytes()
    for p_str in list(idx["groups"])[::500][:20]:
        o, n = idx["groups"][p_str]
        kids = sorted(h3.cell_to_children(h3.str_to_int(p_str), 7))
        bits = np.unpackbits(np.frombuffer(blob[o : o + n], dtype=np.uint8), bitorder="little")[: len(kids)]
        assert {kids[i] for i in np.flatnonzero(bits)} == set(r7_by_r3[h3.str_to_int(p_str)])
    log("land: bitmap round-trip ok")


# ---------------------------------------------------------------- geo layers (coast, borders, cities)

def round_geom(g, nd=3):
    return shapely.set_precision(g, 10 ** -nd)


def step_geo():
    coast = gpd.read_file(f"zip://{RAW / 'ne' / 'ne_10m_coastline.zip'}")
    coast["geometry"] = coast.geometry.simplify(0.004)
    coast[["geometry"]].to_file(CACHE / "coast.geojson", driver="GeoJSON", COORDINATE_PRECISION=3)
    shutil.copy(CACHE / "coast.geojson", OUT / "coast-10m.json")
    log(f"coast: {(OUT / 'coast-10m.json').stat().st_size / 1e6:.2f} MB")

    b = gpd.read_file(f"zip://{RAW / 'ne' / 'ne_10m_admin_0_boundary_lines_land.zip'}")
    b["geometry"] = b.geometry.simplify(0.004)
    b[["geometry"]].to_file(CACHE / "borders.geojson", driver="GeoJSON", COORDINATE_PRECISION=3)
    shutil.copy(CACHE / "borders.geojson", OUT / "borders-10m.json")
    log(f"borders: {(OUT / 'borders-10m.json').stat().st_size / 1e6:.2f} MB")

    pl = gpd.read_file(f"zip://{RAW / 'ne' / 'ne_10m_populated_places_simple.zip'}")
    feats = []
    for _, r in pl.sort_values("pop_max", ascending=False).iterrows():
        feats.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [round(r.geometry.x, 3), round(r.geometry.y, 3)]},
                "properties": {"n": r["name"], "p": int(r["pop_max"]), "r": int(r["scalerank"])},
            }
        )
    write_json(OUT / "places.json", {"type": "FeatureCollection", "features": feats})


# ---------------------------------------------------------------- countries

def load_countries() -> gpd.GeoDataFrame:
    c = gpd.read_file(f"zip://{RAW / 'ne' / 'ne_10m_admin_0_countries.zip'}")
    c = c[["ADMIN", "ISO_A3_EH", "ISO_N3_EH", "CONTINENT", "SUBREGION", "geometry"]].reset_index(drop=True)
    c.index = c.index + 1  # country index 0 means "unknown"
    return c


def assign_country(lats: np.ndarray, lngs: np.ndarray, countries: gpd.GeoDataFrame, max_deg: float | None = None) -> np.ndarray:
    """Country index per point: the country it is in, else the nearest one (within max_deg if given, else 0)."""
    pts = gpd.GeoDataFrame(geometry=gpd.points_from_xy(lngs, lats), crs=countries.crs)
    j = gpd.sjoin(pts, countries[["geometry"]], how="left", predicate="within")
    j = j[~j.index.duplicated()]
    out = j["index_right"].to_numpy(dtype=float, copy=True)
    miss = np.isnan(out)
    if miss.any():
        near = gpd.sjoin_nearest(pts[miss], countries[["geometry"]], how="left", max_distance=max_deg)
        near = near[~near.index.duplicated()]
        out[miss] = near["index_right"].to_numpy()
    return np.nan_to_num(out, nan=0).astype(np.uint16)


# ---------------------------------------------------------------- fine-cell files

def write_cells(name: str, cells: np.ndarray, values: np.ndarray, country: np.ndarray, res: int):
    """cells: uint64 H3 ints at `res`. Groups by r3 parent, writes .bin + .index.json."""
    order = np.lexsort((cells, np.array([h3.cell_to_parent(int(c), 3) for c in cells], dtype=np.uint64)))
    cells, values, country = cells[order], values[order], country[order]
    parents = np.array([h3.cell_to_parent(int(c), 3) for c in cells], dtype=np.uint64)
    rec = np.zeros(len(cells), dtype=REC)
    rec["lo"] = (cells & 0xFFFFFFFF).astype(np.uint32)
    rec["hi"] = (cells >> np.uint64(32)).astype(np.uint32)
    rec["value"] = values.astype(np.float32)
    rec["country"] = country
    path = OUT / f"{name}.bin"
    path.parent.mkdir(parents=True, exist_ok=True)
    rec.tofile(path)
    latlng = np.array([h3.cell_to_latlng(int(c)) for c in cells])
    groups = {}
    starts = np.flatnonzero(np.r_[True, parents[1:] != parents[:-1]])
    ends = np.r_[starts[1:], len(cells)]
    for s, e in zip(starts, ends):
        p = int(parents[s])
        plat, plng = h3.cell_to_latlng(p)
        maxr = float(hav_km(plat, plng, latlng[s:e, 0], latlng[s:e, 1]).max())
        groups[h3.int_to_str(p)] = [int(s), int(e - s), float(rec["value"][s:e].astype(np.float64).sum()), round(maxr, 3)]
    write_json(OUT / f"{name}.index.json", {"res": res, "groups": groups})
    log(f"{name}: {len(cells):,} cells in {len(groups):,} r3 groups, {path.stat().st_size / 1e6:.2f} MB")
    total = sum(g[2] for g in groups.values())
    assert abs(total - rec["value"].astype(np.float64).sum()) < 1e-6 * max(1, total), "r3 totals don't add up"
    write_bycountry(name)


def write_bycountry(name: str):
    """Per-country r3 totals, so a puzzle can be limited to one or more countries."""
    idx, cells, value, cidx = read_cells(name)
    parents = np.array([h3.cell_to_parent(int(c), 3) for c in cells], dtype=np.uint64)
    g = pd.DataFrame({"c": cidx, "p": parents, "v": value}).groupby(["c", "p"])["v"].sum()
    out: dict[str, dict[str, float]] = defaultdict(dict)
    for (c, par), v in g.items():
        if v > 0:
            out[str(int(c))][h3.int_to_str(int(par))] = round(float(v), 2)
    write_json(OUT / f"{name}.bycountry.json", out)


def read_cells(name: str):
    idx = json.loads((OUT / f"{name}.index.json").read_text())
    rec = np.fromfile(OUT / f"{name}.bin", dtype=REC)
    cells = rec["lo"].astype(np.uint64) | (rec["hi"].astype(np.uint64) << np.uint64(32))
    return idx, cells, rec["value"].astype(np.float64), rec["country"].astype(np.int64)


# ---------------------------------------------------------------- population

def step_population():
    gz = RAW / "kontur" / "kontur_r6.gpkg.gz"
    gpkg = CACHE / "kontur_r6.gpkg"
    if not gpkg.exists():
        log("population: unzipping Kontur")
        with gzip.open(gz, "rb") as fi, open(gpkg, "wb") as fo:
            shutil.copyfileobj(fi, fo, length=1 << 24)
    import pyogrio

    df = pyogrio.read_dataframe(gpkg, columns=["h3", "population"], read_geometry=False)
    log(f"population: {len(df):,} Kontur r6 cells, total {df.population.sum() / 1e9:.2f} B")
    r5 = np.array([h3.cell_to_parent(h3.str_to_int(s), 5) for s in df["h3"]], dtype=np.uint64)
    agg = pd.Series(df["population"].to_numpy(dtype=np.float64)).groupby(r5).sum()
    cells = agg.index.to_numpy(dtype=np.uint64)
    pop = agg.to_numpy()
    total = pop.sum()
    assert 7.5e9 < total < 8.5e9, total
    latlng = np.array([h3.cell_to_latlng(int(c)) for c in cells])
    countries = load_countries()
    cidx = assign_country(latlng[:, 0], latlng[:, 1], countries)
    write_cells("population", cells, pop, cidx, 5)
    write_json(
        OUT / "countries.json",
        [{"i": int(i), "name": r.ADMIN, "iso3": r.ISO_A3_EH, "m49": r.ISO_N3_EH, "continent": r.CONTINENT} for i, r in countries.iterrows()],
    )


# ---------------------------------------------------------------- food stats

FOOD = {
    "rice": "Rice and products",
    "beer": "Beer",
    "coffee": "Coffee and products",
    "chocolate": "Cocoa Beans and products",
    "wine": "Wine",
    "potatoes": "Potatoes and products",
    "fish": "Fish, Seafood",
    "tea": "Tea (including mate)",
    "bananas": "Bananas",
    "eggs": "Eggs",
    "yams": "Yams",
    "cassava": "Cassava and products",
    "plantains": "Plantains",
    "dates": "Dates",
    "rye": "Rye and products",
    "pork": "Pigmeat",
    "millet": "Millet and products",
    "sorghum": "Sorghum and products",
    "mutton": "Mutton & Goat Meat",
    "sweet-potatoes": "Sweet potatoes",
    "olive-oil": "Olive Oil",
    "apples": "Apples and products",
}


def step_stats():
    countries = load_countries()
    m49_to_idx: dict[int, list[int]] = defaultdict(list)  # several NE rows share a code (France + Clipperton)
    for i, r in countries.iterrows():
        try:
            n = int(r.ISO_N3_EH)
        except (TypeError, ValueError):
            continue
        if n > 0:
            m49_to_idx[n].append(i)
    with zipfile.ZipFile(RAW / "fao" / "fbs.zip") as z:
        name = next(n for n in z.namelist() if n.endswith(".csv") and "All_Data" in n and "Flags" not in n)
        fbs = pd.read_csv(z.open(name), encoding="latin-1", low_memory=False)
    fbs = fbs[(fbs["Element Code"] == 645)]
    fbs["m49"] = pd.to_numeric(fbs["Area Code (M49)"].astype(str).str.lstrip("'"), errors="coerce")
    fbs = fbs[fbs["Area Code"] != 351]  # "China" aggregate; mainland (41), Taiwan, HK, Macao are listed separately
    # Countries FAO reports at all (any item). A listed country without an item eats ~none of it.
    in_fao = set()
    for m in fbs.loc[fbs["Year"] >= 2018, "m49"].dropna().unique():
        in_fao.update(m49_to_idx.get(int(m), []))
    idx, cells, pop, cidx = read_cells("population")
    parents = np.array([h3.cell_to_parent(int(c), 3) for c in cells], dtype=np.uint64)
    pop_by_country = pd.Series(pop).groupby(cidx).sum()
    continent = countries["CONTINENT"].to_dict()
    for key, item in FOOD.items():
        rows = fbs[fbs["Item"] == item]
        assert len(rows), f"FAOSTAT item not found: {item}"
        # Latest year per country (FAO publishes some countries a year or two later), not older than 2018.
        rows = rows[rows["Year"] >= 2018].dropna(subset=["Value"]).sort_values("Year").groupby("m49").tail(1)
        year = int(rows["Year"].max())
        kg = np.zeros(len(countries) + 1)
        for _, r in rows.iterrows():
            for i in m49_to_idx.get(int(r.m49), []) if not math.isnan(r.m49) else []:
                kg[i] = float(r.Value)
        # Countries FAO doesn't cover at all (Japan, Sudan...) get their subregion's population-weighted average.
        sub = countries["SUBREGION"]
        reported = [i for i in pop_by_country.index if i in in_fao]
        imputed = []
        for i in pop_by_country.index:
            if i == 0 or i in in_fao:
                continue
            peers = [j for j in reported if sub[j] == sub[i] and kg[j] > 0]
            if peers and pop_by_country[peers].sum() > 0:
                kg[i] = float((kg[peers] * pop_by_country[peers]).sum() / pop_by_country[peers].sum())
                if pop_by_country[i] > 5e6:
                    imputed.append(countries.loc[i, "ADMIN"])
        if imputed:
            log(f"stat {key}: imputed from subregion for {', '.join(imputed)}")
        w = pop * kg[cidx]
        r3 = pd.Series(w).groupby(parents).sum()
        covered = pop_by_country[[i for i in reported]].sum() / pop.sum()
        by_cont = pd.Series(w).groupby([continent.get(int(i), "?") for i in cidx]).sum() / w.sum()
        by_country = pd.Series(w).groupby(cidx).sum().sort_values(ascending=False) / w.sum()
        log(f"stat {key}: {year}, population covered {covered:.1%}, top continents {by_cont.sort_values(ascending=False).head(3).round(3).to_dict()}")
        assert covered > 0.9, f"{key}: only {covered:.0%} of people are in FAO's data"
        if key == "rice":
            assert by_cont.get("Asia", 0) > 0.8, by_cont
        top = [{"name": countries.loc[int(i), "ADMIN"], "share": round(float(s), 4)} for i, s in by_country.head(5).items() if i > 0]
        write_json(
            OUT / "stats" / f"{key}.json",
            {
                "item": item,
                "year": year,
                "kg": [round(float(x), 3) for x in kg],
                "r3": {h3.int_to_str(int(k)): round(float(v), 1) for k, v in r3.items() if v > 0},
                "top": top,
            },
        )


# ---------------------------------------------------------------- point datasets

def points_to_cells(name: str, lats: np.ndarray, lngs: np.ndarray, max_deg: float | None = None, weights: np.ndarray | None = None, res: int = 7):
    r7 = np.array([h3.latlng_to_cell(float(a), float(b), res) for a, b in zip(lats, lngs)], dtype=np.uint64)
    counts = pd.Series(1.0 if weights is None else np.asarray(weights, dtype=float), index=r7).groupby(level=0).sum()
    cells = counts.index.to_numpy(dtype=np.uint64)
    latlng = np.array([h3.cell_to_latlng(int(c)) for c in cells])
    countries = load_countries()
    cidx = assign_country(latlng[:, 0], latlng[:, 1], countries, max_deg)
    write_cells(f"points/{name}", cells, counts.to_numpy(), cidx, res)
    by_country = pd.Series(counts.to_numpy()).groupby(cidx).sum().sort_values(ascending=False) / counts.sum()
    top = [{"name": countries.loc[int(i), "ADMIN"], "share": round(float(s), 4)} for i, s in by_country.head(5).items() if i > 0]
    write_json(OUT / "points" / f"{name}.top.json", top)


def step_points():
    frames = [pd.read_csv(p) for p in sorted((RAW / "usgs").glob("*.csv"))]
    eq = pd.concat(frames)
    log(f"earthquakes: {len(eq):,} events M4.5+")
    assert len(eq) > 100_000, len(eq)
    # Offshore quakes belong to a country only within ~3° (about 300 km) of its coast; mid-ocean ridges stay unassigned.
    points_to_cells("earthquakes", eq["latitude"].to_numpy(), eq["longitude"].to_numpy(), max_deg=3)
    mcd_path = RAW / "overture" / "mcdonalds.csv"
    if mcd_path.exists():
        mcd = pd.read_csv(mcd_path).dropna(subset=["lat", "lng"])
        log(f"mcdonalds: {len(mcd):,} places")
        assert len(mcd) > 10_000, len(mcd)
        points_to_cells("mcdonalds", mcd["lat"].to_numpy(), mcd["lng"].to_numpy())
    else:
        log("mcdonalds: no Overture extract yet, skipping")
    # PBDB, birds already excluded by the query (base_name=Dinosauria^Aves). Keep finds dated to the Mesozoic.
    dino = pd.read_csv(RAW / "pbdb" / "dinosauria.csv", low_memory=False).dropna(subset=["lat", "lng"])
    dino = dino[dino["max_ma"] > 66]
    log(f"dinosaurs: {len(dino):,} fossil finds")
    assert 15_000 < len(dino) < 40_000, len(dino)
    points_to_cells("dinosaurs", dino["lat"].to_numpy(), dino["lng"].to_numpy(), max_deg=1)
    lang = pd.read_csv(RAW / "glottolog" / "languages_and_dialects_geo.csv").dropna(subset=["latitude", "longitude"])
    lang = lang[lang["level"] == "language"]
    log(f"languages: {len(lang):,} with a location")
    assert 7_000 < len(lang) < 10_000, len(lang)
    points_to_cells("languages", lang["latitude"].to_numpy(), lang["longitude"].to_numpy(), max_deg=1)
    # HydroLAKES pour points, lakes of 10 ha and up. r5 keeps the file small (1.4 M lakes).
    import pyogrio
    lakes = pyogrio.read_dataframe(RAW / "hydrolakes" / "HydroLAKES_points_v10.shp", columns=["Lake_area"])
    log(f"lakes: {len(lakes):,}, {lakes.Lake_area.sum() / 1e6:.2f} M km²")
    assert 1.3e6 < len(lakes) < 1.5e6 and 2.5e6 < lakes.Lake_area.sum() < 3.2e6, (len(lakes), lakes.Lake_area.sum())
    lat, lng = lakes.geometry.y.to_numpy(), lakes.geometry.x.to_numpy()
    points_to_cells("lakes", lat, lng, max_deg=0.5, res=5)
    points_to_cells("lake-area", lat, lng, max_deg=0.5, weights=lakes["Lake_area"].to_numpy(), res=5)


# ---------------------------------------------------------------- livestock (GLW4 2015, CC0 on Dataverse)

def raster_to_cells(path: Path, res: int):
    """Sum a global count raster (one value per pixel) into H3 cells by pixel centre."""
    import rasterio
    from rasterio.transform import xy

    with rasterio.open(path) as src:
        a = src.read(1).astype(np.float64)
        nodata = src.nodata
        transform = src.transform
    if nodata is not None:
        a[a == nodata] = 0
    a[~np.isfinite(a) | (a < 0)] = 0
    rows, cols = np.nonzero(a > 0)
    xs, ys = xy(transform, rows, cols)
    vals = a[rows, cols]
    ids = np.array([h3.latlng_to_cell(float(y), float(x), res) for x, y in zip(xs, ys)], dtype=np.uint64)
    agg = pd.Series(vals).groupby(ids).sum()
    return agg.index.to_numpy(dtype=np.uint64), agg.to_numpy()


def step_livestock():
    countries = load_countries()
    # Expected world totals (FAO, 2015) as a sanity range.
    for name, lo, hi in (("cattle", 1.2e9, 2.0e9), ("sheep", 0.9e9, 1.6e9), ("pigs", 0.7e9, 1.3e9), ("chickens", 15e9, 28e9), ("goats", 0.8e9, 1.4e9)):
        cells, vals = raster_to_cells(RAW / "glw" / f"{name}.tif", 5)
        log(f"{name}: {vals.sum() / 1e9:.2f} B head in {len(cells):,} r5 cells")
        assert lo < vals.sum() < hi, vals.sum()
        latlng = np.array([h3.cell_to_latlng(int(c)) for c in cells])
        write_cells(name, cells, vals, assign_country(latlng[:, 0], latlng[:, 1], countries), 5)


# ---------------------------------------------------------------- land area

def step_landcells():
    """Every r5 land hex (from the land bitmaps) weighted by its area in km²."""
    idx = json.loads((OUT / "land-r5.json").read_text())
    blob = (OUT / "land-r5.bin").read_bytes()
    cells: list[int] = []
    for p_str in idx["full"]:
        cells.extend(h3.cell_to_children(h3.str_to_int(p_str), 5))
    for p_str, (o, n) in idx["groups"].items():
        kids = sorted(h3.cell_to_children(h3.str_to_int(p_str), 5))
        bits = np.unpackbits(np.frombuffer(blob[o : o + n], dtype=np.uint8), bitorder="little")[: len(kids)]
        cells.extend(kids[i] for i in np.flatnonzero(bits))
    arr = np.array(sorted(set(cells)), dtype=np.uint64)
    area = np.array([h3.cell_area(int(c), "km^2") for c in arr])
    log(f"land: {len(arr):,} r5 cells, {area.sum() / 1e6:.0f} M km²")
    assert 140e6 < area.sum() < 175e6, area.sum()
    latlng = np.array([h3.cell_to_latlng(int(c)) for c in arr])
    write_cells("land", arr, area, assign_country(latlng[:, 0], latlng[:, 1], load_countries()), 5)


def step_bycountry():
    """Backfill per-country totals for files written before write_cells produced them."""
    for name in ("population", "points/earthquakes", "points/mcdonalds"):
        if (OUT / f"{name}.index.json").exists():
            write_bycountry(name)


# ---------------------------------------------------------------- APAD industrial asset data (AWS Open Data)

# name, file in data-raw/apad, weight column (None = count each site), what one unit of weight is
APAD = [
    ("brick-kilns", "Brick_Kilns_Brick_kilns_biomass.csv", None, "kilns"),
    ("igp-coal", "asset_igp_csvs_coal_plants_main.csv", "capacity_power", "MW"),
    ("igp-cement", "asset_igp_csvs_cement_plants_main.csv", None, "plants"),  # capacity missing for 42%
    ("igp-paper", "asset_igp_csvs_paper_pulp_main.csv", None, "mills"),  # capacity missing for 78%
    ("igp-waste", "asset_igp_csvs_waste_main.csv", None, "sites"),
    ("africa-power", "africa_assets_csvs_Power_Generation.csv", "capacity_power", "MW"),
    ("africa-waste", "africa_assets_csvs_waste_Africa_main.csv", "capacity_tonnes", "tonnes"),
]


def load_apad(file: str) -> pd.DataFrame:
    d = pd.read_csv(RAW / "apad" / file, encoding="utf-8-sig", encoding_errors="replace")
    d.columns = [c.strip().replace("\ufffd", "") for c in d.columns]
    d["lat"] = pd.to_numeric(d["lat"], errors="coerce")
    d["lon"] = pd.to_numeric(d["lon"], errors="coerce")
    return d[d.lat.between(-60, 60) & d.lon.between(-180, 180)].reset_index(drop=True)


def step_apad():
    for name, file, wcol, unit in APAD:
        d = load_apad(file)
        if wcol:
            w = pd.to_numeric(d[wcol], errors="coerce")
            w = w.where(w > 0)
            log(f"apad {name}: {w.isna().mean():.0%} missing {wcol}, filled with the median")
            w = w.fillna(w.median()).to_numpy()
        else:
            w = np.ones(len(d))
        points_to_cells(name, d.lat.to_numpy(), d.lon.to_numpy(), weights=w)
        big = int(np.argmax(w))
        nm = re.sub(r"[*]+", "", str(d.loc[big, "name"])).strip() if "name" in d and isinstance(d.loc[big, "name"], str) else ""
        biggest = f"The biggest, {nm}, has {fmt_count(w[big])} {unit}." if wcol and nm else ""
        write_json(OUT / "points" / f"{name}.meta.json", {"sites": len(d), "total": float(w.sum()), "unit": unit, "biggest": biggest})
        log(f"apad {name}: {len(d):,} sites, total {fmt_count(w.sum())} {unit}")


# ---------------------------------------------------------------- best possible shapes

def fine_points(cells_name: str, stat: str | None, scope: list[int] | None = None):
    """Cell centres and weights for one dataset, optionally limited to some countries."""
    idx, cells, value, cidx = read_cells(cells_name)
    w = value
    if stat:
        kg = np.array(json.loads((OUT / "stats" / f"{stat}.json").read_text())["kg"])
        w = value * kg[cidx]
    keep = w > 0
    if scope is not None:
        keep &= np.isin(cidx, scope)
    latlng = np.array([h3.cell_to_latlng(int(c)) for c in cells[keep]])
    return latlng, w[keep]


def unit(latlng):
    la, lo = np.radians(latlng[:, 0]), np.radians(latlng[:, 1])
    return np.stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)], axis=1)


def best_circle(latlng, w, radius_km, n_cand=4000, rank=None, tree=None):
    """Centre that catches the largest sum of w (w may be negative). Returns (lng, lat), sum."""
    from sklearn.neighbors import BallTree

    rad = np.radians(latlng)
    tree = tree or BallTree(rad, metric="haversine")
    r = radius_km / EARTH_R
    order = np.argsort(-(w if rank is None else rank))
    cand = order[: min(len(order), n_cand)]
    sums = np.array([w[ix].sum() for ix in tree.query_radius(rad[cand], r)])
    top = cand[np.argsort(-sums)[:12]]
    step = min(0.6, radius_km / 111 / 3)
    best_c, best_s = None, -np.inf
    for t in top:  # local refinement on a small grid around each top centre
        for dlat in np.linspace(-step, step, 7):
            for dlng in np.linspace(-step, step, 7):
                c = np.radians([[latlng[t, 0] + dlat, latlng[t, 1] + dlng]])
                s_ = w[tree.query_radius(c, r)[0]].sum()
                if s_ > best_s:
                    best_s, best_c = s_, (latlng[t, 1] + dlng, latlng[t, 0] + dlat)
    return best_c, float(best_s)


def smallest_circle_km(latlng, w, frac):
    """Smallest radius (km) we can find whose best circle holds `frac` of the total."""
    from sklearn.neighbors import BallTree

    tree = BallTree(np.radians(latlng), metric="haversine")
    lo, hi, goal = 5.0, 5000.0, frac * w.sum()
    for _ in range(14):
        mid = (lo + hi) / 2
        if best_circle(latlng, w, mid, n_cand=600, tree=tree)[1] >= goal:
            hi = mid
        else:
            lo = mid
    return hi


def tri_mask(U, A, B, C):
    m = (A + B + C)
    front = U @ m > 0
    s1 = U @ np.cross(A, B)
    s2 = U @ np.cross(B, C)
    s3 = U @ np.cross(C, A)
    return front & (((s1 >= 0) & (s2 >= 0) & (s3 >= 0)) | ((s1 <= 0) & (s2 <= 0) & (s3 <= 0)))


def best_triangle(latlng, w, budget_km, seed=1):
    rng = np.random.default_rng(seed)
    # Work on r3-level aggregates for speed, then score the winner on the fine cells.
    keys = np.array([h3.cell_to_parent(h3.latlng_to_cell(a, b, 5), 3) for a, b in latlng], dtype=np.uint64)
    agg = pd.Series(w).groupby(keys).sum()
    cl = np.array([h3.cell_to_latlng(int(k)) for k in agg.index])
    cw = agg.to_numpy()
    U = unit(cl)
    Uf = unit(latlng)

    def tri_ok(p):
        s = [hav_km(p[i][0], p[i][1], p[(i + 1) % 3][0], p[(i + 1) % 3][1]) for i in range(3)]
        return min(s) >= 10 and max(s) <= 8000 and sum(s) <= budget_km

    def score(p, U_, w_):
        A, B, C = unit(np.array(p))
        return w_[tri_mask(U_, A, B, C)].sum()

    heavy = np.argsort(-cw)[:300]
    best_p, best_s = None, -1.0
    for n in range(300):
        top_n = min(40, len(heavy))
        c = cl[heavy[n % top_n]] if n < 3 * top_n else cl[rng.choice(heavy)]  # every top cell gets 3 tries first
        r = budget_km / 3 / 1.732 / 111 * rng.uniform(0.7, 1.0)  # equilateral triangle radius in degrees, roughly
        ang = rng.uniform(0, 2 * np.pi) + np.array([0, 2.094, 4.189])
        p = [[c[0] + r * np.sin(a), c[1] + r * np.cos(a) / max(0.2, np.cos(np.radians(c[0])))] for a in ang]
        while not tri_ok(p):
            p = [[c[0] + (q[0] - c[0]) * 0.9, c[1] + (q[1] - c[1]) * 0.9] for q in p]
        s = score(p, U, cw)
        step = 3.0
        for _ in range(250):
            i = rng.integers(3)
            q = [list(x) for x in p]
            q[i][0] = float(np.clip(q[i][0] + rng.normal(0, step), -85, 85))
            q[i][1] = q[i][1] + rng.normal(0, step)
            if tri_ok(q):
                sq = score(q, U, cw)
                if sq >= s:
                    p, s = q, sq
            step = max(0.05, step * 0.985)
        if s > best_s:
            best_s, best_p = s, p
    fine = score(best_p, Uf, w)
    return [[q[1], q[0]] for q in best_p], 100 * fine / w.sum()


def auto_view(latlng, w):
    """Centre and zoom that frame where most of the weight is (5th to 95th percentile)."""
    def wq(x, q):
        o = np.argsort(x)
        cw = np.cumsum(w[o]) / w.sum()
        return x[o][np.searchsorted(cw, q).clip(0, len(x) - 1)]

    lat0, lat1 = wq(latlng[:, 0], 0.05), wq(latlng[:, 0], 0.95)
    lngs = latlng[:, 1].copy()
    if wq(lngs, 0.95) - wq(lngs, 0.05) > 180:  # straddles the date line (Russia, Fiji, NZ islands), or is spread worldwide
        shifted = np.where(lngs < 0, lngs + 360, lngs)
        if wq(shifted, 0.95) - wq(shifted, 0.05) < wq(lngs, 0.95) - wq(lngs, 0.05):
            lngs = shifted
    lng0, lng1 = wq(lngs, 0.05), wq(lngs, 0.95)
    if lng1 - lng0 > 180:  # worldwide: centre on Europe and Africa like the handwritten world puzzles, not the Pacific
        return [10.0, 25.0, 1.2]
    span = max(lat1 - lat0, (lng1 - lng0) * np.cos(np.radians((lat0 + lat1) / 2)), 0.5)
    zoom = float(np.clip(np.log2(300 / span) - 0.2, 1.2, 8))
    lng = (lng0 + lng1) / 2
    return [round(float(((lng + 180) % 360) - 180), 3), round(float((lat0 + lat1) / 2), 3), round(zoom, 2)]


# ---------------------------------------------------------------- puzzles

POP = "Kontur Population (CC BY 4.0)"
FOOD_CREDIT = "FAO FAOSTAT Food Balances (CC BY 4.0); Kontur Population (CC BY 4.0)"
GLW = "FAO Gridded Livestock of the World 2015, Gilbert et al. 2022 (CC0 / CC BY 4.0)"
LAND = "Natural Earth (public domain)"
QUAKES = "USGS Earthquake Catalog (public domain)"
MCD = "Overture Maps Places (CDLA Permissive 2.0)"
PBDB = "The Paleobiology Database (CC0)"
GLOTTOLOG = "Glottolog 5.4, Hammarström, Forkel, Haspelmath & Bank (CC BY 4.0)"
LAKES = "HydroLAKES v1.0, Messager et al. 2016 (CC BY 4.0)"

APAD_BY = "APAD Asset Data via AWS Open Data (CC BY 4.0)"
APAD_ODC = "APAD Asset Data via AWS Open Data (ODC-By 1.0)"
IGP = {"countries": ["India", "Pakistan", "Bangladesh"], "label": "the Indo-Gangetic Plain"}
AFRICA_APAD = {"continent": "Africa", "label": "Africa"}

# scope: list of Natural Earth country names, or {"continent": "Europe"}. vs: second dataset for "net" puzzles.
PUZZLES = [
    dict(stat="people", cells="population", mechanic="circle", mode="target", target_pct=10,
         prompt="Draw a circle that holds exactly 10% of all people on Earth.", view=[95, 25, 1.6], credit=POP),
    dict(stat="rice eaten", cells="population", food="rice", mechanic="circle", mode="target", target_pct=20,
         prompt="Draw a circle that holds exactly 20% of the world's rice eaten.", view=[100, 20, 1.6]),
    dict(stat="beer drunk", cells="population", food="beer", mechanic="triangle", mode="target", target_pct=25,
         prompt="Put down 3 pins. Your triangle must hold exactly 25% of the world's beer drunk.", view=[10, 35, 1.4]),
    dict(stat="coffee drunk", cells="population", food="coffee", mechanic="circle", mode="budget", radius_km=1500,
         prompt="You get a 1,500 km circle. Catch as much of the world's coffee drunk as you can.", view=[0, 30, 1.4]),
    dict(stat="chocolate eaten", cells="population", food="chocolate", mechanic="triangle", mode="budget", perimeter_km=6000,
         prompt="Your triangle can be at most 6,000 km around. Catch as much chocolate eaten as you can.", view=[0, 40, 1.4]),
    dict(stat="wine drunk", cells="population", food="wine", mechanic="circle", mode="target", target_pct=30,
         prompt="Draw a circle that holds exactly 30% of the world's wine drunk.", view=[10, 40, 1.6]),
    dict(stat="potatoes eaten", cells="population", food="potatoes", mechanic="triangle", mode="target", target_pct=20,
         prompt="Put down 3 pins. Your triangle must hold exactly 20% of the world's potatoes eaten.", view=[40, 40, 1.4]),
    dict(stat="fish eaten", cells="population", food="fish", mechanic="circle", mode="budget", radius_km=1000,
         prompt="You get a 1,000 km circle. Catch as much of the world's fish eaten as you can.", view=[120, 25, 1.6]),
    dict(stat="earthquakes", cells="points/earthquakes", mechanic="triangle", mode="budget", perimeter_km=8000,
         prompt="Your triangle can be at most 8,000 km around. Catch as many earthquakes (M4.5+, since 2000) as you can.",
         view=[150, 0, 1.3], credit=QUAKES),
    dict(stat="mapped McDonald's", cells="points/mcdonalds", mechanic="circle", mode="target", target_pct=10,
         prompt="Draw a circle that holds exactly 10% of the world's mapped McDonald's.", view=[-40, 35, 1.4], credit=MCD),
    # --- batch 2: country and region puzzles, livestock, land
    dict(stat="people", cells="population", scope=["Australia"], mechanic="circle", mode="target", target_pct=50,
         prompt="Draw a circle that holds exactly half of Australia's people.", credit=POP),
    dict(stat="people", cells="population", scope=["Egypt"], mechanic="triangle", mode="target", target_pct=50,
         prompt="Put down 3 pins. Your triangle must hold exactly half of Egypt's people.", credit=POP),
    dict(stat="cattle", cells="cattle", vs="population", vs_label="people", mechanic="circle", mode="net", radius_km=1000,
         prompt="Cows vs people. You get a 1,000 km circle: find where cows outnumber people by the most.",
         view=[0, 0, 1.3], credit=f"{GLW}; {POP}"),
    dict(stat="sheep", cells="sheep", vs="population", vs_label="people", scope=["Australia", "New Zealand"], mechanic="circle",
         mode="net", radius_km=500, prompt="Sheep vs people in Australia and New Zealand. Your 500 km circle should catch the most sheep beyond the number of people.",
         credit=f"{GLW}; {POP}"),
    dict(stat="mapped McDonald's", cells="points/mcdonalds", scope=["United States of America"], mechanic="circle", mode="target",
         target_pct=25, prompt="Draw a circle that holds exactly 25% of America's mapped McDonald's.", credit=MCD),
    dict(stat="earthquakes", cells="points/earthquakes", scope=["Japan"], mechanic="circle", mode="budget", radius_km=300,
         prompt="You get a 300 km circle. Catch as many of Japan's earthquakes (M4.5+, since 2000) as you can.", credit=QUAKES),
    dict(stat="wine drunk", cells="population", food="wine", scope={"continent": "Europe"}, mechanic="triangle", mode="target",
         target_pct=30, prompt="Put down 3 pins. Your triangle must hold exactly 30% of Europe's wine drunk."),
    dict(stat="land", cells="land", mechanic="circle", mode="target", target_pct=10,
         prompt="Draw a circle that covers exactly 10% of all land on Earth.", view=[60, 30, 1.3], credit=LAND),
    dict(stat="land", cells="land", scope=["Canada"], mechanic="triangle", mode="target", target_pct=50,
         prompt="Put down 3 pins. Your triangle must cover exactly half of Canada's land.", credit=LAND),
    dict(stat="cattle", cells="cattle", scope=["India"], mechanic="circle", mode="target", target_pct=25,
         prompt="Draw a circle that holds exactly 25% of India's cattle.", credit=GLW),
    # --- batch 3
    dict(stat="pigs", cells="pigs", mechanic="circle", mode="target", target_pct=25,
         prompt="Draw a circle that holds exactly 25% of the world's pigs.", view=[60, 30, 1.4], credit=GLW),
    dict(stat="chickens", cells="chickens", vs="population", vs_label="people", mechanic="circle", mode="net", radius_km=1000,
         prompt="Chickens vs people. Your 1,000 km circle should catch the most chickens beyond the number of people.",
         view=[0, 20, 1.3], credit=f"{GLW}; {POP}"),
    dict(stat="goats", cells="goats", vs="population", vs_label="people", scope={"continent": "Africa"}, mechanic="circle",
         mode="net", radius_km=800, prompt="Goats vs people in Africa. Your 800 km circle should catch the most goats beyond the number of people.",
         credit=f"{GLW}; {POP}"),
    dict(stat="tea drunk", cells="population", food="tea", mechanic="triangle", mode="budget", perimeter_km=5000,
         prompt="Your triangle can be at most 5,000 km around. Catch as much of the world's tea drunk as you can.", view=[80, 25, 1.4]),
    dict(stat="bananas eaten", cells="population", food="bananas", mechanic="circle", mode="target", target_pct=20,
         prompt="Draw a circle that holds exactly 20% of the world's bananas eaten.", view=[20, 5, 1.3]),
    dict(stat="beer drunk", cells="population", food="beer", scope=["Germany"], mechanic="circle", mode="target", target_pct=50,
         prompt="Draw a circle that holds exactly half of Germany's beer drunk."),
    dict(stat="coffee drunk", cells="population", food="coffee", scope=["Brazil"], mechanic="triangle", mode="target", target_pct=50,
         prompt="Put down 3 pins. Your triangle must hold exactly half of Brazil's coffee drunk."),
    dict(stat="earthquakes", cells="points/earthquakes", scope=["Chile"], mechanic="triangle", mode="target", target_pct=50,
         prompt="Put down 3 pins. Your triangle must hold exactly half of Chile's earthquakes (M4.5+, since 2000).", credit=QUAKES),
    dict(stat="mapped McDonald's", cells="points/mcdonalds", scope=["Japan"], mechanic="circle", mode="budget", radius_km=300,
         prompt="You get a 300 km circle. Catch as many of Japan's mapped McDonald's as you can.", credit=MCD),
    dict(stat="land", cells="land", scope=["Indonesia"], mechanic="triangle", mode="target", target_pct=50,
         prompt="Put down 3 pins. Your triangle must cover exactly half of Indonesia's land.", credit=LAND),
    # --- batch 4: APAD industrial asset data (AWS Open Data)
    # 31 and 32 were brick kilns on the Indo-Gangetic Plain until 20 Nov 2026; Max: too hard, all along one strip.
    dict(stat="chickens", cells="chickens", scope=["United States of America"], mechanic="circle", mode="target", target_pct=50,
         prompt="Draw a circle that holds exactly half of the USA's chickens.", credit=GLW),
    dict(stat="sheep", cells="sheep", scope={"continent": "Oceania"}, mechanic="triangle", mode="target", target_pct=25,
         prompt="Put down 3 pins. Your triangle must hold exactly 25% of Oceania's sheep.", credit=GLW),
    # 33 to 38 and 40 were APAD industrial data (brick kilns, coal, cement, paper, waste) until 20 Nov 2026; Max: weird data on one strip.
    dict(stat="pigs", cells="pigs", scope=["Brazil"], mechanic="circle", mode="target", target_pct=10,
         prompt="Draw a circle that holds exactly 10% of Brazil's pigs.", credit=GLW),
    dict(stat="rice eaten", cells="population", food="rice", scope={"continent": "Africa"}, mechanic="triangle", mode="target",
         target_pct=25, prompt="Put down 3 pins. Your triangle must hold exactly 25% of Africa's rice eaten.", credit=FOOD_CREDIT),
    dict(stat="cattle", cells="cattle", scope=["Mexico"], mechanic="circle", mode="budget", radius_km=400,
         prompt="You get a 400 km circle. Catch as many of Mexico's cattle as you can.", credit=GLW),
    dict(stat="tea drunk", cells="population", food="tea", mechanic="triangle", mode="target", target_pct=10,
         prompt="Put down 3 pins. Your triangle must hold exactly 10% of the world's tea drunk.", credit=FOOD_CREDIT),
    dict(stat="chocolate eaten", cells="population", food="chocolate", mechanic="circle", mode="target", target_pct=25,
         prompt="Draw a circle that holds exactly 25% of the world's chocolate eaten.", credit=FOOD_CREDIT),
    dict(stat="coffee drunk", cells="population", food="coffee", mechanic="triangle", mode="target", target_pct=25,
         prompt="Put down 3 pins. Your triangle must hold exactly 25% of the world's coffee drunk.", credit=FOOD_CREDIT),
    # 39 was Africa's gas and oil power capacity until 20 Nov 2026: a few plants hold it all, a first guess scored 0 at any size.
    dict(stat="potatoes eaten", cells="population", food="potatoes", scope={"continent": "Africa"}, mechanic="triangle", mode="target",
         target_pct=10, prompt="Put down 3 pins. Your triangle must hold exactly 10% of Africa's potatoes eaten.", credit=FOOD_CREDIT),
    dict(stat="land", cells="land", scope=["Russia"], mechanic="circle", mode="target", target_pct=20,
         prompt="Draw a circle that covers exactly 20% of Russia's land.", credit=LAND),
]


def resolve_scope(scope, countries) -> tuple[list[int] | None, str | None]:
    if scope is None:
        return None, None
    if isinstance(scope, dict) and "continent" in scope:
        ids = [int(i) for i in countries.index[countries["CONTINENT"] == scope["continent"]]]
        return ids, scope.get("label", scope["continent"])
    if isinstance(scope, dict):
        ids, _ = resolve_scope(scope["countries"], countries)
        return ids, scope["label"]
    ids = []
    for name in scope:
        hit = countries.index[countries["ADMIN"] == name]
        assert len(hit), f"unknown country {name}"
        ids.extend(int(i) for i in hit)
    label = scope[0] if len(scope) == 1 else " and ".join(scope)
    return ids, label.replace("United States of America", "the USA")


def fmt_count(x: float) -> str:
    if abs(x) >= 1e9:
        return f"{x / 1e9:.1f} billion"
    if abs(x) >= 1e6:
        return f"{x / 1e6:.1f} million"
    return f"{round(x):,}"


def pct_text(s):
    return f"{s * 100:.0f}%"


def country_shares(cells: str, food: str | None, scope: list[int] | None, countries):
    bc = json.loads((OUT / f"{cells}.bycountry.json").read_text())
    kg = json.loads((OUT / "stats" / f"{food}.json").read_text())["kg"] if food else None
    tot = {int(c): sum(v.values()) * (kg[int(c)] if kg else 1) for c, v in bc.items()}
    if scope is not None:
        tot = {c: v for c, v in tot.items() if c in scope}
    s = sum(tot.values())
    return [(countries.loc[c, "ADMIN"] if c > 0 else "?", v / s, v) for c, v in sorted(tot.items(), key=lambda x: -x[1]) if c > 0]


def make_fact(p, latlng, w, scope_ids, scope_label, countries) -> str:
    meta = OUT / f"{p['cells']}.meta.json"
    if p.get("fact_biggest") and meta.exists() and json.loads(meta.read_text())["biggest"]:
        return json.loads(meta.read_text())["biggest"]
    if p["mode"] == "net":
        a = json.loads((OUT / f"{p['cells']}.bycountry.json").read_text())
        b = json.loads((OUT / f"{p['vs']}.bycountry.json").read_text())
        ratios = []
        for c, v in a.items():
            if int(c) == 0 or (scope_ids is not None and int(c) not in scope_ids):
                continue
            people = sum(b.get(c, {}).values())
            if people > (2e5 if scope_ids else 2e6):
                ratios.append((sum(v.values()) / people, countries.loc[int(c), "ADMIN"]))
        ratios.sort(reverse=True)
        if len(ratios) >= 2:
            (r1, n1), (r2, n2) = ratios[0], ratios[1]
            return f"{n1} has {r1:.1f} {p['stat']} per person. {n2} has {r2:.1f}."
        if ratios:  # a single-country scope
            return f"{ratios[0][1]} has {ratios[0][0]:.1f} {p['stat']} per person."
        # no country big enough to compare: fall through to the share facts
    if p["mode"] == "target" and scope_ids is not None:
        km = p.get("smallest_km") or smallest_circle_km(latlng, w, p["target_pct"] / 100)
        return f"The smallest circle we found holding {p['target_pct']}% of {whose(scope_label)} {p['stat']} has a radius of {fmt_count(km)} km."
    shares = country_shares(p["cells"], p.get("food"), scope_ids, countries)
    if scope_ids is not None:
        n, share, v = shares[0]
        if p.get("food"):  # "Germany has 5.4 billion beer drunk" reads wrong for kg totals
            return f"{n} accounts for {pct_text(share)} of {whose(scope_label)} {p['stat']}."
        if p["cells"] == "points/earthquakes":
            return f"{n} recorded {fmt_count(v)} {p['stat']} since 2000."
        if p["cells"] == "land":  # hex areas overcount coasts a few %, hence "about"
            return f"{n} has about {fmt_count(v)} km² of land."
        if p["cells"] == "points/lake-area":
            return f"{n} has {fmt_count(v)} km² of lakes."
        return f"{n} has {fmt_count(v)} {p['stat']}."
    if p["cells"] == "population" and not p.get("food"):
        return "Half of all people live in a circle about 4,000 km wide over Asia."
    (n1, s1, _), (n2, s2, _), (n3, s3, _) = shares[:3]
    what = "the world's land" if p["stat"] == "land" else f"the world's {p['stat']}"
    return f"{n1} alone accounts for {pct_text(s1)} of {what}. {n2} and {n3} follow with {pct_text(s2)} and {pct_text(s3)}."


def polish_fact(fact: str, stat: str) -> str:
    for name, short in {**SCOPE_NAMES, "United States of America": "the USA"}.items():
        fact = re.sub(rf"(?<!the ){re.escape(name)}", short, fact)  # "Philippines" -> "the Philippines", once
    fact = re.sub(r"(^|\. )([a-z])", lambda m: m.group(1) + m.group(2).upper(), fact)  # "the USA" opening a sentence
    return fact.replace("holding", "covering") if stat == "land" else fact


def build_puzzle(pid, p, countries):
    scope_ids, scope_label = resolve_scope(p.get("scope"), countries)
    latlng, w = fine_points(p["cells"], p.get("food"), scope_ids)
    best = {"pct": None, "net": None, "shape": None}
    if p["mode"] == "budget":
        if p["mechanic"] == "circle":
            c, s_ = best_circle(latlng, w, p["radius_km"])
            best = {"pct": round(100 * s_ / w.sum(), 2), "net": None, "shape": {"center": [round(c[0], 4), round(c[1], 4)], "radius_km": p["radius_km"]}}
        else:
            pins, pct = best_triangle(latlng, w, p["perimeter_km"])
            best = {"pct": round(pct, 2), "net": None, "shape": {"pins": [[round(a, 4), round(b, 4)] for a, b in pins]}}
    elif p["mode"] == "net":
        lb, wb = fine_points(p["vs"], None, scope_ids)
        ll = np.vstack([latlng, lb])
        ww = np.r_[w, -wb]
        c, s_ = best_circle(ll, ww, p["radius_km"], n_cand=5000, rank=np.r_[w, np.zeros(len(wb))])
        best = {"pct": None, "net": round(s_), "shape": {"center": [round(c[0], 4), round(c[1], 4)], "radius_km": p["radius_km"]}}
    view = p.get("view") or auto_view(latlng, w)
    fact = polish_fact(make_fact(p, latlng, w, scope_ids, scope_label, countries), p["stat"])
    out = {
        "id": pid,
        "stat": p["stat"],
        "prompt": p["prompt"],
        "mechanic": p["mechanic"],
        "mode": p["mode"],
        "target_pct": p.get("target_pct"),
        "radius_km": p.get("radius_km"),
        "perimeter_km": p.get("perimeter_km"),
        "best": best,
        "cells": f"/data/{p['cells']}",
        "stat_file": f"/data/stats/{p['food']}.json" if p.get("food") else None,
        "scope": {"name": scope_label, "countries": scope_ids} if scope_ids is not None else None,
        "vs": {"cells": f"/data/{p['vs']}", "label": p["vs_label"]} if p.get("vs") else None,
        "view": {"center": view[:2], "zoom": view[2]},
        "fact": fact,
        "credit": p.get("credit", FOOD_CREDIT),
    }
    log(f"puzzle {pid} ({p['stat']}{', ' + scope_label if scope_label else ''}): best {best['pct'] or best['net']} | {out['fact']}")
    return out


def merge_curated(legacy, curated):
    """puzzles.json = legacy + approved curated entries, sorted by ID (PRD §5.8). The reviewed fact and prompt win."""
    out = list(legacy)
    for key, e in curated.items():
        if e.get("status") != "approved":
            continue
        assert isinstance(e["id"], int) and e["id"] > len(PUZZLES), f"{key}: id {e['id']} clashes with the legacy puzzles"
        out.append({"id": e["id"], **e["puzzle"], "key": key, "fact": e["fact"], "prompt": e["prompt"] or e["puzzle"]["prompt"]})
    ids = [p["id"] for p in out]
    assert len(ids) == len(set(ids)), "duplicate puzzle ids in curated.json"
    return sorted(out, key=lambda p: p["id"])


FUNFACTS = ROOT / "puzzles" / "funfacts.json"


def attach_funfacts(puzzles, funfacts):
    """Adds one approved "Did you know?" fact per puzzle from its dataset's list, rotating by ID so repeats differ."""
    for p in puzzles:
        p.pop("funfact", None)
        facts = [f for f in funfacts.get(puzzle_dataset(p), []) if f.get("approved")]
        if facts:
            f = facts[p["id"] % len(facts)]
            p["funfact"] = {"text": f["text"], "source": f["source"]}
    return puzzles


def write_puzzles(legacy):
    curated = json.loads(CURATED.read_text()) if CURATED.exists() else {}
    funfacts = json.loads(FUNFACTS.read_text()) if FUNFACTS.exists() else {}
    out = attach_funfacts(merge_curated(legacy, curated), funfacts)
    write_json(OUT / "puzzles.json", out)
    log(f"puzzles.json: {len(legacy)} legacy + {len(out) - len(legacy)} approved = {len(out)}")


def step_puzzles():
    countries = load_countries()
    out = [build_puzzle(i, p, countries) for i, p in enumerate(PUZZLES, start=1) if (OUT / f"{p['cells']}.index.json").exists()]
    assert len(out) == len(PUZZLES), "some puzzle data is missing"
    write_puzzles(out)


def step_publish():
    """Re-merges curated.json into puzzles.json without recomputing the legacy puzzles."""
    path = OUT / "puzzles.json"
    legacy = [p for p in json.loads(path.read_text()) if isinstance(p["id"], int) and p["id"] <= len(PUZZLES)] if path.exists() else []
    assert len(legacy) == len(PUZZLES), "legacy puzzles missing from puzzles.json, run: pnpm data puzzles"
    write_puzzles(legacy)


LAUNCH_DAY = "2026-11-18"  # same as lib/puzzles.ts; 18 Nov to 27 Dec are puzzles 1 to 40
SCHEDULE_AHEAD = 60
WEEKDAY_LEVEL = ["easy", "easy", "medium", "medium", "medium", "hard", "hard"]  # Monday first
LEVELS = ["easy", "medium", "hard"]


def puzzle_dataset(p):
    """Dataset name from the files a puzzle reads: works for legacy and generated puzzles alike."""
    return Path(p.get("stat_file") or p["cells"]).name.removesuffix(".json")


def fill_schedule(schedule, puzzles, today, ahead=SCHEDULE_AHEAD):
    """Fills empty dates from today to today + ahead (PRD §5.9). Never changes a date already set.

    Picks, per date, the puzzle that breaks the fewest rules in priority order:
    weekday difficulty (distance to the wanted level), dataset within 7 days, mechanic 3 days running,
    no budget/net puzzle in 7 days. Ties go to the lowest ID (approved first)."""
    by_id = {p["id"]: p for p in puzzles}
    out = dict(schedule)
    launch = dt.date.fromisoformat(LAUNCH_DAY)
    for i in range(len(PUZZLES)):
        out.setdefault((launch + dt.timedelta(i)).isoformat(), i + 1)
    used = set(out.values())
    pool = [p for p in puzzles if isinstance(p["id"], int) and p["id"] not in used]
    at = lambda d: by_id.get(out.get(d.isoformat()))  # noqa: E731

    for k in range(ahead + 1):
        d = today + dt.timedelta(k)
        if d.isoformat() in out or not pool:
            continue
        want = LEVELS.index(WEEKDAY_LEVEL[d.weekday()])
        near = [at(d + dt.timedelta(j)) for j in range(-6, 7) if j]  # shortcut: looks both ways for datasets only
        recent_ds = {puzzle_dataset(q) for q in near if q}
        prev = [at(d - dt.timedelta(j)) for j in (1, 2)]
        run_mech = prev[0]["mechanic"] if all(prev) and prev[0]["mechanic"] == prev[1]["mechanic"] else None
        week = [at(d - dt.timedelta(j)) for j in range(1, 7)]
        need_size = not any(q and q["mode"] != "target" for q in week)

        def cost(p):
            return (
                abs(LEVELS.index(p.get("difficulty") or "medium") - want),
                puzzle_dataset(p) in recent_ds,
                p["mechanic"] == run_mech,
                need_size and p["mode"] == "target",
                p["id"],
            )

        pick = min(pool, key=cost)
        out[d.isoformat()] = pick["id"]
        pool.remove(pick)
    return dict(sorted(out.items()))


def days_ahead(schedule, today):
    """Consecutive scheduled days starting today."""
    n = 0
    while (today + dt.timedelta(n)).isoformat() in schedule:
        n += 1
    return n


def step_schedule():
    path = OUT / "schedule.json"
    puzzles = json.loads((OUT / "puzzles.json").read_text())
    old = json.loads(path.read_text()) if path.exists() else {}
    ids = {p["id"] for p in puzzles}
    missing = sorted({v for v in old.values() if v not in ids})
    assert not missing, f"schedule.json uses unknown puzzle ids {missing}, run: pnpm data publish"
    twice = sorted({v for v in old.values() if list(old.values()).count(v) > 1})
    if twice:
        log(f"warning: puzzles scheduled more than once: {twice}")
    today = dt.date.today()
    new = fill_schedule(old, puzzles, today)
    path.write_text(json.dumps(new, indent=0) + "\n")  # one date per line, easy to edit by hand
    unscheduled = sum(1 for p in puzzles if p["id"] not in set(new.values()))
    log(f"schedule: {len(new) - len(old)} days added, {days_ahead(new, today)} days scheduled ahead (target 60), {unscheduled} approved puzzles left")


def step_countries():
    """'Halve the country' puzzles: one circle puzzle per country with enough population cells."""
    countries = load_countries()
    idx, cells, pop, cidx = read_cells("population")
    out, used = [], set()
    for ci, r in countries.iterrows():
        mask = cidx == ci
        if mask.sum() < 25 or pop[mask].sum() < 2e5:
            continue
        code = r.ISO_A3_EH if isinstance(r.ISO_A3_EH, str) and r.ISO_A3_EH not in ("-99", "") else r.ADMIN[:3].upper()
        if code in used:
            continue
        used.add(code)
        latlng = np.array([h3.cell_to_latlng(int(c)) for c in cells[mask]])
        w = pop[mask]
        name = r.ADMIN.replace("United States of America", "the USA").replace("United Kingdom", "the UK")
        km = smallest_circle_km(latlng, w, 0.5)
        view = auto_view(latlng, w)
        out.append(
            {
                "id": f"c-{code.lower()}",
                "code": code,
                "country": r.ADMIN,
                "continent": r.CONTINENT,
                "population": round(float(w.sum())),
                "stat": "people",
                "prompt": f"Draw a circle that holds exactly half of {name}'s people.",
                "mechanic": "circle",
                "mode": "target",
                "target_pct": 50,
                "radius_km": None,
                "perimeter_km": None,
                "best": {"pct": None, "net": None, "shape": None},
                "cells": "/data/population",
                "stat_file": None,
                "scope": {"name": name, "countries": [int(ci)]},
                "vs": None,
                "view": {"center": view[:2], "zoom": view[2]},
                "fact": f"The smallest circle we found holding half of {name}'s people has a radius of {fmt_count(km)} km.",
                "credit": POP,
            }
        )
    out.sort(key=lambda x: x["country"])
    log(f"countries: {len(out)} 'halve the country' puzzles")
    assert len(out) > 150, len(out)
    write_json(OUT / "countries-mode.json", out)


# ---------------------------------------------------------------- puzzle generator (PRD-puzzle-generator.md)

GEN_OUT = ROOT / "scripts" / "pipeline" / "out"
CURATED = ROOT / "puzzles" / "curated.json"

FOOD_STAT = {"rice": "rice eaten", "beer": "beer drunk", "coffee": "coffee drunk", "chocolate": "chocolate eaten",
             "wine": "wine drunk", "potatoes": "potatoes eaten", "fish": "fish eaten", "tea": "tea drunk",
             "bananas": "bananas eaten", "eggs": "eggs eaten", "yams": "yams eaten", "cassava": "cassava eaten",
             "plantains": "plantains eaten", "dates": "dates eaten", "rye": "rye eaten", "pork": "pork eaten",
             "millet": "millet eaten", "sorghum": "sorghum eaten", "mutton": "mutton and goat meat eaten",
             "sweet-potatoes": "sweet potatoes eaten", "olive-oil": "olive oil used", "apples": "apples eaten"}

# One entry per dataset. licence_ok: True only if GAME-SPEC §4 marks it ✅ or Max checked the source page.
DATASETS = [
    dict(key="population", cells="population", stat="people", noun="count", credit=POP, licence_ok=True, baseline=True),
    dict(key="land", cells="land", stat="land", noun="mass", credit=LAND, licence_ok=True, baseline=True),
    *[dict(key=k, cells="population", food=k, stat=FOOD_STAT[k], noun="mass", credit=FOOD_CREDIT, licence_ok=True) for k in FOOD],
    dict(key="earthquakes", cells="points/earthquakes", stat="earthquakes", prompt_stat="earthquakes (M4.5+, since 2000)", noun="count", credit=QUAKES, licence_ok=True),
    dict(key="mcdonalds", cells="points/mcdonalds", stat="mapped McDonald's", noun="count", credit=MCD, licence_ok=True),
    # PBDB says CC0 in its API's datainfo header; Glottolog and HydroLAKES say CC BY 4.0 on their download pages (checked 20 Nov 2026).
    dict(key="dinosaurs", cells="points/dinosaurs", stat="dinosaur fossil finds", noun="count", credit=PBDB, licence_ok=True),
    dict(key="languages", cells="points/languages", stat="languages", noun="count", credit=GLOTTOLOG, licence_ok=True),
    dict(key="lakes", cells="points/lakes", stat="lakes", prompt_stat="lakes (10 hectares and up)", noun="count", credit=LAKES, licence_ok=True),
    dict(key="lake-area", cells="points/lake-area", stat="lake area", noun="mass", credit=LAKES, licence_ok=True),
    # GLW: each 2015 species record on Harvard Dataverse is CC0, FAO's catalogue says CC BY 4.0; both allow commercial use (checked 20 Nov 2026).
    *[dict(key=k, cells=k, stat=k, noun="count", credit=GLW, licence_ok=True, vs=("population", "people"))
      for k in ("cattle", "sheep", "pigs", "chickens", "goats")],
    # APAD: CC BY 4.0 / ODC-By 1.0 (both allow commercial use), checked when puzzles 31 to 40 were added.
    dict(key="brick-kilns", off=True, cells="points/brick-kilns", stat="brick kilns", prompt_stat="mapped brick kilns", noun="count", credit=APAD_BY, licence_ok=True, coverage=["Indo-Gangetic Plain"]),
    dict(key="igp-coal", off=True, cells="points/igp-coal", stat="coal power capacity", noun="mass", credit=APAD_BY, licence_ok=True, coverage=["Indo-Gangetic Plain"]),
    dict(key="igp-cement", off=True, cells="points/igp-cement", stat="cement plants", noun="count", credit=APAD_ODC, licence_ok=True, coverage=["Indo-Gangetic Plain"]),
    dict(key="igp-paper", off=True, cells="points/igp-paper", stat="paper mills", noun="count", credit=APAD_ODC, licence_ok=True, coverage=["Indo-Gangetic Plain"]),
    dict(key="igp-waste", off=True, cells="points/igp-waste", stat="plastic waste and dump sites", noun="count", credit=APAD_ODC, licence_ok=True, coverage=["Indo-Gangetic Plain"]),
    dict(key="africa-power", off=True, cells="points/africa-power", stat="mapped gas and oil power capacity", noun="mass", credit=APAD_BY, licence_ok=True, coverage=["Africa"]),
    dict(key="africa-waste", off=True, cells="points/africa-waste", stat="waste tonnage at mapped dump sites", noun="mass", credit=APAD_ODC, licence_ok=True, coverage=["Africa"]),
]
CONTINENTS = ["Africa", "Asia", "Europe", "North America", "Oceania", "South America"]
REGIONS = {"Indo-Gangetic Plain": IGP}
TARGET_PCTS = [10, 20, 25, 30, 50]  # 50 only for country and region scopes
RADII = [100, 150, 200, 250, 300, 400, 500, 750, 1000, 1500, 2000]
PERIMETERS = [1000, 1500, 2000, 3000, 4000, 5000, 6000, 8000, 10000]
# Thresholds (PRD §5.3, §5.4). Starting values until calibration; changing them needs no re-measuring.
GATE = dict(min_cells=200, max_top_share=0.20, min_surprise=0.25, budget_lo=20, budget_hi=60, budget_aim=35,
            country_min_share=0.02, reach_margin=10, min_smallest_km=25, prompt_max=140, easy_from=55, medium_from=30,
            max_naive=95, continent_min_share=0.05)  # 5%: Max disliked "a few spots hold it all" puzzles
# Measurement settings. They are part of the cache hash, as is GEN_VERSION: bump it when gen_measure changes.
MEASURE = dict(fine_max=40_000, circle_max_km=5000, side_max_km=8000, tri_fallback_margin=10, sim_shapes=400, sim_sigma=0.4,
               net_jitter_km=500)
GEN_VERSION = "4"
SCOPE_NAMES = {"United States of America": "the USA", "United Kingdom": "the UK", "Netherlands": "the Netherlands",
               "Philippines": "the Philippines", "Solomon Islands": "the Solomon Islands",
               "United Republic of Tanzania": "Tanzania", "South Georgia and the Islands": "South Georgia",
               "Democratic Republic of the Congo": "DR Congo", "Republic of the Congo": "the Republic of the Congo"}


def tvd(a: np.ndarray, p: np.ndarray) -> float:
    """Total variation distance between two weight vectors over the same cells: 0 = same layout, 1 = no overlap."""
    return float(0.5 * np.abs(a / a.sum() - p / p.sum()).sum())


def cheap_fails(w: np.ndarray, mode: str, target_pct: int | None, surprise: float, baseline: bool) -> list[str]:
    """Checks that only need the scope's fine-cell weights (w > 0) and the surprise score."""
    fails = []
    top = w.max() / w.sum() if len(w) else 1.0
    if len(w) < GATE["min_cells"]:
        fails.append("cells")
    if top >= GATE["max_top_share"] or (mode == "target" and top >= target_pct / 200):
        fails.append("hotspot")
    if not baseline and surprise < GATE["min_surprise"]:
        fails.append("surprise")
    return fails


def best_r3_sums(ll_rad: np.ndarray, w: np.ndarray, radii_km, rank: np.ndarray | None = None, n_cand=600) -> dict:
    """Best circle sum per radius, with r3 cell centres as both points and candidate centres. An estimate for picking sizes."""
    from sklearn.neighbors import BallTree

    nz = np.flatnonzero(w != 0)
    pts, ww = ll_rad[nz], w[nz]
    tree = BallTree(pts, metric="haversine")
    cand = np.argsort(-(ww if rank is None else rank[nz]))[:n_cand]
    return {r: float(max(ww[ix].sum() for ix in tree.query_radius(pts[cand], r / EARTH_R))) for r in radii_km}


def pick_budget_radius(frac_by_radius: dict) -> int:
    return min(frac_by_radius, key=lambda r: (abs(frac_by_radius[r] - GATE["budget_aim"] / 100), r))


def nearest_perimeter(radius_km: int) -> int:
    """An equilateral triangle with perimeter 8r has about the area of a circle of radius r."""
    return min(PERIMETERS, key=lambda p: (abs(p - 8 * radius_km), p))


def gen_key(dataset: str, scope: str, mechanic: str, mode: str, param) -> str:
    return f"{dataset}|{scope}|{mechanic}|{mode}|{param}"


def country_code(c: dict) -> str:
    return c["iso3"] if c["iso3"] not in ("-99", "", None) else c["name"][:3].upper()


def gen_scopes(countries: list[dict]) -> list[dict]:
    """World, continents and named regions. Single countries are added per dataset."""
    by_name = {c["name"]: c["i"] for c in countries}
    out = [dict(code="world", name="the world", kind="world", ids=None)]
    out += [dict(code=k, name=k, kind="continent", ids=[c["i"] for c in countries if c["continent"] == k]) for k in CONTINENTS]
    out += [dict(code=k, name=v["label"], kind="region", ids=[by_name[n] for n in v["countries"]]) for k, v in REGIONS.items()]
    return out


def legacy_key(p: dict, countries: list[dict]) -> str:
    d = next(d for d in DATASETS if d["cells"] == p["cells"] and d.get("food") == p.get("food"))
    s = p.get("scope")
    if s is None:
        code = "world"
    elif isinstance(s, dict) and "continent" in s:
        code = s["continent"]
    elif isinstance(s, dict):
        code = next(k for k, v in REGIONS.items() if v["countries"] == s["countries"])
    else:
        iso = {c["name"]: country_code(c) for c in countries}
        code = "+".join(iso[n] for n in s)
    param = p.get("target_pct") or p.get("radius_km") or p.get("perimeter_km")
    return gen_key(d["key"], code, p["mechanic"], p["mode"], param)


def load_weights(d: dict, r3_vocab: dict, cache: dict):
    """Fine-cell weights, country index and r3 parent id (into r3_vocab) for one dataset."""
    if d["cells"] not in cache:
        idx = json.loads((OUT / f"{d['cells']}.index.json").read_text())
        rec = np.fromfile(OUT / f"{d['cells']}.bin", dtype=REC)
        r3 = np.empty(len(rec), dtype=np.int64)
        for p_str, (o, n, *_) in idx["groups"].items():
            r3[o : o + n] = r3_vocab.setdefault(p_str, len(r3_vocab))
        cache[d["cells"]] = (rec["value"].astype(np.float64), rec["country"].astype(np.int64), r3)
    w, cidx, r3 = cache[d["cells"]]
    if d.get("food"):
        w = w * np.array(json.loads((OUT / "stats" / f"{d['food']}.json").read_text())["kg"])[cidx]
    return w, cidx, r3


def legacy_scope(s, countries: list[dict]) -> tuple[list[int] | None, str, str]:
    """(country ids, display name, kind) for a handwritten puzzle's scope."""
    if s is None:
        return None, "the world", "world"
    if isinstance(s, dict) and "continent" in s:
        return [c["i"] for c in countries if c["continent"] == s["continent"]], s["continent"], "continent"
    by_name = {c["name"]: c["i"] for c in countries}
    if isinstance(s, dict):
        return [by_name[n] for n in s["countries"]], s["label"], "region"
    return [by_name[n] for n in s], " and ".join(SCOPE_NAMES.get(n, n) for n in s), "country" if len(s) == 1 else "region"


def param_name(mechanic: str, mode: str) -> str:
    return "target_pct" if mode == "target" else "perimeter_km" if mechanic == "triangle" else "radius_km"


def whose(name: str) -> str:
    """'the world' -> "the world's", 'the Philippines' -> "the Philippines'"."""
    return name + ("'" if name.endswith("s") else "'s")


def make_prompt(d: dict, scope_name: str, scope_kind: str, mechanic: str, mode: str, param: int) -> str:
    stat = d.get("prompt_stat", d["stat"])
    of = whose(scope_name)
    much = "much" if d["noun"] == "mass" else "many"
    hold = "cover" if d["key"] == "land" else "hold"
    if mode == "target":
        pct = "half" if param == 50 else f"{param}%"
        if mechanic == "circle":
            return f"Draw a circle that {hold}s exactly {pct} of {of} {stat}."
        return f"Put down 3 pins. Your triangle must {hold} exactly {pct} of {of} {stat}."
    if mode == "budget":
        lead = f"You get a {param:,} km circle." if mechanic == "circle" else f"Your triangle can be at most {param:,} km around."
        return f"{lead} Catch as {much} of {of} {stat} as you can."
    where = "" if scope_kind == "world" else f" in {scope_name}"
    return f"{stat[0].upper() + stat[1:]} vs people{where}. Your {param:,} km circle should catch the most {stat} beyond the number of people."


def prompt_ok(s: str) -> bool:
    return s.endswith(".") and "  " not in s and "the the" not in s.lower() and len(s) <= GATE["prompt_max"]


# Score formulas, same as lib/score.ts (JS Math.round = floor(x + 0.5)).
def js_round(x: float) -> int:
    return int(math.floor(x + 0.5))


def score_target(pct: float, target: float) -> int:
    return max(0, js_round(100 * (1 - abs(pct - target) / target)))


def score_budget(pct: float, best: float) -> int:
    return 0 if best <= 0 else min(100, js_round(100 * pct / best))


def score_net(net: float, best: float) -> int:
    return 0 if best <= 0 else max(0, min(100, js_round(100 * net / best)))


def difficulty_label(naive_median: float) -> str:
    return "easy" if naive_median >= GATE["easy_from"] else "medium" if naive_median >= GATE["medium_from"] else "hard"


def destination(lat, lng, bearing_rad, dist_km):
    """Point(s) dist_km from (lat, lng) along a bearing, in degrees."""
    p1, l1, d = np.radians(lat), np.radians(lng), np.asarray(dist_km) / EARTH_R
    p2 = np.arcsin(np.sin(p1) * np.cos(d) + np.cos(p1) * np.sin(d) * np.cos(bearing_rad))
    l2 = l1 + np.arctan2(np.sin(bearing_rad) * np.sin(d) * np.cos(p1), np.cos(d) - np.sin(p1) * np.sin(p2))
    return np.degrees(p2), (np.degrees(l2) + 540) % 360 - 180


def naive_median(spec: dict, P_ll, P_w, S_ll, S_w, best_pct, best_net) -> float:
    """Median score of a simulated player who assumes the stat sits where people live (PRD §5.4). r3 aggregates."""
    rng = np.random.default_rng(int(hashlib.sha1(spec["key"].encode()).hexdigest()[:8], 16))
    PU, SU = unit(P_ll), unit(S_ll)
    Ptot, Stot = P_w.sum(), S_w.sum()
    mech, mode, n = spec["mechanic"], spec["mode"], MEASURE["sim_shapes"]
    if mode == "net":
        i = rng.choice(len(S_w), n, p=S_w / Stot)
        lat, lng = destination(S_ll[i, 0], S_ll[i, 1], rng.uniform(0, 2 * np.pi, n), rng.uniform(0, MEASURE["net_jitter_km"], n))
    else:
        i = rng.choice(len(P_w), n, p=P_w / Ptot)
        lat, lng = P_ll[i, 0], P_ll[i, 1]
    noise = rng.lognormal(0, MEASURE["sim_sigma"], n)
    turn = rng.uniform(0, 2 * np.pi, n)
    scores = []
    for k in range(n):
        c = unit(np.array([[lat[k], lng[k]]]))[0]
        dS = np.arccos(np.clip(SU @ c, -1, 1)) * EARTH_R
        if mode == "target":
            dP = np.arccos(np.clip(PU @ c, -1, 1)) * EARTH_R
            o = np.argsort(dP)
            j = min(len(o) - 1, int(np.searchsorted(np.cumsum(P_w[o]), spec["target_pct"] / 100 * Ptot)))
            r = float(np.clip(dP[o][j] * noise[k], 10, 5000))
        else:
            r = spec["radius_km"] or spec["perimeter_km"] / 3 / math.sqrt(3) / 1.5551  # triangle budget: see below
        if mech == "circle":
            inside = dS <= r
        else:
            # Equilateral triangle with the circle's area: circumradius 1.5551 r. Budget: side = perimeter / 3.
            R = min(r * 1.5551, MEASURE["side_max_km"] / math.sqrt(3))
            vl, vg = destination(lat[k], lng[k], turn[k] + np.array([0, 2 * np.pi / 3, 4 * np.pi / 3]), R)
            A, B, C = unit(np.stack([vl, vg], axis=1))
            inside = tri_mask(SU, A, B, C)
        pct = 100 * S_w[inside].sum() / Stot
        if mode == "target":
            scores.append(score_target(pct, spec["target_pct"]))
        elif mode == "budget":
            scores.append(score_budget(pct, best_pct))
        else:
            dP = np.arccos(np.clip(PU @ c, -1, 1)) * EARTH_R
            scores.append(score_net(S_w[inside].sum() - P_w[dP <= r].sum(), best_net))
    return float(np.median(scores))


_PTS: dict = {}


def scope_points(cells: str, food: str | None, ids: list[int] | None, coarse=False):
    """Weighted points for one dataset in a scope: fine cell centres, or r3 aggregates when coarse or too many cells."""
    if cells not in _PTS:
        idx, fine, value, cidx = read_cells(cells)
        r3 = np.empty(len(fine), dtype=np.uint64)
        for p_str, (o, n, *_) in idx["groups"].items():
            r3[o : o + n] = h3.str_to_int(p_str)
        _PTS[cells] = (np.array([h3.cell_to_latlng(int(c)) for c in fine]), value, cidx, r3)
    ll, w, cidx, r3 = _PTS[cells]
    if food:
        if ("kg", food) not in _PTS:
            _PTS[("kg", food)] = np.array(json.loads((OUT / "stats" / f"{food}.json").read_text())["kg"])
        w = w * _PTS[("kg", food)][cidx]
    m = w > 0
    if ids is not None:
        m &= np.isin(cidx, ids)
    if not coarse and m.sum() <= MEASURE["fine_max"]:
        return ll[m], w[m]
    u, inv = np.unique(r3[m], return_inverse=True)
    return np.array([h3.cell_to_latlng(int(x)) for x in u]).reshape(-1, 2), np.bincount(inv, weights=w[m])


def gen_measure(spec: dict) -> dict:
    """Expensive measurements for one candidate (runs in a worker): best shapes, reach, difficulty, view, fact."""
    if "countries_df" not in _PTS:
        _PTS["countries_df"] = pd.DataFrame(json.loads((OUT / "countries.json").read_text())).set_index("i").rename(columns={"name": "ADMIN"})
    ids, mech, mode = spec["countries"], spec["mechanic"], spec["mode"]
    ll, w = scope_points(spec["cells"], spec["food"], ids)
    tot = w.sum()
    out = dict(reach_pct=None, smallest_km=None, best=dict(pct=None, net=None, shape=None))
    if mode == "target":
        pct = spec["target_pct"]
        out["smallest_km"] = round(smallest_circle_km(ll, w, pct / 100), 1)
        if mech == "circle":
            reach = 100 * best_circle(ll, w, MEASURE["circle_max_km"], n_cand=600)[1] / tot
        else:  # the biggest triangle's incircle is a cheap lower bound; search triangles only if it falls short
            reach = 100 * best_circle(ll, w, MEASURE["side_max_km"] / (2 * math.sqrt(3)), n_cand=600)[1] / tot
            if reach < pct + MEASURE["tri_fallback_margin"]:
                reach = max(reach, best_triangle(ll, w, 3 * MEASURE["side_max_km"])[1])
        out["reach_pct"] = round(reach, 1)
    elif mode == "budget" and mech == "circle":
        c, s_ = best_circle(ll, w, spec["radius_km"], n_cand=1000)
        out["best"] = dict(pct=round(100 * s_ / tot, 2), net=None, shape={"center": [round(c[0], 4), round(c[1], 4)], "radius_km": spec["radius_km"]})
    elif mode == "budget":
        pins, pct = best_triangle(ll, w, spec["perimeter_km"])
        out["best"] = dict(pct=round(pct, 2), net=None, shape={"pins": [[round(a, 4), round(b, 4)] for a, b in pins]})
    else:
        lb, wb = scope_points(spec["vs"], None, ids)
        c, s_ = best_circle(np.vstack([ll, lb]), np.r_[w, -wb], spec["radius_km"], n_cand=2000, rank=np.r_[w, np.zeros(len(wb))])
        out["best"] = dict(pct=None, net=round(s_), shape={"center": [round(c[0], 4), round(c[1], 4)], "radius_km": spec["radius_km"]})
    S_ll, S_w = scope_points(spec["cells"], spec["food"], ids, coarse=True)
    P_ll, P_w = scope_points("population", None, ids, coarse=True)
    if P_w.sum() <= 0:  # nobody lives here (Antarctica): the naive player spreads guesses evenly over the stat's cells
        P_ll, P_w = S_ll, np.ones(len(S_w))
    out["naive_median"] = naive_median(spec, P_ll, P_w, S_ll, S_w, out["best"]["pct"], out["best"]["net"])
    out["view"] = auto_view(ll, w)
    p = dict(cells=spec["cells"], food=spec["food"], vs=spec["vs"], stat=spec["stat"], mode=mode, target_pct=spec["target_pct"],
             fact_biggest=True, smallest_km=out["smallest_km"])
    out["fact"] = make_fact(p, ll, w, ids, spec["scope_name"], _PTS["countries_df"])  # polished in gen_record
    return out


def gen_spec(d: dict, code: str, name: str, kind: str, ids, mech: str, mode: str, param: int, ws, measured: dict) -> dict:
    s = dict(key=gen_key(d["key"], code, mech, mode, param), dataset=d["key"], cells=d["cells"], food=d.get("food"),
             vs=d["vs"][0] if d.get("vs") else None, stat=d["stat"], scope_code=code, scope_name=name, scope_kind=kind,
             countries=ids, mechanic=mech, mode=mode, target_pct=None, radius_km=None, perimeter_km=None,
             prompt=make_prompt(d, name, kind, mech, mode, param), checks=dict(measured))
    s[param_name(mech, mode)] = param
    s["fails"] = (["licence"] if not d["licence_ok"] else []) + cheap_fails(
        ws, mode, param if mode == "target" else None, measured["surprise"], d.get("baseline", False))
    if not prompt_ok(s["prompt"]):
        s["fails"].append("prompt")
    return s


def gate_fails(s: dict, m: dict) -> list[str]:
    """Checks on the expensive measurements. Thresholds live in GATE, so tuning them needs no re-measuring."""
    if s["mode"] == "target":
        fails = (["reachable"] if m["reach_pct"] < s["target_pct"] + GATE["reach_margin"] else []) + (
            ["pinpoint"] if m["smallest_km"] < GATE["min_smallest_km"] else [])
    elif s["mode"] == "budget":
        fails = [] if GATE["budget_lo"] <= m["best"]["pct"] <= GATE["budget_hi"] else ["budget_range"]
    else:
        fails = [] if m["best"]["net"] > 0 else ["net_positive"]
    # A typical first guess already scores this much, e.g. a 1,500 km circle anywhere on New Zealand.
    return fails + (["trivial"] if m.get("naive_median", 0) >= GATE["max_naive"] else [])


def gen_record(s: dict, d: dict, m: dict | None, fails: list[str]) -> dict:
    """Candidate record: a Puzzle without an id, plus key, rank, checks and difficulty (PRD §6)."""
    m = m or {}
    best = m.get("best") or {}
    view = m.get("view")
    return dict(
        key=s["key"], dataset=s["dataset"], rank=s["checks"]["surprise"],
        difficulty=difficulty_label(m["naive_median"]) if m else None, inputs_hash=m.get("inputs_hash"),
        checks=dict(**s["checks"], reach_pct=m.get("reach_pct"), smallest_km=m.get("smallest_km"), best_pct=best.get("pct"),
                    best_net=best.get("net"), naive_median=m.get("naive_median"), passed=not fails, failed=fails),
        stat=s["stat"], prompt=s["prompt"], mechanic=s["mechanic"], mode=s["mode"], target_pct=s["target_pct"],
        radius_km=s["radius_km"], perimeter_km=s["perimeter_km"], best=best or None, cells=f"/data/{s['cells']}",
        stat_file=f"/data/stats/{s['food']}.json" if s["food"] else None,
        scope=None if s["scope_kind"] == "world" else {"name": s["scope_name"], "countries": s["countries"]},
        vs={"cells": f"/data/{s['vs']}", "label": d["vs"][1]} if s["vs"] else None,
        view={"center": view[:2], "zoom": view[2]} if view else None, fact=polish_fact(m["fact"], s["stat"]) if m else None,
        credit=d["credit"] + (f"; {POP}" if s["vs"] else ""),
    )


def measure_all(specs: list[dict]) -> dict:
    """gen_measure for every spec, cached by key and a hash of its input files plus GEN_VERSION."""
    path = CACHE / "candidates-measured.json"
    cache = json.loads(path.read_text()) if path.exists() else {}
    file_hash: dict[str, str] = {}

    def fh(rel: str) -> str:
        if rel not in file_hash:
            p = OUT / rel
            file_hash[rel] = hashlib.sha1(p.read_bytes()).hexdigest() if p.exists() else "-"
        return file_hash[rel]

    def inputs_hash(s):
        files = [f"{s['cells']}.bin", "population.bin", "countries.json"] + ([f"stats/{s['food']}.json"] if s["food"] else [])
        return hashlib.sha1("|".join([GEN_VERSION, json.dumps(MEASURE), s["key"], *map(fh, files)]).encode()).hexdigest()[:6]

    for s in specs:
        s["inputs_hash"] = inputs_hash(s)
    todo = sorted({s["key"]: s for s in specs if cache.get(s["key"], {}).get("inputs_hash") != s["inputs_hash"]}.values(),
                  key=lambda s: (s["cells"], s["food"] or "", s["key"]))
    log(f"candidates: measuring {len(todo)} ({len(specs) - len(todo)} cached)")
    if todo:
        with ProcessPoolExecutor(max_workers=os.cpu_count()) as ex:
            for i, (s, m) in enumerate(zip(todo, ex.map(gen_measure, todo, chunksize=4)), 1):
                cache[s["key"]] = {**m, "inputs_hash": s["inputs_hash"]}
                if i % 100 == 0 or i == len(todo):
                    path.write_text(json.dumps(cache))
                    log(f"candidates: measured {i}/{len(todo)}")
    return cache


def step_candidates():
    countries = json.loads((OUT / "countries.json").read_text())
    by_i = {c["i"]: c for c in countries}
    base_scopes = gen_scopes(countries)
    scope_by_code = {s["code"]: s for s in base_scopes}
    vocab, cache = {}, {}
    data = {d["key"]: load_weights(d, vocab, cache) for d in DATASETS}
    ll_rad = np.radians([h3.cell_to_latlng(h3.str_to_int(s)) for s in vocab])  # vocab keeps insertion order
    V = len(vocab)
    pw, pc, pr = data["population"]

    def in_scope(cidx, ids):
        return np.ones(len(cidx), bool) if ids is None else np.isin(cidx, ids)

    def cheap(d, ids):
        w, cidx, r3 = data[d["key"]]
        m = in_scope(cidx, ids) & (w > 0)
        ws = w[m]
        a = np.bincount(r3[m], weights=ws, minlength=V)
        pm = in_scope(pc, ids) & (pw > 0)
        p = np.bincount(pr[pm], weights=pw[pm], minlength=V)
        surprise = tvd(a, p) if p.sum() > 0 and len(ws) else 1.0
        return ws, a, p, dict(cells=len(ws), top_cell_share=round(float(ws.max() / ws.sum()), 4) if len(ws) else 1.0,
                              surprise=round(surprise, 3))

    def covered(d, scope):
        cov = d.get("coverage")
        if cov is None or scope["code"] in cov:
            return True
        return scope["kind"] == "country" and any(scope["ids"][0] in scope_by_code[c]["ids"] for c in cov)

    # Approved puzzles (legacy + curated) block duplicates; rejected keys stay rejected.
    used, approved_n = set(), defaultdict(int)
    curated = json.loads(CURATED.read_text()) if CURATED.exists() else {}
    for k in [legacy_key(p, countries) for p in PUZZLES] + [k for k, v in curated.items() if v["status"] == "approved"]:
        ds, sc, mech, mode, _ = k.split("|")
        used.add((ds, sc, mech, mode))
        approved_n[ds] += 1
    rejected = {k for k, v in curated.items() if v["status"] == "rejected"}

    specs = []
    for d in DATASETS:
        if not d["licence_ok"] or d.get("off"):  # off: Max found the APAD industrial data weird (20 Nov 2026)
            continue
        w, cidx, r3 = data[d["key"]]
        pos = w > 0
        tot = np.bincount(cidx[pos], weights=w[pos])
        cnt = np.bincount(cidx[pos])
        countries_here = [
            dict(code=country_code(by_i[i]), kind="country", ids=[int(i)], name=SCOPE_NAMES.get(by_i[i]["name"], by_i[i]["name"]))
            for i in np.argsort(-tot) if i > 0 and cnt[i] >= GATE["min_cells"] and tot[i] >= GATE["country_min_share"] * w[pos].sum()
        ]
        for scope in base_scopes + countries_here:
            if not covered(d, scope):
                continue
            # "Europe's cassava eaten": a continent that eats almost none of a food just shows where its few eaters live.
            if d.get("food") and scope["kind"] == "continent" and w[pos & np.isin(cidx, scope["ids"])].sum() < GATE["continent_min_share"] * w[pos].sum():
                continue
            ws, a, p, measured = cheap(d, scope["ids"])
            if not len(ws):
                continue
            variants = [(mech, "target", pct) for mech in ("circle", "triangle") for pct in TARGET_PCTS
                        if pct < 50 or scope["kind"] in ("country", "region")]
            fracs = {r: s / ws.sum() for r, s in best_r3_sums(ll_rad, a, RADII).items()}
            r = pick_budget_radius(fracs)
            if d["key"] != "land":  # land is uniform: "as much land as you can" just means "keep the shape on land"
                variants += [("circle", "budget", r), ("triangle", "budget", nearest_perimeter(r))]
            if d.get("vs"):
                nets = best_r3_sums(ll_rad, a - p, RADII, rank=a)  # v1: vs is always population
                variants.append(("circle", "net", max(RADII, key=lambda x: (nets[x], -x))))
            for mech, mode, param in variants:
                s = gen_spec(d, scope["code"], scope["name"], scope["kind"], scope["ids"], mech, mode, param, ws, measured)
                if (d["key"], scope["code"], mech, mode) in used:
                    s["fails"].append("duplicate")
                if s["key"] in rejected:
                    s["fails"].append("rejected")
                specs.append(s)

    # The 40 handwritten puzzles go through the same checks, for calibration (PRD §5.5).
    legacy = []
    for pid, p in enumerate(PUZZLES, start=1):
        d = next(d for d in DATASETS if d["cells"] == p["cells"] and d.get("food") == p.get("food"))
        ids, name, kind = legacy_scope(p.get("scope"), countries)
        ws, _, _, measured = cheap(d, ids)
        s = gen_spec(d, legacy_key(p, countries).split("|")[1], name, kind, ids, p["mechanic"], p["mode"],
                     p.get(param_name(p["mechanic"], p["mode"])), ws, measured)
        s.update(id=pid, prompt=p["prompt"], fails=[f for f in s["fails"] if f != "prompt"])
        legacy.append(s)

    survivors = [s for s in specs if not s["fails"]]
    log(f"candidates: {len(specs):,} raw, {len(survivors):,} pass the cheap checks")
    measured = measure_all(survivors + legacy)

    out = []
    for s in specs:
        m = measured[s["key"]] if not s["fails"] else None
        out.append(gen_record(s, next(d for d in DATASETS if d["key"] == s["dataset"]), m, s["fails"] + (gate_fails(s, m) if m else [])))
    out.sort(key=lambda c: (not c["checks"]["passed"], -c["rank"], approved_n[c["dataset"]], c["key"]))
    write_json(GEN_OUT / "candidates.json", out)

    rows = []
    for s in legacy:
        m = measured[s["key"]]
        fails = s["fails"] + gate_fails(s, m)
        rows.append(dict(id=s["id"], key=s["key"], prompt=s["prompt"], passed=not fails, failed=" ".join(fails),
                         surprise=s["checks"]["surprise"], top_cell_share=s["checks"]["top_cell_share"], cells=s["checks"]["cells"],
                         reach_pct=m["reach_pct"], smallest_km=m["smallest_km"], best_pct=m["best"]["pct"], best_net=m["best"]["net"],
                         naive_median=m["naive_median"], difficulty=difficulty_label(m["naive_median"]),
                         max_quality="", max_difficulty=""))
    pd.DataFrame(rows).to_csv(GEN_OUT / "calibration.csv", index=False)
    log(f"wrote {(GEN_OUT / 'calibration.csv').relative_to(ROOT)}: fill in max_quality (good/boring/too hard/broken) and max_difficulty")

    passed = [c for c in out if c["checks"]["passed"]]
    log(f"candidates: {len(passed):,} pass all checks; legacy: {sum(r['passed'] for r in rows)}/{len(rows)} pass")
    fail_counts = pd.Series([f for c in out for f in c["checks"]["failed"]]).value_counts()
    log("candidates: failures " + ", ".join(f"{k} {v}" for k, v in sorted(fail_counts.items())))
    log("candidates: difficulty " + ", ".join(f"{k} {v}" for k, v in pd.Series([c["difficulty"] for c in passed]).value_counts().items()))
    log("candidates: passing per dataset " + ", ".join(f"{k} {v}" for k, v in pd.Series([c["dataset"] for c in passed]).value_counts().items()))
    for c in passed[:15]:
        log(f"  {c['rank']:.2f} {c['difficulty']:<6} {c['prompt']}")


def step_selftest():
    """Assert-based checks for the generator (PRD §5.11, Python part)."""
    p = np.array([5.0, 3, 0, 2])
    assert tvd(p, p) == 0
    assert tvd(p, p * 7) < 1e-12  # scale-free
    assert abs(tvd(np.array([1.0, 0]), np.array([0.0, 1])) - 1) < 1e-12
    flat = np.ones(400)
    assert cheap_fails(flat, "target", 10, 0.5, False) == []
    assert cheap_fails(flat[:100], "target", 10, 0.5, False) == ["cells"]
    spike = np.r_[flat, 30.0]  # one cell holds ~7%: fine for budget, too much for a 10% target
    assert cheap_fails(spike, "budget", None, 0.5, False) == []
    assert cheap_fails(spike, "target", 10, 0.5, False) == ["hotspot"]
    assert cheap_fails(flat, "target", 10, 0.1, False) == ["surprise"]
    assert cheap_fails(flat, "target", 10, 0.1, True) == []
    # Toy r3 circles: two heavy points 50 km apart and one far away.
    ll = np.radians([[0, 0], [0, 0.45], [40, 40]])
    s = best_r3_sums(ll, np.array([1.0, 1, 5]), [10, 100])
    assert s == {10: 5.0, 100: 5.0}
    s = best_r3_sums(ll, np.array([3.0, 3, 5]), [10, 100])
    assert s == {10: 5.0, 100: 6.0}
    assert best_r3_sums(ll, np.array([3.0, -4, 1]), [100], rank=np.array([3.0, 0, 1])) == {100: 1.0}
    assert pick_budget_radius({100: 0.1, 200: 0.3, 300: 0.4}) == 200  # tie 0.05 vs 0.05 goes to the smaller radius
    assert nearest_perimeter(500) == 4000 and nearest_perimeter(2000) == 10000

    # Expensive gates on toy measurements.
    t = dict(mode="target", target_pct=25)
    assert gate_fails(t, dict(reach_pct=40, smallest_km=300)) == []
    assert gate_fails(t, dict(reach_pct=30, smallest_km=10)) == ["reachable", "pinpoint"]
    assert gate_fails(dict(mode="budget"), dict(best=dict(pct=70))) == ["budget_range"]
    assert gate_fails(dict(mode="net"), dict(best=dict(net=-5))) == ["net_positive"]
    assert gate_fails(dict(mode="net"), dict(best=dict(net=5), naive_median=100)) == ["trivial"]

    # Start views: worldwide data opens on Europe and Africa, date-line data (Fiji) stays on the date line.
    world = np.array([[40.0, -100.0], [20.0, 0.0], [25.0, 80.0], [30.0, 120.0], [-30.0, 150.0], [-15.0, -60.0]])
    assert auto_view(world, np.ones(len(world))) == [10.0, 25.0, 1.2]
    fiji = np.array([[-17.0, 178.0], [-17.5, 179.5], [-16.5, -179.5], [-18.0, -178.5]])
    assert abs(abs(auto_view(fiji, np.ones(4))[0]) - 180) < 3

    # Scores match lib/score.ts, including JS rounding of .5 upwards.
    assert score_target(25, 25) == 100 and score_target(50, 25) == 0 and score_target(0, 25) == 0 and score_target(30, 20) == 50
    assert score_target(12.5, 20) == 63  # 62.5 rounds up like Math.round
    assert score_budget(30, 40) == 75 and score_budget(50, 40) == 100 and score_budget(10, 0) == 0
    assert score_net(-10, 100) == 0 and score_net(50, 100) == 50 and score_net(500, 100) == 100
    assert [difficulty_label(x) for x in (80, 55, 54, 30, 29)] == ["easy", "easy", "medium", "medium", "hard"]
    lat, lng = destination(0.0, 0.0, math.pi / 2, math.pi * EARTH_R / 180)  # 1 degree east along the equator
    assert abs(lat) < 1e-9 and abs(lng - 1) < 1e-9
    lat, lng = destination(0.0, 179.5, math.pi / 2, math.pi * EARTH_R / 180)
    assert abs(lng + 179.5) < 1e-9, lng  # wraps across the date line

    # Naive player on a toy world: the stat sits exactly where people live, so a target guess sized on people scores well.
    rng = np.random.default_rng(0)
    P_ll = np.c_[rng.uniform(-30, 30, 500), rng.uniform(-30, 30, 500)]
    P_w = rng.uniform(1, 10, 500)
    spec = dict(key="toy", mechanic="circle", mode="target", target_pct=25, radius_km=None, perimeter_km=None)
    easy = naive_median(spec, P_ll, P_w, P_ll, P_w, None, None)
    far = naive_median(spec, P_ll, P_w, P_ll + [0, 90], P_w, None, None)  # same stat moved a quarter of the world away
    # Radius noise (sigma 0.4) alone caps this near 50: a people-shaped target puzzle lands at the Easy/Medium border.
    assert easy >= 45 and far == 0, (easy, far)
    assert naive_median({**spec, "mechanic": "triangle"}, P_ll, P_w, P_ll, P_w, None, None) >= 30
    assert naive_median(spec, P_ll, P_w, P_ll, P_w, None, None) == easy  # deterministic per key

    # Prompts: every mechanic x mode x scope type, count and mass nouns, "half", the length limit.
    ds = {d["key"]: d for d in DATASETS}
    assert make_prompt(ds["coffee"], "Brazil", "country", "triangle", "target", 50) == PUZZLES[26]["prompt"]
    assert make_prompt(ds["beer"], "Germany", "country", "circle", "target", 50) == PUZZLES[25]["prompt"]
    assert make_prompt(ds["tea"], "the world", "world", "triangle", "budget", 5000) == PUZZLES[23]["prompt"]
    assert make_prompt(ds["earthquakes"], "Japan", "country", "circle", "budget", 300) == PUZZLES[15]["prompt"]
    assert make_prompt(ds["wine"], "Europe", "continent", "triangle", "target", 30) == PUZZLES[16]["prompt"]
    assert make_prompt(ds["land"], "Canada", "country", "triangle", "target", 50) == PUZZLES[18]["prompt"]
    assert make_prompt(ds["brick-kilns"], "Bangladesh", "country", "triangle", "target", 50) == "Put down 3 pins. Your triangle must hold exactly half of Bangladesh's mapped brick kilns."
    assert make_prompt(ds["mcdonalds"], "the world", "world", "circle", "target", 10) == PUZZLES[9]["prompt"]
    assert make_prompt(ds["land"], "the world", "world", "circle", "budget", 1500) == \
        "You get a 1,500 km circle. Catch as much of the world's land as you can."
    assert make_prompt(ds["goats"], "Africa", "continent", "circle", "net", 750) == \
        "Goats vs people in Africa. Your 750 km circle should catch the most goats beyond the number of people."
    assert make_prompt(ds["cattle"], "the world", "world", "circle", "net", 1000).startswith("Cattle vs people. Your 1,000 km")
    assert make_prompt(ds["rice"], "the Philippines", "country", "circle", "target", 20) == \
        "Draw a circle that holds exactly 20% of the Philippines' rice eaten."
    assert make_prompt(ds["igp-coal"], "the Indo-Gangetic Plain", "region", "circle", "budget", 250).startswith(
        "You get a 250 km circle. Catch as much of the Indo-Gangetic Plain's coal power capacity")
    f = "the USA alone accounts for 12% of coffee. Brazil and Philippines follow, then the Philippines and United Kingdom."
    assert polish_fact(f, "coffee drunk") == \
        "The USA alone accounts for 12% of coffee. Brazil and the Philippines follow, then the Philippines and the UK."
    assert polish_fact(polish_fact(f, "x"), "x") == polish_fact(f, "x")
    assert polish_fact("The smallest circle we found holding half", "land") == "The smallest circle we found covering half"
    assert prompt_ok("Draw a circle.") and not prompt_ok("Draw  a circle.") and not prompt_ok("Draw the the circle.")
    assert not prompt_ok("x" * 140 + ".") and not prompt_ok("No full stop")
    for d in DATASETS:
        for name, kind in (("the world", "world"), ("South America", "continent"), ("the Indo-Gangetic Plain", "region"), ("the USA", "country")):
            for mech, mode, param in (("circle", "target", 50), ("triangle", "target", 25), ("circle", "budget", 1500),
                                      ("triangle", "budget", 10000), ("circle", "net", 2000)):
                s = make_prompt(d, name, kind, mech, mode, param)
                assert s.endswith(".") and "  " not in s and "the the" not in s.lower(), s

    # Keys: deterministic, and every legacy puzzle maps to a unique one.
    countries = json.loads((OUT / "countries.json").read_text())
    keys = [legacy_key(p, countries) for p in PUZZLES]
    assert keys == [legacy_key(p, countries) for p in PUZZLES]
    assert len(set(keys)) == len(keys), "two legacy puzzles share a key"
    assert keys[26] == "coffee|BRA|triangle|target|50", keys[26]
    assert keys[13] == "sheep|AUS+NZL|circle|net|500", keys[13]
    assert keys[30] == "chickens|USA|circle|target|50", keys[30]
    assert keys[16] == "wine|Europe|triangle|target|30", keys[16]
    assert legacy_scope(PUZZLES[13]["scope"], countries)[1:] == ("Australia and New Zealand", "region")
    assert legacy_scope(PUZZLES[14]["scope"], countries)[1:] == ("the USA", "country")
    # publish: legacy kept, approved appended by ID with the reviewed text, rejected dropped
    legacy = [{"id": i, "prompt": "L"} for i in range(1, len(PUZZLES) + 1)]
    snap = {"key": "k", "prompt": "Gen.", "fact": "Gen fact.", "difficulty": "easy"}
    cur = {"b": {"status": "approved", "id": 60, "fact": "Mine.", "prompt": None, "puzzle": {**snap, "key": "b"}},
           "a": {"status": "approved", "id": 59, "fact": "F.", "prompt": "Edited.", "puzzle": {**snap, "key": "a"}},
           "r": {"status": "rejected", "reason": "boring"}}
    m = merge_curated(legacy, cur)
    assert [p["id"] for p in m[-2:]] == [59, 60] and len(m) == len(PUZZLES) + 2
    assert m[-2]["prompt"] == "Edited." and m[-1]["prompt"] == "Gen." and m[-1]["fact"] == "Mine." and m[-1]["key"] == "b"
    for bad in ({"x": {**cur["a"], "id": 3}}, {"x": cur["a"], "y": {**cur["b"], "id": 59}}):
        try:
            merge_curated(legacy, bad)
            raise AssertionError("bad curated ids accepted")
        except AssertionError as e:
            assert "accepted" not in str(e)

    # fun facts: only approved ones, rotated by ID, replaced on every publish
    ff = {"coffee": [{"text": "A.", "source": "u", "approved": True}, {"text": "No.", "source": "u", "approved": False},
                     {"text": "B.", "source": "v", "approved": True}]}
    ps = attach_funfacts([{"id": 4, "cells": "/data/population", "stat_file": "/data/stats/coffee.json", "funfact": "old"},
                          {"id": 5, "cells": "/data/population", "stat_file": "/data/stats/coffee.json"},
                          {"id": 6, "cells": "/data/land", "stat_file": None, "funfact": "old"}], ff)
    assert ps[0]["funfact"] == {"text": "A.", "source": "u"} and ps[1]["funfact"]["text"] == "B." and "funfact" not in ps[2]

    # schedule (PRD §5.11): rules on a fake pool of 10 datasets x 3 levels x 2 mechanics x 2 modes
    legacy = [{"id": i, "cells": f"/data/legacy{i % 5}", "stat_file": None, "mechanic": "circle", "mode": "target"} for i in range(1, len(PUZZLES) + 1)]
    pool, pid = [], len(PUZZLES) + 1
    for ds in range(10):
        for lv in LEVELS:
            for mech in ("circle", "triangle"):
                for mode in ("target", "budget"):
                    pool.append({"id": pid, "cells": f"/data/d{ds}", "stat_file": None, "difficulty": lv, "mechanic": mech, "mode": mode})
                    pid += 1
    first = dt.date(2026, 12, 28)
    fixed_swap = {"2026-11-20": 4, "2026-11-21": 3, "2027-01-05": pool[-1]["id"]}  # hand edits the script must keep
    sched = fill_schedule(fixed_swap, legacy + pool, first, ahead=59)
    launch = dt.date.fromisoformat(LAUNCH_DAY)
    assert sched["2026-11-20"] == 4 and sched["2026-11-21"] == 3 and sched["2027-01-05"] == pool[-1]["id"]
    assert all(sched[(launch + dt.timedelta(i)).isoformat()] == i + 1 for i in range(len(PUZZLES)) if i not in (2, 3))
    assert days_ahead(sched, first) == 60 and days_ahead(sched, first + dt.timedelta(60)) == 0
    vals = list(sched.values())
    assert len(vals) == len(set(vals)), "each puzzle at most once"
    byid = {q["id"]: q for q in legacy + pool}
    days = [first + dt.timedelta(k) for k in range(60)]
    seq = [byid[sched[d.isoformat()]] for d in days]
    for k, (d, q) in enumerate(zip(days, seq)):
        if d.isoformat() != "2027-01-05":
            assert q["difficulty"] == WEEKDAY_LEVEL[d.weekday()], (d, q)
        assert all(puzzle_dataset(q) != puzzle_dataset(o) for o in seq[max(0, k - 6):k]), f"dataset repeat {d}"
        assert k < 2 or not (q["mechanic"] == seq[k - 1]["mechanic"] == seq[k - 2]["mechanic"]), f"mechanic x3 {d}"
        assert k < 6 or any(o["mode"] != "target" for o in seq[k - 6:k + 1]), f"no budget/net in 7 days {d}"
    # nearest level when the wanted one runs out
    only_hard = [q for q in pool if q["difficulty"] == "hard"][:3]
    s2 = fill_schedule({}, legacy + only_hard, first, ahead=0)  # Monday wants easy
    assert byid[s2[first.isoformat()]]["difficulty"] == "hard"

    log("selftest: ok")


STEPS = {
    "land": step_land,
    "geo": step_geo,
    "population": step_population,
    "stats": step_stats,
    "points": step_points,
    "livestock": step_livestock,
    "landcells": step_landcells,
    "bycountry": step_bycountry,
    "apad": step_apad,
    "puzzles": step_puzzles,
    "publish": step_publish,
    "schedule": step_schedule,
    "countries": step_countries,
    "candidates": step_candidates,
    "selftest": step_selftest,
}

if __name__ == "__main__":
    CACHE.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    for s in sys.argv[1:] or list(STEPS):
        log(f"=== {s}")
        STEPS[s]()
