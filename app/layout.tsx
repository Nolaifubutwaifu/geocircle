import type { Metadata, Viewport } from "next";
import { Chakra_Petch, Figtree } from "next/font/google";
import "./globals.css";
import Analytics from "@/components/Analytics";

const display = Chakra_Petch({ variable: "--font-chakra", subsets: ["latin"], weight: ["500", "600", "700"] });
const body = Figtree({ variable: "--font-figtree", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Geocircle",
  description: "A daily map puzzle. Catch a share of the world's rice, beer or earthquakes with one circle or one triangle.",
  icons: { icon: { url: "/icons/32", type: "image/png" }, apple: "/icons/180" },
  appleWebApp: { capable: true, title: "Geocircle", statusBarStyle: "default" },
};

export const viewport: Viewport = { themeColor: "#0B1228", width: "device-width", initialScale: 1, viewportFit: "cover" };

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${display.variable} ${body.variable} h-full antialiased`}>
      <body className="min-h-full bg-ocean font-sans text-chalk">
        {children}
        <Analytics />
      </body>
    </html>
  );
}
