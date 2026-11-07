// S2-005: the status badge tells every ingestion state apart (S2-BR-005).
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { StatusBadge, statusDotClass, statusLabel } from "@/app/_components/status-badge";

const STATES = [
  ["QUEUED", "Queued"],
  ["RUNNING", "Ingesting…"],
  ["COMPLETED", "Ingested"],
  ["PARTIAL", "Partially ingested"],
  ["FAILED", "Failed"],
] as const;

function render(status: string | null | undefined): string {
  return renderToStaticMarkup(createElement(StatusBadge, { status }));
}

describe("status badge", () => {
  it.each(STATES)("%s is shown as %s", (status, label) => {
    expect(statusLabel(status)).toBe(label);
    expect(render(status)).toContain(label);
  });

  it("gives every state its own label, so no two can be confused", () => {
    const labels = STATES.map(([status]) => statusLabel(status));
    expect(new Set(labels).size).toBe(STATES.length);
  });

  it("shows a partial run in amber and a failed run in red, never in success green", () => {
    expect(render("PARTIAL")).toContain("bg-amber-50");
    expect(render("FAILED")).toContain("bg-red-50");
    expect(render("PARTIAL")).not.toContain("bg-green-50");
    expect(render("FAILED")).not.toContain("bg-green-50");
    expect(render("COMPLETED")).toContain("bg-green-50");
  });

  it("never relies on color alone: every badge has an icon and a text label", () => {
    for (const [status, label] of STATES) {
      const markup = render(status);
      expect(markup).toContain('aria-hidden="true"');
      expect(markup).toContain(label);
    }
  });

  it("pulses only while a run is in progress", () => {
    expect(render("RUNNING")).toContain("animate-pulse");
    expect(render("COMPLETED")).not.toContain("animate-pulse");
  });

  it.each([null, undefined, "SOMETHING_NEW"])(
    "falls back to Not ingested for %s instead of crashing",
    (status) => {
      expect(statusLabel(status)).toBe("Not ingested");
      expect(render(status)).toContain("Not ingested");
    },
  );

  it("gives the sidebar dot a different color for each state", () => {
    const dots = STATES.map(([status]) => statusDotClass(status));
    expect(new Set(dots).size).toBe(STATES.length);
  });
});
