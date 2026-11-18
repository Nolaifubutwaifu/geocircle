"use client";

import dynamic from "next/dynamic";

/** The game reads localStorage and the local date on first render, so it only renders in the browser. */
const GameClient = dynamic(() => import("./Game"), { ssr: false, loading: () => <main className="fixed inset-0 bg-ocean" /> });
export default GameClient;
