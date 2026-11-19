// Land hexes for the map: static lists when zoomed out (r2 to r4), bitmaps per parent cell when zoomed in (r5 to r7).
import { cellToChildren, cellToLatLng, cellToParent, polygonToCells } from "h3-js";
import { fetchRanges } from "./range";

type BitmapIndex = { res: number; parentRes: number; groups: Record<string, [number, number]>; full: string[] };

const staticLists = new Map<number, Promise<{ id: string; lng: number; lat: number }[]>>();
const indexes = new Map<number, Promise<BitmapIndex & { fullSet: Set<string> }>>();
const decoded = new Map<string, Set<string>>(); // parent id -> land children at that index's res

/** Zoom to hex resolution: one step finer than ~10 px hexes, which read as chunky blocks. */
export function resForZoom(zoom: number): number {
  if (zoom < 2) return 3;
  if (zoom < 3.5) return 4;
  if (zoom < 5) return 5;
  if (zoom < 6.5) return 6;
  return 7;
}

function loadStatic(res: number) {
  let p = staticLists.get(res);
  if (!p) {
    p = fetch(`/data/land-r${res}.json`)
      .then((r) => r.json() as Promise<string[]>)
      .then((ids) =>
        ids.map((id) => {
          const [lat, lng] = cellToLatLng(id);
          return { id, lng, lat };
        }),
      );
    staticLists.set(res, p);
  }
  return p;
}

function loadIndex(res: number) {
  let p = indexes.get(res);
  if (!p) {
    p = fetch(`/data/land-r${res}.json`)
      .then((r) => r.json() as Promise<BitmapIndex>)
      .then((ix) => ({ ...ix, fullSet: new Set(ix.full) }));
    indexes.set(res, p);
  }
  return p;
}

const sortedChildren = (parent: string, res: number) => cellToChildren(parent, res).sort();

async function ensureDecoded(res: number, parents: string[]) {
  const ix = await loadIndex(res);
  const todo = parents.filter((p) => !decoded.has(`${res}:${p}`) && ix.groups[p]);
  if (!todo.length) return ix;
  const read = await fetchRanges(
    `/data/land-r${res}.bin`,
    todo.map((p) => [ix.groups[p][0], ix.groups[p][0] + ix.groups[p][1]]),
  );
  for (const p of todo) {
    const [o, n] = ix.groups[p];
    const bytes = read(o, o + n);
    const kids = sortedChildren(p, res);
    const land = new Set<string>();
    for (let i = 0; i < kids.length; i++) if (bytes[i >> 3] & (1 << (i & 7))) land.add(kids[i]);
    decoded.set(`${res}:${p}`, land);
  }
  return ix;
}

export type Bounds = { west: number; south: number; east: number; north: number };

/** Viewport split into boxes inside [-180, 180]. */
function boxes(b: Bounds): [number, number, number, number][] {
  const south = Math.max(-85, b.south);
  const north = Math.min(85, b.north);
  if (b.east - b.west >= 360) return [[-180, south, 0, north], [0, south, 180, north]];
  const norm = (x: number) => ((((x + 180) % 360) + 360) % 360) - 180;
  const w = norm(b.west);
  const e = norm(b.east);
  if (w <= e) return [[w, south, e, north]];
  return [[w, south, 180, north], [-180, south, e, north]];
}

function inBoxes(lng: number, lat: number, bx: [number, number, number, number][]) {
  return bx.some(([w, s, e, n]) => lng >= w && lng <= e && lat >= s && lat <= n);
}

/** Land hex ids visible in the viewport at `res`, capped at `max` (null when over the cap). */
export async function landCellsInView(b: Bounds, res: number, max = 60000): Promise<string[] | null> {
  const bx = boxes(b);
  if (res <= 4) {
    const all = await loadStatic(res);
    const out = all.filter((c) => inBoxes(c.lng, c.lat, bx)).map((c) => c.id);
    return out.length > max ? null : out;
  }
  const ids: string[] = [];
  for (const [w, s, e, n] of bx) {
    // Split wide boxes so no polygon spans more than 180° of longitude.
    for (let x = w; x < e; x += 90) {
      const x2 = Math.min(e, x + 90);
      const ring = [[s, x], [s, x2], [n, x2], [n, x], [s, x]];
      ids.push(...polygonToCells(ring, res));
      if (ids.length > max * 3) return null;
    }
  }
  const ix = await loadIndex(res);
  const parents = [...new Set(ids.map((id) => cellToParent(id, ix.parentRes)))];
  await ensureDecoded(res, parents);
  const out = ids.filter((id) => {
    const p = cellToParent(id, ix.parentRes);
    return ix.fullSet.has(p) || decoded.get(`${res}:${p}`)?.has(id);
  });
  return out.length > max ? null : out;
}
