// Data cells and scoring: coarse r3 totals, refined to fine cells only where the shape edge crosses.
import { cellToBoundary, cellToLatLng, cellToParent, getResolution, h3IndexToSplitLong, splitLongToH3Index } from "h3-js";
import { contains, distToArcKm, distToShapeKm, haversineKm, type LngLat, type Shape } from "./geo";
import { fetchRanges } from "./range";

export const RECORD_BYTES = 16; // u32 lo, u32 hi, f32 value, u16 country, u16 reserved

/** One r3 group in a fine-cell file. maxR = farthest child centre from the r3 centre (km). */
export type Group = { offset: number; count: number; total: number; maxR: number };
export type Fine = { lo: number; hi: number; value: number; country: number };

export type CellIndex = { res: number; groups: Record<string, [number, number, number, number]> };
export type StatFile = { kg: number[]; r3: Record<string, number> };

export type Dataset = {
  binUrl: string;
  res: number;
  groups: Map<string, Group>;
  /** Weighted total per r3 cell, limited to the scope's countries when there is one. */
  r3Totals: Map<string, number>;
  total: number;
  kg: number[] | null;
  /** Only cells in these countries count. Null = whole world. */
  countries: Set<number> | null;
  cache: Map<string, Fine[]>;
};

/** Per-country r3 totals of the raw values: { countryIndex: { r3: total } }. */
export type ByCountry = Record<string, Record<string, number>>;

export function makeDataset(
  binUrl: string,
  index: CellIndex,
  stat: StatFile | null,
  scope?: { countries: number[]; byCountry: ByCountry } | null,
): Dataset {
  const groups = new Map<string, Group>();
  for (const [k, [offset, count, total, maxR]] of Object.entries(index.groups)) groups.set(k, { offset, count, total, maxR });
  const r3Totals = new Map<string, number>();
  if (scope) {
    for (const c of scope.countries) {
      const mult = stat ? (stat.kg[c] ?? 0) : 1;
      for (const [k, v] of Object.entries(scope.byCountry[c] ?? {})) r3Totals.set(k, (r3Totals.get(k) ?? 0) + v * mult);
    }
  } else if (stat) for (const [k, v] of Object.entries(stat.r3)) r3Totals.set(k, v);
  else for (const [k, g] of groups) r3Totals.set(k, g.total);
  let total = 0;
  for (const v of r3Totals.values()) total += v;
  return { binUrl, res: index.res, groups, r3Totals, total, kg: stat?.kg ?? null, countries: scope ? new Set(scope.countries) : null, cache: new Map() };
}

export async function loadDataset(base: string, stat: string | null, scopeCountries?: number[] | null): Promise<Dataset> {
  const json = <T,>(url: string) =>
    fetch(url).then((r) => {
      if (!r.ok) throw new Error(`Couldn't load ${url} (${r.status})`);
      return r.json() as Promise<T>;
    });
  const [index, statFile, byCountry] = await Promise.all([
    json<CellIndex>(`${base}.index.json`),
    stat ? json<StatFile>(stat) : Promise.resolve(null),
    scopeCountries ? json<ByCountry>(`${base}.bycountry.json`) : Promise.resolve(null),
  ]);
  return makeDataset(`${base}.bin`, index, statFile, scopeCountries && byCountry ? { countries: scopeCountries, byCountry } : null);
}

export function weightOf(ds: Pick<Dataset, "kg" | "countries">, f: Fine): number {
  if (ds.countries && !ds.countries.has(f.country)) return 0;
  return ds.kg ? f.value * (ds.kg[f.country] ?? 0) : f.value;
}

export function parseRecords(buf: ArrayBuffer, byteOffset = 0, count = buf.byteLength / RECORD_BYTES): Fine[] {
  const dv = new DataView(buf, byteOffset, count * RECORD_BYTES);
  const out: Fine[] = new Array(count);
  for (let i = 0; i < count; i++) {
    const o = i * RECORD_BYTES;
    out[i] = {
      lo: dv.getUint32(o, true),
      hi: dv.getUint32(o + 4, true),
      value: dv.getFloat32(o + 8, true),
      country: dv.getUint16(o + 12, true),
    };
  }
  return out;
}

export const fineId = (f: Fine) => splitLongToH3Index(f.lo, f.hi);
export const fineLngLat = (f: Fine): LngLat => {
  const [lat, lng] = cellToLatLng(splitLongToH3Index(f.lo, f.hi));
  return [lng, lat];
};

