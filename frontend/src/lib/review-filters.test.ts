// S3-016: the helpers behind the reviews filter form.
import { describe, expect, it } from "vitest";
import {
  describeFilters,
  filterParams,
  filterProblem,
  formatDay,
  hasFilters,
  NO_FILTERS,
} from "./review-filters";

describe("filterParams", () => {
  it("sends nothing when no filter is set", () => {
    expect(filterParams(NO_FILTERS, "America/New_York")).toEqual({});
  });

  it("sends the ratings as they are, without a timezone", () => {
    expect(filterParams({ minRating: 1, maxRating: 2 }, "America/New_York")).toEqual({
      min_rating: 1,
      max_rating: 2,
    });
  });

  it("sends the user's timezone with any date, so days match the review cards", () => {
    expect(filterParams({ startDate: "2026-02-28" }, "America/New_York")).toEqual({
      start_date: "2026-02-28",
      timezone: "America/New_York",
    });
    expect(filterParams({ endDate: "2026-03-01" }, "Asia/Tokyo")).toEqual({
      end_date: "2026-03-01",
      timezone: "Asia/Tokyo",
    });
  });
});

describe("filterProblem", () => {
  it.each([
    [{ minRating: 1, maxRating: 2 }],
    [{ minRating: 3, maxRating: 3 }],
    [{ startDate: "2026-03-01", endDate: "2026-03-01" }],
    [{ minRating: 4 }],
    [NO_FILTERS],
  ])("accepts %o", (filters) => {
    expect(filterProblem(filters)).toBeNull();
  });

  it("rejects a maximum rating below the minimum", () => {
    expect(filterProblem({ minRating: 4, maxRating: 2 })).toBe(
      "Maximum rating can't be lower than minimum.",
    );
  });

  it("rejects an end date before the start date", () => {
    expect(filterProblem({ startDate: "2026-03-01", endDate: "2026-02-01" })).toBe(
      "End date can't be before start date.",
    );
  });
});

describe("describeFilters", () => {
  it.each([
    [{ minRating: 1, maxRating: 2 }, "1–2 stars"],
    [{ minRating: 3, maxRating: 3 }, "3 stars"],
    [{ minRating: 4 }, "4 stars or more"],
    [{ maxRating: 1 }, "1 star or fewer"],
    [{ startDate: "2026-01-01" }, `From ${formatDay("2026-01-01")}`],
    [{ endDate: "2026-03-01" }, `Until ${formatDay("2026-03-01")}`],
    [
      { minRating: 4, startDate: "2026-01-01", endDate: "2026-03-01" },
      `4 stars or more · ${formatDay("2026-01-01")} – ${formatDay("2026-03-01")}`,
    ],
    [NO_FILTERS, ""],
  ])("describes %o as %s", (filters, text) => {
    expect(describeFilters(filters)).toBe(text);
  });
});

describe("formatDay", () => {
  it("shows the day itself, never the day before (not read as UTC midnight)", () => {
    expect(formatDay("2026-03-01")).toContain("1");
    expect(formatDay("2026-03-01")).toContain("2026");
    expect(formatDay("2026-03-01")).not.toContain("28");
  });
});

describe("hasFilters", () => {
  it("is false only when nothing is set", () => {
    expect(hasFilters(NO_FILTERS)).toBe(false);
    expect(hasFilters({ minRating: undefined })).toBe(false);
    expect(hasFilters({ endDate: "2026-03-01" })).toBe(true);
  });
});
