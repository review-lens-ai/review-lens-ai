"use client";

// S2-009: the Q&A panel, docked beside the workspace tabs, for the active target only.
//
// assistant-ui draws the conversation; it does not send requests or keep messages.
// useAskQuestion owns both (ExternalStoreRuntime): it sends one question at a time
// through TanStack Query and keeps the turns, which start over on a target switch.
// Each question is answered on its own: no earlier turns are sent.
//
// S3-007: the conversation starts from the target's saved history, loaded the first time
// the panel opens. S3-008: Clear Q&A History removes that history, and nothing else.

import { useCallback, useEffect, useId, useMemo, useState } from "react";
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
  CheckIcon,
  CircleAlertIcon,
  CircleCheckIcon,
  ExternalLinkIcon,
  InboxIcon,
  Loader2Icon,
  RotateCcwIcon,
  SearchXIcon,
  SparklesIcon,
  XIcon,
} from "lucide-react";
import Link from "next/link";
import { ConfirmDialog } from "@/app/_components/confirm-dialog";
import { EmptyState } from "@/app/_components/empty-state";
import { ReviewCard } from "@/app/_components/review-card";
import { AskQuestionContext, FollowUpSuggestions } from "./follow-up-suggestions";
import { Button } from "@/app/_components/ui/button";
import { Skeleton } from "@/app/_components/ui/skeleton";
import type { AnswerResponse, AnswerStatus } from "@/lib/generated/api";
import { type QaTurn, turnOf, useAskQuestion } from "@/lib/hooks/use-ask-question";
import { useClearQaHistory, useQaHistory } from "@/lib/hooks/use-qa-history";
import { reviewCount } from "@/lib/ingestion-runs";
import { dayHeadings, questionCount, type SavedQaExchange, timeLabel } from "@/lib/qa-history";
import { noReviewsNotice, QUESTION_MAX_LENGTH, SUGGESTED_QUESTIONS } from "@/lib/questions";
import { persistedReviewsLabel } from "@/lib/reviews";
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

type PanelProps = {
  target: Target;
  // Saved reviews for this target, or null while they load.
  reviewTotal: number | null;
  onNotFound: () => void;
  onClose: () => void;
};

export function ReviewQaPanel({ open, ...props }: PanelProps & { open: boolean }) {
  const { target, onNotFound } = props;
  // The history loads the first time the panel opens, and stays while it is closed.
  const [opened, setOpened] = useState(open);
  if (open && !opened) setOpened(true);
  const history = useQaHistory(target.id, { enabled: opened });

  // The history as it first loaded (or failed to). The conversation starts from this
  // and is never swapped out afterwards: a later refetch (after each answer, or when the
  // browser tab regains focus) briefly empties the query while it runs, and showing the
  // loading state then would unmount the conversation and lose what is on screen.
  const [start, setStart] = useState<{ saved: SavedQaExchange[]; failed: boolean } | null>(null);
  if (start === null && (history.exchanges !== null || history.failed)) {
    setStart({ saved: history.exchanges ?? [], failed: history.failed });
  }

  useEffect(() => {
    if (history.targetMissing) onNotFound();
  }, [history.targetMissing, onNotFound]);

  if (start === null) {
    return (
      <PanelFrame {...props} subtitle="Loading your questions…">
        <div role="status" aria-label="Loading your questions" className="flex flex-col gap-3 p-5">
          <Skeleton className="ml-auto h-12 w-3/4 rounded-xl" />
          <Skeleton className="h-20 w-full rounded-xl" />
        </div>
      </PanelFrame>
    );
  }

  return <QaConversation {...props} saved={start.saved} historyFailed={start.failed} />;
}

// The panel around the conversation. The dataset answers come from, always in view
// (S2-BR-014).
function PanelFrame({
  target,
  onClose,
  subtitle,
  status,
  children,
}: Pick<PanelProps, "target" | "onClose"> & {
  subtitle: string;
  status?: React.ReactNode;
  children: React.ReactNode;
}) {
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
      <div className="flex items-start justify-between gap-3 border-b px-5 py-4">
        <div className="min-w-0">
          <h2 id={headingId} className="font-semibold break-words">
            Ask {target.name}
          </h2>
          <p className="text-sm text-muted-foreground">{subtitle}</p>
        </div>
        <div className="flex items-center gap-2">
          {status}
          <Button variant="ghost" size="icon" aria-label="Close Q&A" onClick={onClose}>
            <XIcon aria-hidden="true" />
          </Button>
        </div>
      </div>
      {children}
    </section>
  );
}

