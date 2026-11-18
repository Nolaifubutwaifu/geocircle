"use client";

import dynamic from "next/dynamic";

const DailyClient = dynamic(() => import("./DailyGame"), { ssr: false, loading: () => <main className="fixed inset-0 bg-ocean" /> });
export default DailyClient;
