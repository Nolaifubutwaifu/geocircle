import { describe, expect, it } from "vitest";
import { decide, nextId, type Candidate, type Curated } from "./curated";

const cand = (key: string) =>
  ({ key, dataset: "coffee", rank: 0.4, inputs_hash: "x", difficulty: "medium", prompt: "Gen prompt.", fact: "Gen fact.", checks: { passed: true } }) as unknown as Candidate;
const day = new Date(2026, 11, 2);

describe("curated decisions", () => {
  it("gives the next free ID above published and curated IDs", () => {
    expect(nextId({}, [1, 2, 40])).toBe(41);
    expect(nextId({ a: { status: "approved", id: 57 } } as unknown as Curated, [1, 40, "c-aus"])).toBe(58);
  });

  it("approves with the edited fact and null prompt when unchanged", () => {
    const out = decide({}, cand("k"), { key: "k", status: "approved", fact: " My fact. ", prompt: "Gen prompt." }, [40], day);
    expect(out.k).toMatchObject({ status: "approved", id: 41, fact: "My fact.", prompt: null, decided: "2026-12-02" });
    const p = (out.k as { puzzle: Record<string, unknown> }).puzzle;
    expect(p.key).toBe("k");
    expect(p).not.toHaveProperty("checks");
  });

  it("keeps an edited prompt", () => {
    const out = decide({}, cand("k"), { key: "k", status: "approved", fact: "f", prompt: "New prompt." }, [40], day);
    expect(out.k).toMatchObject({ prompt: "New prompt." });
  });

  it("rejects with a reason and optional note", () => {
    const out = decide({}, cand("k"), { key: "k", status: "rejected", reason: "boring", note: "" }, [], day);
    expect(out.k).toEqual({ status: "rejected", reason: "boring", note: null, decided: "2026-12-02" });
  });

  it("refuses bad input and double decisions", () => {
    expect(() => decide({}, undefined, { key: "k", status: "rejected", reason: "boring" }, [])).toThrow(/unknown/);
    expect(() => decide({}, cand("k"), { key: "k", status: "rejected", reason: "meh" }, [])).toThrow(/reason/);
    expect(() => decide({}, cand("k"), { key: "k", status: "approved", fact: " ", prompt: "p" }, [])).toThrow(/fact/);
    const once = decide({}, cand("k"), { key: "k", status: "rejected", reason: "broken" }, []);
    expect(() => decide(once, cand("k"), { key: "k", status: "rejected", reason: "broken" }, [])).toThrow(/already/);
  });
});
