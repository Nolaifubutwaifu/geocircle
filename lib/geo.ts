// Spherical geometry for Geocircle. All distances are great-circle km on a sphere.
export type LngLat = [number, number];
type Vec3 = [number, number, number];

export const EARTH_RADIUS_KM = 6371.0088;
const RAD = Math.PI / 180;

export type Circle = { kind: "circle"; center: LngLat; radiusKm: number };
export type Triangle = { kind: "triangle"; pins: [LngLat, LngLat, LngLat] };
export type Shape = Circle | Triangle;

export const LIMITS = {
  circleMinKm: 10,
  circleMaxKm: 5000,
  sideMinKm: 10,
  sideMaxKm: 8000,
};

export function toVec([lng, lat]: LngLat): Vec3 {
  const la = lat * RAD;
  const lo = lng * RAD;
  return [Math.cos(la) * Math.cos(lo), Math.cos(la) * Math.sin(lo), Math.sin(la)];
}

function fromVec([x, y, z]: Vec3): LngLat {
  return [Math.atan2(y, x) / RAD, Math.atan2(z, Math.hypot(x, y)) / RAD];
}

const dot = (a: Vec3, b: Vec3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a: Vec3, b: Vec3): Vec3 => [
  a[1] * b[2] - a[2] * b[1],
  a[2] * b[0] - a[0] * b[2],
  a[0] * b[1] - a[1] * b[0],
];
const norm = (a: Vec3): Vec3 => {
  const l = Math.hypot(a[0], a[1], a[2]);
  return [a[0] / l, a[1] / l, a[2] / l];
};

export function haversineKm(a: LngLat, b: LngLat): number {
  const dLat = (b[1] - a[1]) * RAD;
  const dLng = (b[0] - a[0]) * RAD;
  const h =
    Math.sin(dLat / 2) ** 2 + Math.cos(a[1] * RAD) * Math.cos(b[1] * RAD) * Math.sin(dLng / 2) ** 2;
  return 2 * EARTH_RADIUS_KM * Math.asin(Math.min(1, Math.sqrt(h)));
}

export function destinationPoint([lng, lat]: LngLat, bearingDeg: number, km: number): LngLat {
  const d = km / EARTH_RADIUS_KM;
  const b = bearingDeg * RAD;
  const la1 = lat * RAD;
  const lo1 = lng * RAD;
  const la2 = Math.asin(Math.sin(la1) * Math.cos(d) + Math.cos(la1) * Math.sin(d) * Math.cos(b));
  const lo2 =
    lo1 + Math.atan2(Math.sin(b) * Math.sin(d) * Math.cos(la1), Math.cos(d) - Math.sin(la1) * Math.sin(la2));
  return [lo2 / RAD, la2 / RAD];
}

/** Shift each longitude so consecutive points never jump by more than 180°, keeping lines continuous across the date line. */
function unwrap(ring: LngLat[], refLng: number): LngLat[] {
  let prev = refLng;
  return ring.map(([lng, lat]) => {
    let l = lng;
    while (l - prev > 180) l -= 360;
    while (l - prev < -180) l += 360;
    prev = l;
    return [l, lat];
  });
}

/** Closed ring of n+1 points at exactly radiusKm from center. */
export function geodesicCircle(center: LngLat, radiusKm: number, n = 128): LngLat[] {
  const ring: LngLat[] = [];
  for (let i = 0; i <= n; i++) ring.push(destinationPoint(center, (360 * i) / n, radiusKm));
  return unwrap(ring, center[0]);
}

/** n+1 points along the great circle from a to b. */
export function greatCircleLine(a: LngLat, b: LngLat, n = 64): LngLat[] {
  const va = toVec(a);
  const vb = toVec(b);
  const omega = Math.acos(Math.max(-1, Math.min(1, dot(va, vb))));
  if (omega < 1e-9) return [a, b];
  const pts: LngLat[] = [];
  for (let i = 0; i <= n; i++) {
    const t = i / n;
    const s1 = Math.sin((1 - t) * omega) / Math.sin(omega);
    const s2 = Math.sin(t * omega) / Math.sin(omega);
    pts.push(fromVec([s1 * va[0] + s2 * vb[0], s1 * va[1] + s2 * vb[1], s1 * va[2] + s2 * vb[2]]));
  }
  return unwrap(pts, a[0]);
}

/** Closed ring for a triangle with great-circle edges. */
export function triangleRing(pins: [LngLat, LngLat, LngLat], n = 64): LngLat[] {
  const [a, b, c] = pins;
  const ring = [...greatCircleLine(a, b, n), ...greatCircleLine(b, c, n).slice(1), ...greatCircleLine(c, a, n).slice(1)];
  return unwrap(ring, a[0]);
}

