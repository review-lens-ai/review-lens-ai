// S2-009: the questions asked about one target, and asking a new one. S3-007: the
// conversation starts from the target's saved history (initialTurns), and every answered
// question is saved by the backend, so the cached history is refreshed after each one.
// The request is the generated useAskQuestion mutation (TanStack Query), so a 401 is
// handled for every request by the QueryProvider (which also clears cached data). Only
// the question is sent: no earlier turns (see src/lib/questions.ts).
//
// The turns live in React state, so they are cleared when the workspace remounts for
// another target (<TargetWorkspace key={id}>, S2-BR-014).
import { useCallback, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError, isUnauthorized } from "@/lib/api";
import { type AnswerResponse, useAskQuestion as useAskQuestionRequest } from "@/lib/generated/api";
import { qaHistoryQueryKey, type SavedQaExchange } from "@/lib/qa-history";
import { askFailureMessage } from "@/lib/questions";

// One question and what happened to it. The question stays with its result, so an
// answer is never shown under a question the user typed afterwards. askedAt is when it
// was asked (ISO 8601), for the day headings and times in the panel.
type Asked = { id: string; question: string; askedAt: string };
export type QaTurn =
  | (Asked & { state: "asking" })
  | (Asked & { state: "answered"; response: AnswerResponse })
  | (Asked & { state: "failed"; message: string });

// A saved question and answer, as a turn in the conversation.
export function turnOf(exchange: SavedQaExchange): QaTurn {
  return {
    id: exchange.id,
    question: exchange.question,
    askedAt: exchange.created_at,
    state: "answered",
    response: exchange.response,
  };
}

// The target is gone or not this user's. A 404 without this code (e.g. an endpoint
// not deployed yet) is a failed question, not a missing target.
function isTargetMissing(error: Error): boolean {
  return error instanceof ApiError && error.code === "NOT_FOUND";
}

export function useAskQuestion(
  targetId: string,
  { onTargetMissing, initialTurns = [] }: { onTargetMissing: () => void; initialTurns?: QaTurn[] },
) {
  // Only the first render's initialTurns count: the caller mounts this once the history
  // has loaded, and from then on the conversation here is the one on screen.
  const [turns, setTurns] = useState<QaTurn[]>(initialTurns);
  const nextId = useRef(0);
  const queryClient = useQueryClient();
  const { mutate, isPending } = useAskQuestionRequest();

  const ask = useCallback(
    (question: string) => {
      const id = `turn-${(nextId.current += 1)}`;
      const askedAt = new Date().toISOString();
      const settle = (turn: QaTurn | null) =>
        setTurns((all) => all.flatMap((t) => (t.id !== id ? [t] : turn ? [turn] : [])));

      setTurns((all) => [...all, { id, question, askedAt, state: "asking" }]);
      // These callbacks run only while this component is mounted, so an answer that
      // arrives after a switch to another target is dropped.
      mutate(
        { targetId, data: { question } },
        {
          onSuccess: (response) => {
            settle({ id, question, askedAt, state: "answered", response });
            // The backend saved this one: coming back to the target later shows it too.
            void queryClient.invalidateQueries({ queryKey: qaHistoryQueryKey(targetId) });
          },
          onError: (error) => {
            // A 401 is redirecting to /login, and NOT_FOUND shows the not-found page:
            // neither needs a failed turn.
            if (isUnauthorized(error)) return settle(null);
            if (isTargetMissing(error)) {
              settle(null);
              return onTargetMissing();
            }
            settle({ id, question, askedAt, state: "failed", message: askFailureMessage(error) });
          },
        },
      );
    },
    [mutate, targetId, onTargetMissing, queryClient],
  );

  // Asks a failed question again, in place of the failed turn.
  const retry = useCallback(
    (turn: QaTurn) => {
      setTurns((all) => all.filter((t) => t.id !== turn.id));
      ask(turn.question);
    },
    [ask],
  );

  // S3-008: empties the conversation after its saved history was cleared.
  const clear = useCallback(() => setTurns([]), []);

  return { turns, ask, retry, clear, asking: isPending };
}