export function encodeRecords(cells: { id: string; value: number; country?: number }[]): ArrayBuffer {
  const buf = new ArrayBuffer(cells.length * RECORD_BYTES);
  const dv = new DataView(buf);
  cells.forEach((c, i) => {
    const [lo, hi] = h3IndexToSplitLong(c.id);
    dv.setUint32(i * RECORD_BYTES, lo, true);
    dv.setUint32(i * RECORD_BYTES + 4, hi, true);
    dv.setFloat32(i * RECORD_BYTES + 8, c.value, true);
    dv.setUint16(i * RECORD_BYTES + 12, c.country ?? 0, true);
  });
  return buf;
}

/** Fetch the fine cells of the given r3 groups (byte ranges, merged when close). */
export async function fetchGroups(ds: Dataset, keys: string[]): Promise<void> {
  const todo = keys.filter((k) => !ds.cache.has(k) && ds.groups.has(k)).map((k) => [k, ds.groups.get(k)!] as const);
  if (!todo.length) return;
  const read = await fetchRanges(
    ds.binUrl,
    todo.map(([, g]) => [g.offset * RECORD_BYTES, (g.offset + g.count) * RECORD_BYTES]),
  );
  for (const [k, g] of todo) {
    const bytes = read(g.offset * RECORD_BYTES, (g.offset + g.count) * RECORD_BYTES);
    ds.cache.set(k, parseRecords(bytes.buffer as ArrayBuffer, bytes.byteOffset, g.count));
  }
}

const r3Geo = new Map<string, LngLat>();
function r3Center(k: string): LngLat {
  let c = r3Geo.get(k);
  if (!c) {
    const [lat, lng] = cellToLatLng(k);
    c = [lng, lat];
    r3Geo.set(k, c);
  }
  return c;
}

/** Distance from p to the shape's boundary when p is inside, used for the "fully inside" test. */
function insideMarginKm(shape: Shape, p: LngLat): number {
  if (shape.kind === "circle") return shape.radiusKm - haversineKm(shape.center, p);
  if (!contains(shape, p)) return -1;
  const [a, b, c] = shape.pins;
  return Math.min(distToArcKm(p, a, b), distToArcKm(p, b, c), distToArcKm(p, c, a));
}

export type Classified = { inside: string[]; edge: string[] };

/** Exact with respect to fine-cell centres: every child centre lies within maxR of the r3 centre. */
export function classifyGroups(shape: Shape, ds: Pick<Dataset, "groups" | "r3Totals">): Classified {
  const inside: string[] = [];
  const edge: string[] = [];
  for (const k of ds.r3Totals.keys()) {
    const g = ds.groups.get(k);
    const maxR = (g?.maxR ?? 0) + 0.01;
    const c = r3Center(k);
    if (distToShapeKm(shape, c) > maxR) continue;
    if (insideMarginKm(shape, c) >= maxR) inside.push(k);
    else edge.push(k);
  }
  return { inside, edge };
}

/** Weighted sum of everything inside the shape (same units as the data). */
export async function capturedSum(shape: Shape, ds: Dataset): Promise<number> {
  const { inside, edge } = classifyGroups(shape, ds);
  await fetchGroups(ds, edge);
  let sum = 0;
  for (const k of inside) sum += ds.r3Totals.get(k) ?? 0;
  for (const k of edge) for (const f of ds.cache.get(k) ?? []) if (contains(shape, fineLngLat(f))) sum += weightOf(ds, f);
  return sum;
}

/** Caught share in percent (0 to 100). */
export async function capturedPct(shape: Shape, ds: Dataset): Promise<number> {
  const sum = await capturedSum(shape, ds);
  return ds.total > 0 ? (100 * sum) / ds.total : 0;
}

/** Weighted cells at a display resolution, for the reveal heatmap. Coarser than the data res sums up children. */
export function aggregateTo(ds: Dataset, res: number, r3Keys: Iterable<string>): Map<string, number> {
  const out = new Map<string, number>();
  for (const k of r3Keys) {
    if (res <= 3) {
      const id = res === 3 ? k : cellToParent(k, res);
      out.set(id, (out.get(id) ?? 0) + (ds.r3Totals.get(k) ?? 0));
      continue;
    }
    for (const f of ds.cache.get(k) ?? []) {
      const id = fineId(f);
      const pid = getResolution(id) > res ? cellToParent(id, res) : id;
      out.set(pid, (out.get(pid) ?? 0) + weightOf(ds, f));
    }
  }
  return out;
}

/** Hex outline as a closed GeoJSON ring in [lng, lat], unwrapped across the date line. */
export function hexRing(id: string): LngLat[] {
  const b = cellToBoundary(id, true) as LngLat[];
  const ref = b[0][0];
  return b.map(([lng, lat]) => {
    let l = lng;
    if (l - ref > 180) l -= 360;
    else if (l - ref < -180) l += 360;
    return [l, lat];
  });
}