function QaConversation({
  target,
  reviewTotal,
  onNotFound,
  onClose,
  saved,
  historyFailed,
}: PanelProps & { saved: SavedQaExchange[]; historyFailed: boolean }) {
  const initialTurns = useMemo(() => saved.map(turnOf), [saved]);
  const { turns, ask, retry, clear, asking } = useAskQuestion(target.id, {
    onTargetMissing: onNotFound,
    initialTurns,
  });
  const messages = useMemo(() => messagesOf(turns), [turns]);
  const headings = useMemo(() => dayHeadings(turns), [turns]);

  // S3-008: true once the history was cleared in this visit.
  const [cleared, setCleared] = useState(false);
  const [clearOpen, setClearOpen] = useState(false);
  const clearing = useClearQaHistory(target.id, {
    onCleared: () => {
      clear();
      setCleared(true);
      setClearOpen(false);
    },
    onTargetMissing: onNotFound,
  });

  const runtime = useExternalStoreRuntime({
    messages,
    convertMessage,
    isRunning: asking,
    onNew: async (message) => {
      const question = textOf(message);
      if (question && !asking) ask(question);
    },
  });

  const reviews = reviewTotal === null ? "Loading reviews…" : reviewCount(reviewTotal);
  const subtitle = `${questionCount(turns.length)} · ${reviews} · ${platformLabel(target.platform)}`;
  let status: React.ReactNode = null;
  if (saved.length > 0 && !cleared && turns.length > 0) {
    status = <span className="text-xs font-medium text-success">History restored</span>;
  } else if (cleared && turns.length === 0) {
    status = <span className="text-xs text-muted-foreground">Ready for a new question</span>;
  }

  return (
    <PanelFrame target={target} onClose={onClose} subtitle={subtitle} status={status}>
      {historyFailed && (
        <p role="status" className="border-b bg-warning/10 px-5 py-2 text-sm text-warning">
          Your earlier questions couldn&apos;t load. You can still ask new ones.
        </p>
      )}
      {reviewTotal === 0 && turns.length === 0 ? (
        <div className="flex-1 overflow-y-auto p-5">
          <EmptyState icon={<InboxIcon />} {...noReviewsNotice(target.ingestion_status)} />
        </div>
      ) : (
        <AssistantRuntimeProvider runtime={runtime}>
          <QaThread
            target={target}
            reviewTotal={reviewTotal}
            messages={messages}
            headings={headings}
            cleared={cleared}
            onRetry={retry}
            onClear={() => {
              clearing.reset();
              setClearOpen(true);
            }}
          />
        </AssistantRuntimeProvider>
      )}
      <ConfirmDialog
        open={clearOpen}
        onOpenChange={setClearOpen}
        title="Clear Q&A history?"
        description={clearDescription(turns.length, target.name)}
        confirmLabel="Clear Q&A History"
        pendingLabel="Clearing…"
        pending={clearing.clearing}
        error={clearing.error}
        onConfirm={clearing.clear}
      >
        <KeptDataList target={target} reviewTotal={reviewTotal} />
        <p className="text-sm text-muted-foreground">
          Other analyses and their Q&amp;A histories are not affected.
        </p>
      </ConfirmDialog>
    </PanelFrame>
  );
}

function clearDescription(questions: number, targetName: string): string {
  const what =
    questions === 1
      ? "the saved question and answer"
      : `the ${questions} saved questions and answers`;
  return `Permanently remove ${what} for ${targetName}. This cannot be undone.`;
}

// S3-008: what clearing leaves alone (S3-BR-012).
function KeptDataList({ target, reviewTotal }: { target: Target; reviewTotal: number | null }) {
  const kept = [
    `Analysis: ${target.name}`,
    `${platformLabel(target.platform)} source URL`,
    persistedReviewsLabel(reviewTotal),
    "Ingestion summary",
  ].filter((item) => item !== null);

  return (
    <div className="rounded-md bg-success/10 px-4 py-3 text-sm text-success">
      <p className="font-medium">Your analysis and review data stay saved</p>
      <ul className="mt-2 flex flex-col gap-1">
        {kept.map((item) => (
          <li key={item} className="flex items-center gap-2">
            <CheckIcon className="size-4 shrink-0" aria-hidden="true" />
            {item}
          </li>
        ))}
      </ul>
    </div>
  );
}

