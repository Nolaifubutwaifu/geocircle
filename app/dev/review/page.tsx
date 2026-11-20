import { notFound } from "next/navigation";
import ReviewClient from "@/components/ReviewClient";

export const metadata = { title: "Review candidates", robots: { index: false } };

/** Local curation tool (PRD §5.7). Production builds serve a 404. */
export default function ReviewPage() {
  if (process.env.NODE_ENV !== "development") notFound();
  return <ReviewClient />;
}
