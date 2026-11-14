"use client";

// S2-009: the Q&A panel, docked beside the workspace tabs, for the active target only.
//
// assistant-ui draws the conversation; it does not send requests or keep messages.
// useAskQuestion owns both (ExternalStoreRuntime): it sends one question at a time
// through TanStack Query and keeps the turns, which are cleared on a target switch.
// Each question is answered on its own: no earlier turns are sent.

import { useCallback, useId, useMemo } from "react";
import {
  type AppendMessage,
  AssistantRuntimeProvider,
  ComposerPrimitive,
  type ThreadMessageLike,
  ThreadPrimitive,
  useAui,
  useAuiState,
  useExternalStoreRuntime,
} from "@assistant-ui/react";
import {
  ArrowRightIcon,
  ArrowUpIcon,
  BanIcon,
  CircleAlertIcon,
  CircleCheckIcon,
  InboxIcon,
  Loader2Icon,
  RotateCcwIcon,
  SearchXIcon,
  SparklesIcon,
  XIcon,
} from "lucide-react";
import { EmptyState } from "@/app/_components/empty-state";
import { ReviewCard } from "@/app/_components/review-card";
import { Button } from "@/app/_components/ui/button";
import { Skeleton } from "@/app/_components/ui/skeleton";
import { type QaTurn, useAskQuestion } from "@/lib/hooks/use-ask-question";
import { reviewCount } from "@/lib/ingestion-runs";
import {
  type AnswerResponse,
  type AnswerStatus,
  QUESTION_MAX_LENGTH,
  SUGGESTED_QUESTIONS,
} from "@/lib/questions";
import { platformLabel, type Target } from "@/lib/targets";

// What assistant-ui is given: each turn becomes the user's question and ReviewLens's reply.
type QaMessage = { id: string; role: "user" | "assistant"; turn: QaTurn };

function messagesOf(turns: QaTurn[]): QaMessage[] {
  return turns.flatMap((turn) => [
    { id: `${turn.id}-question`, role: "user", turn },
    { id: `${turn.id}-reply`, role: "assistant", turn },
  ]);
}

function convertMessage({ id, role, turn }: QaMessage): ThreadMessageLike {
  if (role === "user") return { id, role, content: turn.question };
  if (turn.state === "answered") return { id, role, content: turn.response.answer };
  if (turn.state === "failed") return { id, role, content: turn.message };
  return { id, role, content: "" };
}

// The question the user sent, as plain text.
function textOf(message: AppendMessage): string {
  return message.content
    .map((part) => (part.type === "text" ? part.text : ""))
    .join("\n")
    .trim();
}

export function ReviewQaPanel({
  target,
  reviewTotal,
  onNotFound,
  onClose,
}: {
  target: Target;
  // Saved reviews for this target, or null while they load.
  reviewTotal: number | null;
  onNotFound: () => void;
  onClose: () => void;
}) {
  const { turns, ask, retry, asking } = useAskQuestion(target.id, {
    onTargetMissing: onNotFound,
  });
  const messages = useMemo(() => messagesOf(turns), [turns]);

  const runtime = useExternalStoreRuntime({
    messages,
    convertMessage,
    isRunning: asking,
    onNew: async (message) => {
      const question = textOf(message);
      if (question && !asking) ask(question);
    },
  });

  const headingId = useId();

  return (
    <section
      aria-labelledby={headingId}
      // Esc closes the panel, as ✕ does.
      onKeyDown={(e) => {
        if (e.key === "Escape") onClose();
      }}
      className="flex h-full min-h-0 flex-col overflow-hidden rounded-xl border bg-card"
    >
      {/* The dataset answers come from, always in view (S2-BR-014). */}
      <div className="flex items-start justify-between gap-3 border-b px-5 py-4">
        <div className="min-w-0">
          <h2 id={headingId} className="font-semibold break-words">
            Ask {target.name}
          </h2>
          <p className="text-sm text-muted-foreground">
            {reviewTotal === null ? "Loading reviews…" : reviewCount(reviewTotal)} ·{" "}
            {platformLabel(target.platform)}
          </p>
        </div>
        <Button variant="ghost" size="icon" aria-label="Close Q&A" onClick={onClose}>
          <XIcon aria-hidden="true" />
        </Button>
      </div>

      {reviewTotal === 0 ? (
        <div className="flex-1 overflow-y-auto p-5">
          <EmptyState
            icon={<InboxIcon />}
            title="No reviews to ask about yet"
            message="ReviewLens answers only from this target's saved reviews. Use Ingest reviews above to collect them, then ask your question here."
          />
        </div>
      ) : (
        <AssistantRuntimeProvider runtime={runtime}>
          <QaThread target={target} messages={messages} onRetry={retry} />
        </AssistantRuntimeProvider>
      )}
    </section>
  );
}

