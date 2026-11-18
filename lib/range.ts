// HTTP byte-range loading with merging of nearby ranges and a whole-file fallback when Range is ignored.
const wholeFiles = new Map<string, Promise<ArrayBuffer>>();

/** Fetches [start, end) byte ranges. Returns a reader that slices any requested range out of what was fetched. */
export async function fetchRanges(url: string, ranges: [number, number][], mergeGap = 4096): Promise<(start: number, end: number) => Uint8Array> {
  const whole = wholeFiles.get(url);
  if (whole) {
    const buf = await whole;
    return (s, e) => new Uint8Array(buf, s, e - s);
  }
  const sorted = [...ranges].sort((a, b) => a[0] - b[0]);
  const batches: [number, number][] = [];
  for (const [s, e] of sorted) {
    const last = batches[batches.length - 1];
    if (last && s - last[1] <= mergeGap) last[1] = Math.max(last[1], e);
    else batches.push([s, e]);
  }
  const parts: { start: number; buf: ArrayBuffer }[] = [];
  let fullBuf: ArrayBuffer | null = null;
  await Promise.all(
    batches.map(async ([s, e]) => {
      if (fullBuf) return;
      const res = await fetch(url, { headers: { Range: `bytes=${s}-${e - 1}` } });
      if (!res.ok) throw new Error(`Couldn't load map data (${res.status})`);
      if (res.status === 206) parts.push({ start: s, buf: await res.arrayBuffer() });
      else {
        if (!wholeFiles.has(url)) wholeFiles.set(url, res.arrayBuffer());
        fullBuf = await wholeFiles.get(url)!;
      }
    }),
  );
  if (fullBuf) {
    const b: ArrayBuffer = fullBuf;
    return (s, e) => new Uint8Array(b, s, e - s);
  }
  return (s, e) => {
    const p = parts.find((x) => x.start <= s && x.start + x.buf.byteLength >= e);
    if (!p) throw new Error(`Range ${s}-${e} was not fetched`);
    return new Uint8Array(p.buf, s - p.start, e - s);
  };
}
