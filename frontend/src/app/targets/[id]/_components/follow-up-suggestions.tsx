"use client";

// S2-014, S2-015: under a refusal, which kind of refusal it was and questions to try instead.
// An answer shows nothing here. A chip asks its question through the same composer as the
// starter suggestions, so it goes down the same path as a typed question.
import { ThreadPrimitive } from "@assistant-ui/react";
import { ArrowRightIcon } from "lucide-react";
import type { AnswerResponse } from "@/lib/generated/api";
import { REFUSAL_REASON_TEXT } from "@/lib/refusal-reasons";

export function FollowUpSuggestions({ response }: { response: AnswerResponse }) {
  if (response.status === "ANSWERED") return null;
  const reason = response.refusal_reason ? REFUSAL_REASON_TEXT[response.refusal_reason] : null;
  const suggestions = response.suggestions ?? [];
  if (!reason && suggestions.length === 0) return null;

  return (
    <div className="flex flex-col gap-2">
      {reason && <p className="text-xs text-muted-foreground">{reason}</p>}
      {suggestions.length > 0 && (
        <>
          <p className="text-xs font-medium">You could ask instead</p>
          <ul className="flex flex-wrap gap-2">
            {suggestions.map((suggestion) => (
              <li key={suggestion}>
                <ThreadPrimitive.Suggestion
                  prompt={suggestion}
                  send
                  className="flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-left text-sm font-medium transition-colors outline-none hover:bg-muted focus-visible:ring-[3px] focus-visible:ring-ring/50"
                >
                  {suggestion}
                  <ArrowRightIcon className="size-3.5 shrink-0" aria-hidden="true" />
                </ThreadPrimitive.Suggestion>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
