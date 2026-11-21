import { describe, expect, it, vi } from "vitest";
import { dayHeadings, dayLabel, questionCount } from "@/lib/qa-history";

// qa-history.ts imports the API client, which needs NEXT_PUBLIC_API_URL at import time.
// These tests only cover the formatting helpers, so no request is ever made.
vi.mock("@/lib/api", () => ({ apiFetch: vi.fn() }));

// Local times, so the checks hold in any time zone.
const NOW = new Date(2026, 10, 19, 15, 0); // Nov 19, 2026, 3:00 PM

describe("dayLabel", () => {
  it("says Today for a question asked earlier today", () => {
    expect(dayLabel(new Date(2026, 10, 19, 0, 5).toISOString(), NOW)).toBe("Today");
  });

  it("says Yesterday with the date for a question asked yesterday", () => {
    const label = dayLabel(new Date(2026, 10, 18, 23, 59).toISOString(), NOW);
    expect(label.startsWith("Yesterday · ")).toBe(true);
    expect(label).toContain("18");
  });

  it("gives the full date for anything older", () => {
    const label = dayLabel(new Date(2026, 10, 17, 9, 0).toISOString(), NOW);
    expect(label).toContain("17");
    expect(label).toContain("2026");
  });
});

describe("dayHeadings", () => {
  it("puts a heading only above the first question of each day", () => {
    const at = (day: number, hour: number) => new Date(2026, 10, day, hour).toISOString();
    const headings = dayHeadings(
      [
        { id: "a", askedAt: at(17, 9) },
        { id: "b", askedAt: at(17, 10) },
        { id: "c", askedAt: at(19, 9) },
        { id: "d", askedAt: at(19, 11) },
      ],
      NOW,
    );

    expect([...headings.keys()]).toEqual(["a", "c"]);
    expect(headings.get("c")).toBe("Today");
  });

  it("has no headings for an empty conversation", () => {
    expect(dayHeadings([], NOW).size).toBe(0);
  });
});

describe("questionCount", () => {
  it("counts questions in words", () => {
    expect(questionCount(0)).toBe("0 questions");
    expect(questionCount(1)).toBe("1 question");
    expect(questionCount(2)).toBe("2 questions");
  });
});
