import { describe, expect, it } from "vitest";
import type { RefusalReason } from "@/lib/generated/api";
import { REFUSAL_REASON_TEXT } from "./refusal-reasons";

// Every reason the generated client knows. The type makes the type-check fail when the
// backend adds or removes a reason, so this list cannot drift. (The generated module
// is only imported as a type: its value needs the app's environment variables.)
const BACKEND_REASONS: Record<RefusalReason, true> = {
  GENERAL_KNOWLEDGE: true,
  OTHER_ENTITY: true,
  OTHER_LOCATION: true,
  OTHER_PLATFORM: true,
  NO_EVIDENCE: true,
};

describe("REFUSAL_REASON_TEXT", () => {
  it("has a line for every reason the backend can give, and no others", () => {
    expect(Object.keys(REFUSAL_REASON_TEXT).sort()).toEqual(Object.keys(BACKEND_REASONS).sort());
  });

  it("gives each reason its own plain sentence", () => {
    const lines = Object.values(REFUSAL_REASON_TEXT);

    expect(new Set(lines).size).toBe(lines.length);
    for (const line of lines) expect(line).toMatch(/\.$/);
  });
});
