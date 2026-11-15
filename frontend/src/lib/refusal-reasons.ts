// S2-014, S2-015: why ReviewLens declined a question, in words for the user.
//
// The reasons are the backend's RefusalReason, from the generated client.
import type { RefusalReason } from "@/lib/generated/api";

// One short line each. The answer itself already explains the refusal; this says which
// kind it was, so the user knows what to change.
export const REFUSAL_REASON_TEXT: Record<RefusalReason, string> = {
  GENERAL_KNOWLEDGE: "This isn't a question about these reviews.",
  OTHER_ENTITY: "ReviewLens only has reviews for this place.",
  OTHER_LOCATION: "These reviews are for one location only.",
  OTHER_PLATFORM: "ReviewLens only has reviews from this platform.",
  NO_EVIDENCE: "The reviews don't mention this.",
};
