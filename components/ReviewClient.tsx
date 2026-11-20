"use client";

import dynamic from "next/dynamic";

const ReviewClient = dynamic(() => import("./Review"), { ssr: false, loading: () => <main className="fixed inset-0 bg-ocean" /> });
export default ReviewClient;
