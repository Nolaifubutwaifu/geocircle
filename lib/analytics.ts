// PostHog (EU cloud, via the Vercel Marketplace). Privacy-first: nothing is stored on the device, so no consent
// banner is needed, but a player who comes back tomorrow counts as a new visitor. Retention numbers need a banner.
import posthog from "posthog-js";

const token = process.env.NEXT_PUBLIC_POSTHOG_PROJECT_TOKEN;
let started = false;

export function startAnalytics() {
  if (started || !token || typeof window === "undefined") return; // no token in tests and plain local runs
  started = true;
  posthog.init(token, {
    api_host: process.env.NEXT_PUBLIC_POSTHOG_HOST,
    persistence: "memory",
    person_profiles: "identified_only",
    autocapture: false,
    disable_session_recording: true,
    disable_surveys: true,
    capture_pageview: "history_change",
  });
}

export function track(event: "puzzle_submitted" | "puzzle_shared" | "puzzle_feedback" | "challenge_opened", props: Record<string, unknown>) {
  if (started) posthog.capture(event, props);
}
