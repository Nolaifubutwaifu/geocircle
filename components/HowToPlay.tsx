"use client";

import { useEffect, useRef } from "react";

const STEPS = {
  circle: [
    ["Tap the map", "Drop the centre of your circle anywhere on Earth."],
    ["Drag outward", "Size it. The radius shows as you go, but not what's inside."],
    ["Submit", "The data lights up and you see how close you got."],
  ],
  triangle: [
    ["Tap three times", "Each tap drops a pin. Three pins make your triangle."],
    ["Drag the pins", "Reshape it until you're happy. Side lengths show as you go."],
    ["Submit", "The data lights up and you see how close you got."],
  ],
} as const;

export default function HowToPlay({ mechanic, onClose }: { mechanic: "circle" | "triangle"; onClose: () => void }) {
  const btn = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    btn.current?.focus();
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose]);

  return (
    <div className="absolute inset-0 z-20 flex items-center justify-center bg-ocean/70 p-4 backdrop-blur-[2px]" role="dialog" aria-modal="true" aria-labelledby="howto-title">
      <div className="chamfer w-full max-w-sm">
        <div className="chamfer-in p-6">
          <h2 id="howto-title" className="font-display text-[24px] font-semibold">
            How to play
          </h2>
          <p className="mt-1 text-[14px] text-mist">One puzzle a day. Real data, scored to the kilometre.</p>
          <ol className="mt-5 space-y-4">
            {STEPS[mechanic].map(([title, body], i) => (
              <li key={title} className="flex gap-3">
                <span className="hex-num">{i + 1}</span>
                <div>
                  <p className="font-display text-[16px] font-semibold">{title}</p>
                  <p className="text-[14px] leading-relaxed text-mist">{body}</p>
                </div>
              </li>
            ))}
          </ol>
          <p className="mt-5 text-[13px] text-mist">Keyboard: arrows move, + and − resize, 1 to 3 pick a pin, Enter submits.</p>
          <button ref={btn} className="primary-btn mt-5 w-full" onClick={onClose}>
            Start playing
          </button>
        </div>
      </div>
    </div>
  );
}