function QaThread({
  target,
  messages,
  onRetry,
}: {
  target: Target;
  messages: QaMessage[];
  onRetry: (turn: QaTurn) => void;
}) {
  const inputId = useId();
  const aui = useAui();
  const asking = useAuiState((state) => state.thread.isRunning);
  const length = useAuiState((state) => state.composer.text.length);
  const turnOf = useCallback((id: string) => messages.find((m) => m.id === id)?.turn, [messages]);

  // Puts a question back in the box, to change it before asking again.
  const edit = useCallback(
    (question: string) => {
      aui.composer.setText(question);
      document.getElementById(inputId)?.focus();
    },
    [aui, inputId],
  );

  return (
    <ThreadPrimitive.Root className="flex min-h-0 flex-1 flex-col">
      {/* Announces each new reply to screen readers (§10). */}
      <ThreadPrimitive.Viewport
        aria-live="polite"
        className="flex min-h-0 flex-1 flex-col gap-5 overflow-y-auto p-5"
      >
        <ThreadPrimitive.Empty>
          <Suggestions />
        </ThreadPrimitive.Empty>
        <ThreadPrimitive.Messages>
          {({ message }) => {
            const turn = turnOf(message.id);
            if (!turn) return null;
            if (message.role === "user") return <AskedQuestion question={turn.question} />;
            return <Reply turn={turn} targetName={target.name} onRetry={onRetry} onEdit={edit} />;
          }}
        </ThreadPrimitive.Messages>
      </ThreadPrimitive.Viewport>

      <ComposerPrimitive.Root className="flex flex-col gap-3 border-t p-4">
        <div className="flex items-baseline justify-between gap-2">
          <label htmlFor={inputId} className="text-sm font-medium">
            Your question
          </label>
          <span className="text-xs text-muted-foreground tabular-nums" aria-hidden="true">
            {length.toLocaleString()} / {QUESTION_MAX_LENGTH.toLocaleString()}
          </span>
        </div>
        <ComposerPrimitive.Input
          id={inputId}
          // The box stops at the limit, so a question is never too long to send.
          maxLength={QUESTION_MAX_LENGTH}
          minRows={2}
          maxRows={6}
          placeholder="What would you like to know about these reviews?"
          className="w-full resize-none rounded-md border border-input bg-transparent px-3 py-2 text-base shadow-xs outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 md:text-sm"
        />
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-xs text-muted-foreground">
            {asking
              ? "Your question is being processed."
              : "Press Enter to ask, Shift+Enter for a new line."}
          </p>
          <ComposerPrimitive.Send asChild>
            <Button type="submit" className="ml-auto">
              {asking ? (
                <Loader2Icon className="animate-spin" aria-hidden="true" />
              ) : (
                <ArrowUpIcon aria-hidden="true" />
              )}
              {asking ? "Analyzing…" : "Ask ReviewLens"}
            </Button>
          </ComposerPrimitive.Send>
        </div>
        <p className="text-xs text-muted-foreground">
          Each question is answered on its own, using only {target.name}&apos;s reviews.
        </p>
      </ComposerPrimitive.Root>
    </ThreadPrimitive.Root>
  );
}

function Suggestions() {
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-start gap-3">
        <span className="rounded-lg bg-primary/10 p-2 text-primary" aria-hidden="true">
          <SparklesIcon className="size-5" />
        </span>
        <div>
          <h3 className="font-semibold">Your next insight starts with a question.</h3>
          <p className="text-sm text-muted-foreground">
            Start with a suggestion, or ask something of your own.
          </p>
        </div>
      </div>
      <ul className="flex flex-col gap-2">
        {SUGGESTED_QUESTIONS.map((suggestion) => (
          <li key={suggestion}>
            <ThreadPrimitive.Suggestion
              prompt={suggestion}
              send
              className="flex w-full items-center justify-between gap-2 rounded-lg border px-4 py-3 text-left text-sm font-medium transition-colors outline-none hover:bg-muted focus-visible:ring-[3px] focus-visible:ring-ring/50"
            >
              {suggestion}
              <ArrowRightIcon className="size-4 shrink-0" aria-hidden="true" />
            </ThreadPrimitive.Suggestion>
          </li>
        ))}
      </ul>
    </div>
  );
}