/** Inside test for a triangle smaller than a hemisphere, either winding order. */
export function pointInSphericalTriangle(p: LngLat, a: LngLat, b: LngLat, c: LngLat): boolean {
  const P = toVec(p);
  const A = toVec(a);
  const B = toVec(b);
  const C = toVec(c);
  if (dot(P, norm([A[0] + B[0] + C[0], A[1] + B[1] + C[1], A[2] + B[2] + C[2]])) <= 0) return false;
  const s1 = dot(cross(A, B), P);
  const s2 = dot(cross(B, C), P);
  const s3 = dot(cross(C, A), P);
  return (s1 >= 0 && s2 >= 0 && s3 >= 0) || (s1 <= 0 && s2 <= 0 && s3 <= 0);
}

/** Shortest distance from p to the great-circle arc a to b. */
export function distToArcKm(p: LngLat, a: LngLat, b: LngLat): number {
  const P = toVec(p);
  const A = toVec(a);
  const B = toVec(b);
  const n = cross(A, B);
  const nl = Math.hypot(n[0], n[1], n[2]);
  if (nl < 1e-12) return haversineKm(p, a);
  const N: Vec3 = [n[0] / nl, n[1] / nl, n[2] / nl];
  // Closest point on the full great circle, then check it lies between a and b.
  const proj = norm(cross(N, cross(P, N)));
  const onArc = dot(cross(A, proj), N) >= 0 && dot(cross(proj, B), N) >= 0;
  if (onArc) return Math.asin(Math.min(1, Math.abs(dot(P, N)))) * EARTH_RADIUS_KM;
  return Math.min(haversineKm(p, a), haversineKm(p, b));
}

export function perimeterKm(pins: LngLat[]): number {
  let s = 0;
  for (let i = 0; i < pins.length; i++) s += haversineKm(pins[i], pins[(i + 1) % pins.length]);
  return s;
}

export function sideLengthsKm([a, b, c]: [LngLat, LngLat, LngLat]): [number, number, number] {
  return [haversineKm(a, b), haversineKm(b, c), haversineKm(c, a)];
}

export function contains(shape: Shape, p: LngLat): boolean {
  if (shape.kind === "circle") return haversineKm(shape.center, p) <= shape.radiusKm;
  const [a, b, c] = shape.pins;
  return pointInSphericalTriangle(p, a, b, c);
}

/** Distance from p to the shape (0 when inside). */
export function distToShapeKm(shape: Shape, p: LngLat): number {
  if (shape.kind === "circle") return Math.max(0, haversineKm(shape.center, p) - shape.radiusKm);
  if (contains(shape, p)) return 0;
  const [a, b, c] = shape.pins;
  return Math.min(distToArcKm(p, a, b), distToArcKm(p, b, c), distToArcKm(p, c, a));
}

const POLES: LngLat[] = [
  [0, 90],
  [0, -90],
];

/** Returns a short reason when the shape breaks a rule, otherwise null. */
export function validateShape(shape: Shape, perimeterBudgetKm?: number | null): string | null {
  if (shape.kind === "circle") {
    if (shape.radiusKm < LIMITS.circleMinKm) return `Make the circle at least ${LIMITS.circleMinKm} km wide`;
    if (shape.radiusKm > LIMITS.circleMaxKm) return `Keep the radius under ${LIMITS.circleMaxKm.toLocaleString("en")} km`;
    if (POLES.some((p) => contains(shape, p))) return "The circle can't cover a pole";
    return null;
  }
  const sides = sideLengthsKm(shape.pins);
  if (Math.min(...sides) < LIMITS.sideMinKm) return `Each side needs to be at least ${LIMITS.sideMinKm} km`;
  if (Math.max(...sides) > LIMITS.sideMaxKm) return `Keep each side under ${LIMITS.sideMaxKm.toLocaleString("en")} km`;
  if (POLES.some((p) => contains(shape, p))) return "The triangle can't cover a pole";
  if (perimeterBudgetKm != null) {
    const over = perimeterKm(shape.pins) - perimeterBudgetKm;
    if (over > 0) return `Over budget by ${Math.ceil(over).toLocaleString("en")} km`;
  }
  return null;
}

export function shapeRing(shape: Shape): LngLat[] {
  return shape.kind === "circle" ? geodesicCircle(shape.center, shape.radiusKm) : triangleRing(shape.pins);
}
