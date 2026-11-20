// Challenge links: "?beat=64&s=c,151.21,-33.87,420" carries a friend's score and shape.
import type { LngLat, Shape } from "./geo";

const r2 = (x: number) => Math.round(x * 100) / 100;

export function encodeShape(s: Shape): string {
  if (s.kind === "circle") return ["c", r2(s.center[0]), r2(s.center[1]), Math.round(s.radiusKm)].join(",");
  return ["t", ...s.pins.flatMap(([lng, lat]) => [r2(lng), r2(lat)])].join(",");
}

const okLngLat = (lng: number, lat: number) => Math.abs(lng) <= 540 && Math.abs(lat) <= 90;

/** Parses a shape from a URL. Anything malformed gives null: the link comes from outside. */
export function decodeShape(raw: string | null): Shape | null {
  if (!raw || raw.length > 200) return null;
  const [kind, ...rest] = raw.split(",");
  const n = rest.map(Number);
  if (n.some((x) => !Number.isFinite(x))) return null;
  if (kind === "c" && n.length === 3 && okLngLat(n[0], n[1]) && n[2] >= 10 && n[2] <= 5000)
    return { kind: "circle", center: [n[0], n[1]], radiusKm: n[2] };
  if (kind === "t" && n.length === 6) {
    const pins = [0, 2, 4].map((i) => [n[i], n[i + 1]] as LngLat);
    if (pins.every(([lng, lat]) => okLngLat(lng, lat))) return { kind: "triangle", pins: pins as [LngLat, LngLat, LngLat] };
  }
  return null;
}

/** A friend's challenge from the page URL, or null. */
export function readChallenge(search: string): { score: number; shape: Shape | null } | null {
  const q = new URLSearchParams(search);
  const score = Number(q.get("beat"));
  if (!q.has("beat") || !Number.isInteger(score) || score < 0 || score > 100) return null;
  return { score, shape: decodeShape(q.get("s")) };
}