function AskedQuestion({ question }: { question: string }) {
  return (
    <div className="ml-auto max-w-[85%] rounded-xl bg-primary px-4 py-3 text-sm whitespace-pre-line text-primary-foreground">
      <span className="sr-only">You asked: </span>
      {question}
    </div>
  );
}

function Reply({
  turn,
  targetName,
  onRetry,
  onEdit,
}: {
  turn: QaTurn;
  targetName: string;
  onRetry: (turn: QaTurn) => void;
  onEdit: (question: string) => void;
}) {
  const failedId = useId();

  switch (turn.state) {
    case "asking":
      return (
        <div
          role="status"
          aria-label="ReviewLens is reading the reviews"
          className="flex flex-col gap-3"
        >
          <p className="flex items-center gap-2 text-sm font-medium">
            <Loader2Icon className="size-4 animate-spin text-primary" aria-hidden="true" />
            ReviewLens is reading the reviews
          </p>
          <p className="text-sm text-muted-foreground">
            Finding relevant feedback for {targetName}…
          </p>
          <div className="flex flex-col gap-3" aria-hidden="true">
            <Skeleton className="h-2.5 w-11/12" />
            <Skeleton className="h-2.5 w-4/5" />
            <Skeleton className="h-2.5 w-3/5" />
          </div>
        </div>
      );

    case "answered":
      return <Answer response={turn.response} />;

    case "failed":
      return (
        <div
          role="alert"
          aria-labelledby={failedId}
          className="flex flex-col gap-3 rounded-xl border border-destructive/30 bg-destructive/5 p-5"
        >
          <h3 id={failedId} className="flex items-center gap-2 font-semibold text-destructive">
            <CircleAlertIcon className="size-5" aria-hidden="true" />
            ReviewLens couldn&apos;t answer this question
          </h3>
          <p className="text-sm">
            {turn.message} Your reviews haven&apos;t changed. Try again in a moment. If this keeps
            happening, please contact support.
          </p>
          <div className="flex flex-wrap items-center gap-3">
            <Button variant="outline" onClick={() => onRetry(turn)}>
              <RotateCcwIcon aria-hidden="true" />
              Try again
            </Button>
            <Button
              variant="link"
              className="px-0 text-muted-foreground"
              onClick={() => onEdit(turn.question)}
            >
              Edit your question
            </Button>
          </div>
        </div>
      );
  }
}

// How each kind of reply is labelled, so a declined or unsupported question never
// looks like a real answer (S2-BR-021).
const STATUS_VIEW: Record<
  AnswerStatus,
  { label: string; icon: typeof CircleCheckIcon; className: string }
> = {
  ANSWERED: { label: "Answered", icon: CircleCheckIcon, className: "bg-success/10 text-success" },
  INSUFFICIENT_EVIDENCE: {
    label: "Not enough evidence",
    icon: SearchXIcon,
    className: "bg-warning/10 text-warning",
  },
  OUT_OF_SCOPE: {
    label: "Outside these reviews",
    icon: BanIcon,
    className: "bg-muted text-muted-foreground",
  },
  NO_REVIEWS: {
    label: "No reviews yet",
    icon: InboxIcon,
    className: "bg-muted text-muted-foreground",
  },
};

function Answer({ response }: { response: AnswerResponse }) {
  const view = STATUS_VIEW[response.status];
  const Icon = view.icon;
  const { reviews_considered, reviews_total, is_subset } = response.context;

  return (
    <article aria-label="ReviewLens response" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p
          className={`flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ${view.className}`}
        >
          <Icon className="size-3.5" aria-hidden="true" />
          {view.label}
        </p>
        {response.status === "ANSWERED" && (
          <span className="text-xs text-muted-foreground">
            {is_subset
              ? `Based on ${reviews_considered.toLocaleString()} of ${reviewCount(reviews_total)}`
              : `Based on ${reviewCount(reviews_total)}`}
          </span>
        )}
      </div>
      <p className="text-sm leading-relaxed whitespace-pre-line">{response.answer}</p>
      {response.evidence.length > 0 && (
        <details className="group flex flex-col gap-3">
          <summary className="w-fit cursor-pointer text-sm font-medium text-primary hover:underline">
            Evidence: {reviewCount(response.evidence.length)}
          </summary>
          <ul className="mt-3 flex flex-col gap-3">
            {response.evidence.map((review) => (
              <li key={review.id}>
                <ReviewCard review={review} anchor={false} />
              </li>
            ))}
          </ul>
        </details>
      )}
    </article>
  );
}