function QaThread({
  target,
  reviewTotal,
  messages,
  headings,
  cleared,
  onRetry,
  onClear,
}: {
  target: Target;
  reviewTotal: number | null;
  messages: QaMessage[];
  // The day heading above the first question of each day, by turn id.
  headings: Map<string, string>;
  cleared: boolean;
  onRetry: (turn: QaTurn) => void;
  onClear: () => void;
}) {
  const inputId = useId();
  const aui = useAui();
  const asking = useAuiState((state) => state.thread.isRunning);
  const length = useAuiState((state) => state.composer.text.length);
  const turnFor = useCallback((id: string) => messages.find((m) => m.id === id)?.turn, [messages]);

  // The conversation appears once the history has loaded, just after the panel opens, so
  // the cursor goes to the question box then.
  useEffect(() => {
    document.getElementById(inputId)?.focus();
  }, [inputId]);

  // Puts a question back in the box, to change it before asking again.
  const edit = useCallback(
    (question: string) => {
      aui.composer.setText(question);
      document.getElementById(inputId)?.focus();
    },
    [aui, inputId],
  );

  // Asks a follow-up chip's question, unless an answer is still on its way.
  const ask = useCallback(
    (question: string) => {
      if (asking) return;
      aui.thread.append({ content: [{ type: "text", text: question }] });
    },
    [aui, asking],
  );

  return (
    <ThreadPrimitive.Root className="flex min-h-0 flex-1 flex-col">
      {/* Announces each new reply to screen readers (§10). */}
      <ThreadPrimitive.Viewport
        aria-live="polite"
        className="flex min-h-0 flex-1 flex-col gap-5 overflow-y-auto p-5"
      >
        <ThreadPrimitive.Empty>
          {cleared && <ClearedNotice reviewTotal={reviewTotal} />}
          <Suggestions cleared={cleared} targetName={target.name} />
        </ThreadPrimitive.Empty>
        <AskQuestionContext.Provider value={ask}>
          <ThreadPrimitive.Messages>
            {({ message }) => {
              const turn = turnFor(message.id);
              if (!turn) return null;
              if (message.role === "user") {
                return <AskedQuestion turn={turn} heading={headings.get(turn.id)} />;
              }
              return <Reply turn={turn} targetName={target.name} onRetry={onRetry} onEdit={edit} />;
            }}
          </ThreadPrimitive.Messages>
        </AskQuestionContext.Provider>
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
          <div className="ml-auto flex flex-wrap items-center gap-2">
            {/* S3-008: type="button", so it never submits the question box. Off while an
                answer is on its way, and when there is nothing to clear. */}
            <Button
              type="button"
              variant="outline"
              onClick={onClear}
              disabled={asking || messages.length === 0}
            >
              Clear Q&amp;A History
            </Button>
            <ComposerPrimitive.Send asChild>
              <Button type="submit">
                {asking ? (
                  <Loader2Icon className="animate-spin" aria-hidden="true" />
                ) : (
                  <ArrowUpIcon aria-hidden="true" />
                )}
                {asking ? "Analyzing…" : "Ask ReviewLens"}
              </Button>
            </ComposerPrimitive.Send>
          </div>
        </div>
        <p className="text-xs text-muted-foreground">
          Each question is answered on its own, using only {target.name}&apos;s reviews.
        </p>
      </ComposerPrimitive.Root>
    </ThreadPrimitive.Root>
  );
}

// S3-008: after clearing, says what was kept (S3-BR-012).
function ClearedNotice({ reviewTotal }: { reviewTotal: number | null }) {
  const reviews = reviewTotal === null ? "saved reviews" : `${reviewCount(reviewTotal)}`;
  return (
    <div role="status" className="rounded-lg bg-success/10 px-4 py-3 text-sm text-success">
      <p className="font-medium">Q&amp;A history cleared</p>
      <p>Your analysis, source URL, {reviews} and ingestion summary are unchanged.</p>
    </div>
  );
}

function Suggestions({ cleared, targetName }: { cleared: boolean; targetName: string }) {
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-start gap-3">
        <span className="rounded-lg bg-primary/10 p-2 text-primary" aria-hidden="true">
          <SparklesIcon className="size-5" />
        </span>
        <div>
          <h3 className="font-semibold">
            {cleared ? "A fresh start, same reviews." : "Your next insight starts with a question."}
          </h3>
          <p className="text-sm text-muted-foreground">
            {cleared
              ? `Ask a new question about ${targetName}. Answers use only its reviews.`
              : "Start with a suggestion, or ask something of your own."}
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

// The user's question, with the day heading above it when it is the first of its day
// (S3-007), and the time it was asked.
function AskedQuestion({ turn, heading }: { turn: QaTurn; heading?: string }) {
  return (
    <>
      {heading && (
        <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
          {heading}
        </p>
      )}
      <div className="ml-auto max-w-[85%] rounded-xl bg-primary px-4 py-3 text-sm whitespace-pre-line text-primary-foreground">
        <p>
          <span className="sr-only">You asked: </span>
          {turn.question}
        </p>
        <time dateTime={turn.askedAt} className="mt-1 block text-xs text-primary-foreground/80">
          {timeLabel(turn.askedAt)}
        </time>
      </div>
    </>
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
      <FollowUpSuggestions response={response} />
      {response.evidence.length > 0 && (
        <details className="group flex flex-col gap-3">
          <summary className="w-fit cursor-pointer text-sm font-medium text-primary hover:underline">
            Evidence: {reviewCount(response.evidence.length)}
          </summary>
          <ul className="mt-3 flex flex-col gap-3">
            {response.evidence.map((review) => (
              <li key={review.id} className="flex flex-col gap-1">
                <ReviewCard review={review} anchor={false} />
                {/* Opens the review in the Reviews tab, on its page, highlighted. */}
                <Link
                  href={`/targets/${response.context.target_id}?review=${review.id}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex w-fit items-center gap-1 text-xs font-medium text-primary hover:underline"
                >
                  Open in Reviews
                  <ExternalLinkIcon className="size-3" aria-hidden="true" />
                </Link>
              </li>
            ))}
          </ul>
        </details>
      )}
    </article>
  );
}
