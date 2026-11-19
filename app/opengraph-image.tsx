import { OG_SIZE, ogPng } from "@/lib/brand";

export const alt = "Geocircle: a daily map puzzle";
export const size = OG_SIZE;
export const contentType = "image/png";

export default function Image() {
  return ogPng(null, "Catch a share of the world's rice, beer or earthquakes with one circle.");
}
