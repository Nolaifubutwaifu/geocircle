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
Steps: land, geo, population, stats, points, livestock, landcells, bycountry, puzzles, countries
(default: all, in that order).

Formats (see lib/cells.ts and lib/land.ts for the readers):
  fine-cell file  <name>.bin           16-byte records: u32 lo, u32 hi, f32 value, u16 country, u16 0
                  <name>.index.json    {"res": r, "groups": {r3: [offset, count, total, maxR_km]}}
                  <name>.bycountry.json {country_index: {r3: total}} for puzzles limited to some countries
  land bitmaps    land-r{5,6,7}.bin    one bit per child (sorted ids) of each parent cell
                  land-r{5,6,7}.json   {"res", "parentRes", "groups": {parent: [byteOffset, nBytes]}, "full": [parent...]}
"""
from __future__ import annotations

import gzip
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

def points_to_cells(name: str, lats: np.ndarray, lngs: np.ndarray, max_deg: float | None = None, weights: np.ndarray | None = None):
    r7 = np.array([h3.latlng_to_cell(float(a), float(b), 7) for a, b in zip(lats, lngs)], dtype=np.uint64)
    counts = pd.Series(1.0 if weights is None else np.asarray(weights, dtype=float), index=r7).groupby(level=0).sum()
    cells = counts.index.to_numpy(dtype=np.uint64)
    latlng = np.array([h3.cell_to_latlng(int(c)) for c in cells])
    countries = load_countries()
    cidx = assign_country(latlng[:, 0], latlng[:, 1], countries, max_deg)
    write_cells(f"points/{name}", cells, counts.to_numpy(), cidx, 7)
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


# ---------------------------------------------------------------- livestock (GLW4 2015, CC0)

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
    if wq(lngs, 0.95) - wq(lngs, 0.05) > 180:  # straddles the date line (Russia, Fiji, NZ islands)
        lngs = np.where(lngs < 0, lngs + 360, lngs)
    lng0, lng1 = wq(lngs, 0.05), wq(lngs, 0.95)
    span = max(lat1 - lat0, (lng1 - lng0) * np.cos(np.radians((lat0 + lat1) / 2)), 0.5)
    zoom = float(np.clip(np.log2(300 / span) - 0.2, 1.2, 8))
    lng = (lng0 + lng1) / 2
    return [round(float(((lng + 180) % 360) - 180), 3), round(float((lat0 + lat1) / 2), 3), round(zoom, 2)]


# ---------------------------------------------------------------- puzzles

POP = "Kontur Population (CC BY 4.0)"
FOOD_CREDIT = "FAO FAOSTAT Food Balances (CC BY 4.0); Kontur Population (CC BY 4.0)"
GLW = "FAO Gridded Livestock of the World 2015 (CC0)"
LAND = "Natural Earth (public domain)"
QUAKES = "USGS Earthquake Catalog (public domain)"
MCD = "Overture Maps Places (CDLA Permissive 2.0)"

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
    dict(stat="brick kilns", cells="points/brick-kilns", scope=IGP, mechanic="circle", mode="target", target_pct=25,
         prompt="Draw a circle that holds exactly 25% of the brick kilns mapped by satellite across India, Pakistan and Bangladesh.",
         credit=APAD_BY),
    dict(stat="brick kilns", cells="points/brick-kilns", scope=IGP, mechanic="circle", mode="budget", radius_km=150,
         prompt="You get a 150 km circle. Catch as many of South Asia's mapped brick kilns as you can.", credit=APAD_BY),
    dict(stat="brick kilns", cells="points/brick-kilns", scope=["Bangladesh"], mechanic="triangle", mode="target", target_pct=50,
         prompt="Put down 3 pins. Your triangle must hold exactly half of Bangladesh's mapped brick kilns.", credit=APAD_BY),
    dict(stat="brick kilns", cells="points/brick-kilns", scope=["Pakistan"], mechanic="circle", mode="target", target_pct=50,
         prompt="Draw a circle that holds exactly half of Pakistan's mapped brick kilns.", credit=APAD_BY),
    dict(stat="coal power capacity", cells="points/igp-coal", scope=IGP, mechanic="circle", mode="target", target_pct=30,
         prompt="Draw a circle that holds exactly 30% of the coal power capacity on the Indo-Gangetic Plain.", fact_biggest=True,
         credit=APAD_BY),
    dict(stat="cement plants", cells="points/igp-cement", scope=IGP, mechanic="triangle", mode="target", target_pct=25,
         prompt="Put down 3 pins. Your triangle must hold exactly 25% of the Indo-Gangetic Plain's cement plants.", credit=APAD_ODC),
    dict(stat="paper mills", cells="points/igp-paper", scope=IGP, mechanic="circle", mode="budget", radius_km=250,
         prompt="You get a 250 km circle. Catch as many of the Indo-Gangetic Plain's paper mills as you can.", credit=APAD_ODC),
    dict(stat="plastic waste and dump sites", cells="points/igp-waste", scope=IGP, mechanic="circle", mode="target", target_pct=30,
         prompt="Draw a circle that holds exactly 30% of the plastic waste and dump sites mapped on the Indo-Gangetic Plain.",
         credit=APAD_ODC),
    dict(stat="mapped gas and oil power capacity", cells="points/africa-power", scope=AFRICA_APAD, mechanic="triangle", mode="budget",
         perimeter_km=3000, prompt="Your triangle can be at most 3,000 km around. Catch as much of the mapped gas and oil power capacity in Africa as you can.",
         fact_biggest=True, credit=APAD_BY),
    dict(stat="waste tonnage at mapped dump sites", cells="points/africa-waste", scope=AFRICA_APAD, mechanic="circle", mode="target", target_pct=50,
         prompt="Draw a circle that holds exactly half of the waste tonnage at Africa's mapped dump sites.", fact_biggest=True,
         credit=APAD_ODC),
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
        (r1, n1), (r2, n2) = ratios[0], ratios[1]
        return f"{n1} has {r1:.1f} {p['stat']} per person. {n2} has {r2:.1f}."
    if p["mode"] == "target" and scope_ids is not None:
        km = smallest_circle_km(latlng, w, p["target_pct"] / 100)
        return f"The smallest circle we found holding {p['target_pct']}% of {scope_label}'s {p['stat']} has a radius of {fmt_count(km)} km."
    shares = country_shares(p["cells"], p.get("food"), scope_ids, countries)
    if scope_ids is not None:
        n, _, v = shares[0]
        return f"{n} recorded {fmt_count(v)} {p['stat']} since 2000." if p["cells"] == "points/earthquakes" else f"{n} has {fmt_count(v)} {p['stat']}."
    if p["cells"] == "population" and not p.get("food"):
        return "Half of all people live in a circle about 4,000 km wide over Asia."
    (n1, s1, _), (n2, s2, _), (n3, s3, _) = shares[:3]
    what = "the world's land" if p["stat"] == "land" else f"the world's {p['stat']}"
    return f"{n1} alone accounts for {pct_text(s1)} of {what}. {n2} and {n3} follow with {pct_text(s2)} and {pct_text(s3)}."


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
    fact = make_fact(p, latlng, w, scope_ids, scope_label, countries).replace("United States of America", "the USA")
    fact = re.sub(r"(^|\. )([a-z])", lambda m: m.group(1) + m.group(2).upper(), fact)  # "the USA" opening a sentence
    if p["stat"] == "land":
        fact = fact.replace("holding", "covering")
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


def step_puzzles():
    countries = load_countries()
    out = [build_puzzle(i, p, countries) for i, p in enumerate(PUZZLES, start=1) if (OUT / f"{p['cells']}.index.json").exists()]
    assert len(out) == len(PUZZLES), "some puzzle data is missing"
    write_json(OUT / "puzzles.json", out)


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
    "countries": step_countries,
}

if __name__ == "__main__":
    CACHE.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    for s in sys.argv[1:] or list(STEPS):
        log(f"=== {s}")
        STEPS[s]()
