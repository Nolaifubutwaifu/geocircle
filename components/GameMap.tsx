"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { Map as MlMap, GeoJSONSource, StyleSpecification } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { cellToLatLng } from "h3-js";
import { destinationPoint, haversineKm, type LngLat } from "@/lib/geo";
import { hexRing } from "@/lib/cells";
import { landCellsInView, resForZoom, type Bounds } from "@/lib/land";

export const C = {
  ocean: "#0C1734",
  grid: "#152552",
  side: "#060B1C",
  land: ["#3B5BA4", "#4364AF", "#4B6EBA"],
  ice: ["#6A80BC", "#7489C4"],
  coast: "#9FB4EC",
  border: "#7F95D4",
  city: "#DCE4F8",
  amber: "#FAC775",
  ember: "#EF9F27",
  bad: "#F08A6C",
};

export type Handle = { id: string; at: LngLat };
export type Pill = { id: string; at: LngLat; text: string; tone?: "ok" | "bad" };
export type HitTarget = { kind: "handle"; id: string } | { kind: "ring" } | { kind: "sizing" };

type Props = {
  view: { center: LngLat; zoom: number };
  projection: "mercator" | "globe";
  shape: LngLat[] | null; // closed ring of the player's shape
  shapeBad: boolean;
  pins: LngLat[]; // open polyline while placing triangle pins
  best: LngLat[] | null;
  handles: Handle[];
  pills: Pill[];
  ring: { center: LngLat; radiusKm: number } | null; // circle edge is draggable
  captureAll: boolean; // next press sizes the circle instead of panning
  heat: { id: string; v: number }[] | null;
  dimLand: boolean;
  interactive: boolean;
  bottomInset: number;
  onTap: (p: LngLat) => void;
  onDrag: (hit: HitTarget, p: LngLat, phase: "start" | "move" | "end") => void;
  onView: (res: number, bounds: Bounds) => void;
};

const STYLE: StyleSpecification = {
  version: 8,
  glyphs: "https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf",
  sources: {},
  layers: [{ id: "bg", type: "background", paint: { "background-color": C.ocean } }],
};

const empty = (): GeoJSON.FeatureCollection => ({ type: "FeatureCollection", features: [] });
const poly = (ring: LngLat[]): GeoJSON.Feature => ({ type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [ring] } });
const line = (pts: LngLat[], props = {}): GeoJSON.Feature => ({ type: "Feature", properties: props, geometry: { type: "LineString", coordinates: pts } });

function graticule(): GeoJSON.FeatureCollection {
  const f: GeoJSON.Feature[] = [];
  for (let lng = -180; lng <= 180; lng += 15) f.push(line(Array.from({ length: 35 }, (_, i) => [lng, -85 + i * 5] as LngLat)));
  for (let lat = -75; lat <= 75; lat += 15) f.push(line(Array.from({ length: 73 }, (_, i) => [-180 + i * 5, lat] as LngLat)));
  return { type: "FeatureCollection", features: f };
}

function hexFeatures(ids: string[], value?: Map<string, number>): GeoJSON.FeatureCollection {
  return {
    type: "FeatureCollection",
    features: ids.map((id) => {
      const lat = cellToLatLng(id)[0];
      const props: Record<string, number> = { t: parseInt(id.slice(-4, -1), 16) % 3, ice: Math.abs(lat) > 62 ? 1 : 0 };
      if (value) props.v = value.get(id) ?? 0;
      return { type: "Feature", properties: props, geometry: { type: "Polygon", coordinates: [hexRing(id)] } };
    }),
  };
}

// shortcut: MapLibre v6 hangs when bundled by Turbopack (its worker never answers), so the browser loads
// the untouched ESM build from /public/maplibre (copied by predev/prebuild). Revisit on MapLibre/Next upgrades.
let mlPromise: Promise<typeof import("maplibre-gl")> | null = null;
function loadMaplibre() {
  const url = "/maplibre/maplibre-gl.mjs";
  return (mlPromise ??= import(/* webpackIgnore: true */ /* turbopackIgnore: true */ url));
}

