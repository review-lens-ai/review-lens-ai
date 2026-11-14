// S2-009: the questions asked about one target in this visit, and asking a new one.
// The request goes through TanStack Query, so a 401 is handled for every request by the
// QueryProvider (which also clears cached data). Only the question is sent: no earlier
// turns (see src/lib/questions.ts).
//
// The turns live in React state, so they are cleared when the workspace remounts for
// another target (<TargetWorkspace key={id}>, S2-BR-014).
import { useCallback, useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { ApiError, isUnauthorized } from "@/lib/api";
import { type AnswerResponse, askFailureMessage, askQuestion } from "@/lib/questions";

// One question and what happened to it. The question stays with its result, so an
// answer is never shown under a question the user typed afterwards.
export type QaTurn =
  | { id: string; question: string; state: "asking" }
  | { id: string; question: string; state: "answered"; response: AnswerResponse }
  | { id: string; question: string; state: "failed"; message: string };

// The target is gone or not this user's. A 404 without this code (e.g. an endpoint
// not deployed yet) is a failed question, not a missing target.
function isTargetMissing(error: Error): boolean {
  return error instanceof ApiError && error.code === "NOT_FOUND";
}

export function useAskQuestion(
  targetId: string,
  { onTargetMissing }: { onTargetMissing: () => void },
) {
  const [turns, setTurns] = useState<QaTurn[]>([]);
  const nextId = useRef(0);
  const { mutate, isPending } = useMutation({
    mutationFn: (question: string) => askQuestion(targetId, question),
  });

  const ask = useCallback(
    (question: string) => {
      const id = `turn-${(nextId.current += 1)}`;
      const settle = (turn: QaTurn | null) =>
        setTurns((all) => all.flatMap((t) => (t.id !== id ? [t] : turn ? [turn] : [])));

      setTurns((all) => [...all, { id, question, state: "asking" }]);
      // These callbacks run only while this component is mounted, so an answer that
      // arrives after a switch to another target is dropped.
      mutate(question, {
        onSuccess: (response) => settle({ id, question, state: "answered", response }),
        onError: (error) => {
          // A 401 is redirecting to /login, and NOT_FOUND shows the not-found page:
          // neither needs a failed turn.
          if (isUnauthorized(error)) return settle(null);
          if (isTargetMissing(error)) {
            settle(null);
            return onTargetMissing();
          }
          settle({ id, question, state: "failed", message: askFailureMessage(error) });
        },
      });
    },
    [mutate, onTargetMissing],
  );

  // Asks a failed question again, in place of the failed turn.
  const retry = useCallback(
    (turn: QaTurn) => {
      setTurns((all) => all.filter((t) => t.id !== turn.id));
      ask(turn.question);
    },
    [ask],
  );

  return { turns, ask, retry, asking: isPending };
}
