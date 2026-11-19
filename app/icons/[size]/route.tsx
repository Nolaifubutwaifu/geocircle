import { iconPng } from "@/lib/brand";

const SIZES = ["32", "180", "192", "512"];
export const generateStaticParams = () => SIZES.map((size) => ({ size }));

export async function GET(_: Request, { params }: RouteContext<"/icons/[size]">) {
  return iconPng(Number((await params).size));
}
