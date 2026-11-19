import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Geocircle",
    short_name: "Geocircle",
    description: "A daily map puzzle. Catch a share of the world's rice, beer or earthquakes with one circle or one triangle.",
    start_url: "/",
    display: "standalone",
    background_color: "#070c1c",
    theme_color: "#0B1228",
    icons: [
      { src: "/icons/192", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/icons/512", sizes: "512x512", type: "image/png", purpose: "any" },
      { src: "/icons/512", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  };
}
