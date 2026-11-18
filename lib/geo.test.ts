import { describe, expect, it } from "vitest";
import { latLngToCell, gridDisk, cellToParent, cellToLatLng } from "h3-js";
import {
  destinationPoint,
  distToArcKm,
  geodesicCircle,
  haversineKm,
  perimeterKm,
  pointInSphericalTriangle,
  validateShape,
  contains,
  type LngLat,
  type Shape,
} from "./geo";
import { scoreBudget, scoreTarget } from "./score";
import { classifyGroups, encodeRecords, makeDataset, parseRecords, fineLngLat, weightOf, type CellIndex } from "./cells";

const london: LngLat = [-0.1276, 51.5072];
const paris: LngLat = [2.3522, 48.8566];

describe("geo", () => {
  it("measures London to Paris", () => {
    expect(haversineKm(london, paris)).toBeGreaterThan(340);
    expect(haversineKm(london, paris)).toBeLessThan(346);
  });

  it("puts every circle vertex at the radius", () => {
    const ring = geodesicCircle([153.03, -27.47], 1234);
    expect(ring[0]).toEqual(ring[ring.length - 1]);
    for (const p of ring) expect(Math.abs(haversineKm([153.03, -27.47], p) - 1234)).toBeLessThan(0.01);
  });

  it("keeps circle longitudes continuous across the date line", () => {
    const ring = geodesicCircle([179.5, -17], 300);
    for (let i = 1; i < ring.length; i++) expect(Math.abs(ring[i][0] - ring[i - 1][0])).toBeLessThan(10);
  });

  it("round-trips destination point", () => {
    const d = destinationPoint(london, 135, 800);
    expect(haversineKm(london, d)).toBeCloseTo(800, 3);
  });

  it("tests points in a triangle, either winding", () => {
    const a: LngLat = [0, 0];
    const b: LngLat = [10, 0];
    const c: LngLat = [5, 10];
    expect(pointInSphericalTriangle([5, 3], a, b, c)).toBe(true);
    expect(pointInSphericalTriangle([5, 3], c, b, a)).toBe(true);
    expect(pointInSphericalTriangle([15, 3], a, b, c)).toBe(false);
    expect(pointInSphericalTriangle([-175, -3], a, b, c)).toBe(false); // antipode side
  });

  it("handles triangles across the date line", () => {
    const pins: [LngLat, LngLat, LngLat] = [
      [175, -10],
      [-175, -10],
      [180, 0],
    ];
    expect(pointInSphericalTriangle([179.9, -6], ...pins)).toBe(true);
    expect(pointInSphericalTriangle([0, -6], ...pins)).toBe(false);
  });

  it("measures distance to an arc", () => {
    expect(distToArcKm([5, 1], [0, 0], [10, 0])).toBeCloseTo(haversineKm([5, 1], [5, 0]), 0);
    expect(distToArcKm([12, 0], [0, 0], [10, 0])).toBeCloseTo(haversineKm([12, 0], [10, 0]), 3);
  });

  it("validates shapes", () => {
    expect(validateShape({ kind: "circle", center: london, radiusKm: 5 })).toMatch(/at least/);
    expect(validateShape({ kind: "circle", center: [0, 80], radiusKm: 1500 })).toMatch(/pole/);
    expect(validateShape({ kind: "circle", center: london, radiusKm: 500 })).toBeNull();
    const t: Shape = { kind: "triangle", pins: [london, paris, [4.9, 52.37]] };
    expect(validateShape(t)).toBeNull();
    expect(validateShape(t, 100)).toMatch(/Over budget/);
    expect(perimeterKm(t.pins)).toBeGreaterThan(900);
  });
});

describe("score", () => {
  it("matches the spec examples", () => {
    expect(scoreTarget(25, 20)).toBe(75);
    expect(scoreTarget(0, 20)).toBe(0);
    expect(scoreTarget(40, 20)).toBe(0);
    expect(scoreTarget(20, 20)).toBe(100);
    expect(scoreBudget(43.5, 50)).toBe(87);
    expect(scoreBudget(60, 50)).toBe(100);
  });
});

// Seeded RNG so failures are reproducible.
function rng(seed: number) {
  return () => ((seed = (seed * 1664525 + 1013904223) >>> 0) / 2 ** 32);
}

describe("refined capture equals brute force", () => {
  // Fixture: r5 cells around Brisbane and Sydney with random weights and two countries.
  const rand = rng(42);
  const ids = [...new Set([...gridDisk(latLngToCell(-27.47, 153.03, 5), 40), ...gridDisk(latLngToCell(-33.87, 151.21, 5), 30)])];
  const cells = ids.map((id) => ({ id, value: Math.floor(rand() * 1000), country: rand() < 0.5 ? 1 : 2 }));
  const byParent = new Map<string, typeof cells>();
  for (const c of cells) {
    const p = cellToParent(c.id, 3);
    byParent.set(p, [...(byParent.get(p) ?? []), c]);
  }
  const ordered: typeof cells = [];
  const index: CellIndex = { res: 5, groups: {} };
  const kg = [0, 2, 5];
  const r3: Record<string, number> = {};
  for (const [p, list] of byParent) {
    const [plat, plng] = cellToLatLng(p);
    const maxR = Math.max(...list.map((c) => { const [la, ln] = cellToLatLng(c.id); return haversineKm([plng, plat], [ln, la]); }));
    index.groups[p] = [ordered.length, list.length, list.reduce((s, c) => s + c.value, 0), maxR];
    r3[p] = list.reduce((s, c) => s + c.value * kg[c.country], 0);
    ordered.push(...list);
  }
  const fine = parseRecords(encodeRecords(ordered));
  // Per-country r3 totals of the raw values, as the pipeline writes them.
  const byCountry: Record<string, Record<string, number>> = {};
  for (const c of ordered) {
    const p = cellToParent(c.id, 3);
    byCountry[c.country] ??= {};
    byCountry[c.country][p] = (byCountry[c.country][p] ?? 0) + c.value;
  }

  for (const [withStat, scoped] of [[false, false], [true, false], [false, true], [true, true]]) {
    it(`matches for 200 random shapes${withStat ? " (per-country stat)" : ""}${scoped ? " (one country only)" : ""}`, () => {
      const ds = makeDataset("x", index, withStat ? { kg, r3 } : null, scoped ? { countries: [1], byCountry } : null);
      // Fill the cache as if every group were fetched.
      let off = 0;
      for (const [p, list] of byParent) {
        ds.cache.set(p, fine.slice(off, off + list.length));
        off += list.length;
      }
      const r = rng(7);
      for (let i = 0; i < 200; i++) {
        const c: LngLat = [149 + r() * 6, -36 + r() * 11];
        const shape: Shape =
          i % 2
            ? { kind: "circle", center: c, radiusKm: 10 + r() * 400 }
            : { kind: "triangle", pins: [c, [c[0] + r() * 4 - 2, c[1] + r() * 4 - 2], [c[0] + r() * 4 - 2, c[1] + r() * 4 - 2]] };
        const brute = fine.filter((f) => contains(shape, fineLngLat(f))).reduce((s, f) => s + weightOf(ds, f), 0);
        const { inside, edge } = classifyGroups(shape, ds);
        let refined = inside.reduce((s, k) => s + (ds.r3Totals.get(k) ?? 0), 0);
        for (const k of edge) for (const f of ds.cache.get(k)!) if (contains(shape, fineLngLat(f))) refined += weightOf(ds, f);
        expect(refined).toBeCloseTo(brute, 3);
      }
    });
  }
});
