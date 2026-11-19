// App icon and share-card artwork, rendered to PNG by next/og (ImageResponse).
import { ImageResponse } from "next/og";
import { readFile } from "node:fs/promises";
import { join } from "node:path";

const OCEAN = "#070c1c";
const EDGE = "#2c3f78";
const CHALK = "#e6ecfa";
const MIST = "#93a3cf";
const AMBER = "#fac775";

// Pointy-top hex points around (cx, cy), matching the map's land hexes.
const hex = (cx: number, cy: number, r: number) =>
  Array.from({ length: 6 }, (_, k) => {
    const a = ((60 * k - 90) * Math.PI) / 180;
    return `${(cx + r * Math.cos(a)).toFixed(2)},${(cy + r * Math.sin(a)).toFixed(2)}`;
  }).join(" ");

const STEP = 22.5; // centre-to-centre distance for r = 13 hexes; drawn at r = 12 for a gap
const CLUSTER = [[0, 0], ...[0, 60, 120, 180, 240, 300].map((d) => [STEP * Math.cos((d * Math.PI) / 180), STEP * Math.sin((d * Math.PI) / 180)])];

/** Seven land hexes with the amber circle on top. Content stays inside the maskable safe zone. */
export function Logo({ size, background = true }: { size: number; background?: boolean }) {
  return (
    <svg width={size} height={size} viewBox="0 0 100 100">
      {background && <rect width="100" height="100" fill={OCEAN} />}
      {CLUSTER.map(([x, y], i) => (
        <polygon key={i} points={hex(50 + x, 50 + y, 12)} fill={i === 0 ? AMBER : EDGE} />
      ))}
      <circle cx="50" cy="50" r="21" fill="none" stroke={AMBER} strokeWidth="5" />
    </svg>
  );
}

export const iconPng = (size: number) => new ImageResponse(<Logo size={size} />, { width: size, height: size });

export const OG_SIZE = { width: 1200, height: 630 };
const font = readFile(join(process.cwd(), "assets/ChakraPetch-SemiBold.ttf"));

/** 1200x630 link preview: wordmark, an optional kicker line and the headline. No answers shown. */
export async function ogPng(kicker: string | null, title: string) {
  return new ImageResponse(
    (
      <div style={{ width: "100%", height: "100%", display: "flex", background: OCEAN, fontFamily: "Chakra Petch", padding: 72 }}>
        <div style={{ display: "flex", flexDirection: "column", flex: 1, paddingRight: 40 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 18 }}>
            <Logo size={72} background={false} />
            <span style={{ fontSize: 48, color: CHALK }}>Geocircle</span>
          </div>
          <div style={{ display: "flex", flexDirection: "column", flex: 1, justifyContent: "center" }}>
            {kicker && <span style={{ fontSize: 34, color: AMBER, marginBottom: 16 }}>{kicker}</span>}
            <span style={{ fontSize: title.length > 70 ? 50 : 60, lineHeight: 1.2, color: CHALK }}>{title}</span>
          </div>
          <span style={{ fontSize: 28, color: MIST }}>A daily map puzzle. Real data, scored to the kilometre.</span>
        </div>
        <div style={{ display: "flex", alignItems: "center" }}>
          <Logo size={300} background={false} />
        </div>
      </div>
    ),
    { ...OG_SIZE, fonts: [{ name: "Chakra Petch", data: await font, weight: 600, style: "normal" }] },
  );
}