export default function GameMap(props: Props) {
  const el = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MlMap | null>(null);
  const [ready, setReady] = useState(false);
  const live = useRef(props);
  useLayoutEffect(() => {
    live.current = props;
  });
  const pillEls = useRef(new Map<string, HTMLDivElement>());
  const pillLayer = useRef<HTMLDivElement>(null);

  function positionPills() {
    const map = mapRef.current;
    if (!map) return;
    for (const p of live.current.pills) {
      const node = pillEls.current.get(p.id);
      if (!node) continue;
      const s = map.project(p.at);
      node.style.transform = `translate(${Math.round(s.x)}px, ${Math.round(s.y)}px) translate(-50%, -150%)`;
    }
  }

  // Create the map once.
  useEffect(() => {
    let cleanup: (() => void) | undefined;
    let dead = false;
    function setup(ml: typeof import("maplibre-gl")) {
    const map = new ml.Map({
      container: el.current!,
      style: STYLE,
      center: live.current.view.center,
      zoom: live.current.view.zoom,
      minZoom: 0.8,
      maxZoom: 10,
      attributionControl: false,
      dragRotate: false,
      pitchWithRotate: false,
      renderWorldCopies: true,
    });
    map.touchZoomRotate.disableRotation();
    map.keyboard.disable(); // arrows move the shape instead (handled in Game)
    mapRef.current = map;
    if (process.env.NODE_ENV !== "production") (window as unknown as { __map: MlMap }).__map = map;

    map.on("load", () => {
      const add = (id: string, data: GeoJSON.FeatureCollection | string = empty()) => map.addSource(id, { type: "geojson", data, tolerance: 0 });
      add("grid", graticule());
      add("hex");
      add("heat");
      add("shape");
      add("pins");
      add("best");
      add("handles");
      map.addLayer({ id: "grid", type: "line", source: "grid", paint: { "line-color": C.grid, "line-width": 1 } });
      map.addLayer({ id: "hex-side", type: "fill", source: "hex", paint: { "fill-color": C.side, "fill-translate": [0, 2.5], "fill-antialias": false } });
      map.addLayer({
        id: "hex-top",
        type: "fill",
        source: "hex",
        paint: {
          "fill-color": ["case", ["==", ["get", "ice"], 1], ["match", ["get", "t"], 0, C.ice[0], C.ice[1]], ["match", ["get", "t"], 0, C.land[0], 1, C.land[1], C.land[2]]],
          "fill-outline-color": C.ocean,
          "fill-opacity-transition": { duration: 600 },
        },
      });
      map.addLayer({
        id: "heat",
        type: "fill",
        source: "heat",
        paint: {
          "fill-color": ["interpolate", ["linear"], ["get", "v"], 0, "#3A2A12", 0.15, "#854F0B", 0.4, C.ember, 0.75, C.amber, 1, "#FFF4DC"],
          "fill-outline-color": C.ocean,
          "fill-opacity": 0,
          "fill-opacity-transition": { duration: 900 },
        },
      });
      map.addSource("coast", { type: "geojson", data: "/data/coast-10m.json" });
      map.addSource("borders", { type: "geojson", data: "/data/borders-10m.json" });
      map.addSource("places", { type: "geojson", data: "/data/places.json" });
      map.addLayer({ id: "borders", type: "line", source: "borders", minzoom: 3, paint: { "line-color": C.border, "line-width": 0.8, "line-dasharray": [3, 2], "line-opacity": 0.7 } });
      map.addLayer({ id: "coast", type: "line", source: "coast", minzoom: 4.5, paint: { "line-color": C.coast, "line-width": 0.9, "line-opacity": 0.75 } });
      map.addLayer({
        id: "city-dot",
        type: "circle",
        source: "places",
        minzoom: 4.5,
        filter: ["<=", ["get", "r"], ["interpolate", ["linear"], ["zoom"], 4.5, 2, 6, 5, 8, 8, 10, 10]],
        paint: { "circle-radius": 2.5, "circle-color": C.city, "circle-stroke-color": C.ocean, "circle-stroke-width": 1 },
      });
      map.addLayer({
        id: "city-label",
        type: "symbol",
        source: "places",
        minzoom: 4.5,
        filter: ["<=", ["get", "r"], ["interpolate", ["linear"], ["zoom"], 4.5, 2, 6, 5, 8, 8, 10, 10]],
        layout: {
          "text-field": ["get", "n"],
          "text-font": ["Noto Sans Regular"],
          "text-size": 11,
          "text-offset": [0, 0.9],
          "text-anchor": "top",
          "symbol-sort-key": ["-", 0, ["get", "p"]],
        },
        paint: { "text-color": C.city, "text-halo-color": C.ocean, "text-halo-width": 1.4 },
      });
      map.addLayer({ id: "best", type: "line", source: "best", paint: { "line-color": "#E6ECFA", "line-width": 2, "line-dasharray": [2, 2] } });
      map.addLayer({ id: "shape-fill", type: "fill", source: "shape", paint: { "fill-color": ["case", ["get", "bad"], C.bad, C.amber], "fill-opacity": 0.22 } });
      map.addLayer({ id: "shape-line", type: "line", source: "shape", paint: { "line-color": ["case", ["get", "bad"], C.bad, C.amber], "line-width": 2.5 } });
      map.addLayer({ id: "pins-line", type: "line", source: "pins", paint: { "line-color": C.amber, "line-width": 2, "line-dasharray": [1.5, 1.5] } });
      map.addLayer({ id: "handles-shadow", type: "circle", source: "handles", paint: { "circle-radius": 9, "circle-color": C.side, "circle-translate": [0, 2.5] } });
      map.addLayer({
        id: "handles",
        type: "circle",
        source: "handles",
        paint: { "circle-radius": ["case", ["==", ["get", "id"], "center"], 6, 8], "circle-color": C.amber, "circle-stroke-color": C.ocean, "circle-stroke-width": 2 },
      });
      setReady(true);
      refreshView();
    });

    // Rebuild the visible land hexes after each move.
    let timer: ReturnType<typeof setTimeout> | undefined;
    let seq = 0;
    const refreshView = () => {
      clearTimeout(timer);
      timer = setTimeout(async () => {
        const my = ++seq;
        const b = map.getBounds();
        let res = resForZoom(map.getZoom());
        const bounds = { west: b.getWest(), south: b.getSouth(), east: b.getEast(), north: b.getNorth() };
        let ids: string[] | null = null;
        while (res >= 2 && (ids = await landCellsInView(bounds, res)) === null) res--;
        if (my !== seq || !ids) return;
        (map.getSource("hex") as GeoJSONSource | undefined)?.setData(hexFeatures(ids));
        el.current?.setAttribute("data-hexes", String(ids.length)); // lets tests wait for the map
        live.current.onView(res, bounds);
      }, 120);
    };
    map.on("moveend", refreshView);
    map.on("render", () => positionPills());

    // Pointer handling: drags on handles, the circle edge, or "sizing" never pan the map.
    const box = map.getCanvasContainer();
    let drag: HitTarget | null = null;
    let down: { x: number; y: number; t: number } | null = null;
    const toLngLat = (e: PointerEvent): LngLat | null => {
      const r = box.getBoundingClientRect();
      const ll = map.unproject([e.clientX - r.left, e.clientY - r.top]);
      return Number.isFinite(ll.lng) && Number.isFinite(ll.lat) ? [ll.lng, ll.lat] : null;
    };
    const hitTest = (e: PointerEvent): HitTarget | null => {
      const p = live.current;
      if (!p.interactive) return null;
      // Right after the centre is dropped, any press (even on the centre) sizes the circle.
      if (p.captureAll) return { kind: "sizing" };
      const r = box.getBoundingClientRect();
      const x = e.clientX - r.left;
      const y = e.clientY - r.top;
      const slop = e.pointerType === "touch" ? 30 : 18;
      let bestH: { id: string; d: number } | null = null;
      for (const h of p.handles) {
        const s = map.project(h.at);
        const d = Math.hypot(s.x - x, s.y - y);
        if (d < slop && (!bestH || d < bestH.d)) bestH = { id: h.id, d };
      }
      if (bestH) return { kind: "handle", id: bestH.id };
      if (p.captureAll) return { kind: "sizing" };
      if (p.ring) {
        const at = toLngLat(e);
        if (at) {
          const bearing = bearingDeg(p.ring.center, at);
          const edge = map.project(destinationPoint(p.ring.center, bearing, p.ring.radiusKm));
          if (Math.hypot(edge.x - x, edge.y - y) < slop * 0.8) return { kind: "ring" };
        }
      }
      return null;
    };
    const onDown = (e: PointerEvent) => {
      if (e.button !== 0) return;
      down = { x: e.clientX, y: e.clientY, t: performance.now() };
      const hit = hitTest(e);
      if (!hit) return;
      e.stopPropagation();
      e.preventDefault();
      map.dragPan.disable();
      drag = hit;
      box.setPointerCapture(e.pointerId);
      const at = toLngLat(e);
      if (at) live.current.onDrag(hit, at, "start");
    };
    const onMove = (e: PointerEvent) => {
      if (!drag) return;
      const at = toLngLat(e);
      if (at) live.current.onDrag(drag, at, "move");
    };
    const onUp = (e: PointerEvent) => {
      const at = toLngLat(e);
      if (drag) {
        if (at) live.current.onDrag(drag, at, "end");
        drag = null;
        map.dragPan.enable();
      } else if (down && at && live.current.interactive) {
        const moved = Math.hypot(e.clientX - down.x, e.clientY - down.y);
        if (moved < 6 && performance.now() - down.t < 600) live.current.onTap(at);
      }
      down = null;
    };
    box.addEventListener("pointerdown", onDown, { capture: true });
    box.addEventListener("pointermove", onMove);
    box.addEventListener("pointerup", onUp);
    box.addEventListener("pointercancel", onUp);

    return () => {
      clearTimeout(timer);
      map.remove();
      mapRef.current = null;
    };
    }
    loadMaplibre().then((ml) => {
      if (!dead) cleanup = setup(ml);
    });
    return () => {
      dead = true;
      cleanup?.();
    };
     
  }, []);

  // Projection toggle.
  useEffect(() => {
    if (!ready) return;
    mapRef.current!.setProjection({ type: props.projection });
  }, [ready, props.projection]);

  // Shape, pins, best and handles.
  useEffect(() => {
    if (!ready) return;
    const m = mapRef.current!;
    const set = (id: string, fc: GeoJSON.FeatureCollection) => (m.getSource(id) as GeoJSONSource).setData(fc);
    set("shape", props.shape ? { type: "FeatureCollection", features: [{ ...poly(props.shape), properties: { bad: props.shapeBad } }] } : empty());
    set("pins", props.pins.length > 1 ? { type: "FeatureCollection", features: [line(props.pins)] } : empty());
    set("best", props.best ? { type: "FeatureCollection", features: [line(props.best)] } : empty());
    set("handles", {
      type: "FeatureCollection",
      features: props.handles.map((h) => ({ type: "Feature", properties: { id: h.id }, geometry: { type: "Point", coordinates: h.at } })),
    });
    positionPills();
  }, [ready, props.shape, props.shapeBad, props.pins, props.best, props.handles, props.pills]);

  // Reveal heat and land dimming.
  useEffect(() => {
    if (!ready) return;
    const m = mapRef.current!;
    const heat = props.heat ?? [];
    const max = heat.reduce((a, h) => Math.max(a, h.v), 0) || 1;
    const values = new Map(heat.map((h) => [h.id, Math.sqrt(h.v / max)]));
    (m.getSource("heat") as GeoJSONSource).setData(hexFeatures(heat.map((h) => h.id), values));
    m.setPaintProperty("heat", "fill-opacity", props.heat ? 0.92 : 0);
    m.setPaintProperty("hex-top", "fill-opacity", props.dimLand ? 0.45 : 1);
    m.setPaintProperty("shape-fill", "fill-opacity", props.dimLand ? 0 : 0.22);
  }, [ready, props.heat, props.dimLand]);

  // Keep the map centred in the space the bottom panel leaves free.
  useEffect(() => {
    if (!ready) return;
    mapRef.current!.easeTo({ padding: { top: 0, left: 0, right: 0, bottom: props.bottomInset }, duration: 500 });
  }, [ready, props.bottomInset]);

  return (
    <div className="absolute inset-0">
      <div ref={el} className="h-full w-full" aria-label="Map" role="application" />
      <div ref={pillLayer} className="pointer-events-none absolute inset-0 overflow-hidden">
        {props.pills.map((p) => (
          <div
            key={p.id}
            ref={(n) => {
              if (n) pillEls.current.set(p.id, n);
              else pillEls.current.delete(p.id);
            }}
            className={`pill absolute left-0 top-0 ${p.tone === "bad" ? "pill-bad" : ""}`}
          >
            {p.text}
          </div>
        ))}
      </div>
    </div>
  );
}

function bearingDeg(a: LngLat, b: LngLat): number {
  const R = Math.PI / 180;
  const y = Math.sin((b[0] - a[0]) * R) * Math.cos(b[1] * R);
  const x = Math.cos(a[1] * R) * Math.sin(b[1] * R) - Math.sin(a[1] * R) * Math.cos(b[1] * R) * Math.cos((b[0] - a[0]) * R);
  return Math.atan2(y, x) / R;
}

export { bearingDeg, haversineKm };
