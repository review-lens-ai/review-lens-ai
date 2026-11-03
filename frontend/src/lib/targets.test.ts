import { describe, expect, it } from "vitest";
import { fixFieldsMessage } from "./targets";

describe("fixFieldsMessage", () => {
  it("uses the singular for one field", () => {
    expect(fixFieldsMessage(1)).toBe("Fix 1 field to continue.");
  });

  it("uses the plural for several fields", () => {
    expect(fixFieldsMessage(2)).toBe("Fix 2 fields to continue.");
  });
});
