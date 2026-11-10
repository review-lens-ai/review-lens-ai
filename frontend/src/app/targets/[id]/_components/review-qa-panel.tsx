"use client";

// S2-009: the Review Q&A tab. One question, one answer, for the active target only.
// No chat history: a new question replaces the last answer.

import { useEffect, useId, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ArrowRightIcon,
  ArrowUpIcon,
  CircleAlertIcon,
  CircleCheckIcon,
  InboxIcon,
  Loader2Icon,
  MessageSquareTextIcon,
  RotateCcwIcon,
  SparklesIcon,
} from "lucide-react";
import { EmptyState } from "@/app/_components/empty-state";
import { Button } from "@/app/_components/ui/button";
import { Skeleton } from "@/app/_components/ui/skeleton";
import { ApiError } from "@/lib/api";
import { reviewCount } from "@/lib/ingestion-runs";
import {
  askFailureMessage,
  askQuestion,
  QUESTION_MAX_LENGTH,
  questionProblem,
  SUGGESTED_QUESTIONS,
} from "@/lib/questions";
import { platformLabel, type Target } from "@/lib/targets";

// Each result keeps the question it belongs to, so an answer is never shown
// under a question the user typed afterwards.
type Exchange =
  | { status: "idle" }
  | { status: "asking"; question: string }
  | { status: "answered"; question: string; answer: string }
  | { status: "failed"; question: string; message: string };

export function ReviewQaPanel({
  target,
  reviewTotal,
  onNotFound,
}: {
  target: Target;
  // Saved reviews for this target, or null while they load.
  reviewTotal: number | null;
  onNotFound: () => void;
}) {
  const router = useRouter();
  const [question, setQuestion] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [exchange, setExchange] = useState<Exchange>({ status: "idle" });
  // Counts requests, so a late answer never replaces a newer one or lands after unmount.
  const request = useRef(0);
  useEffect(() => {
    const current = request;
    return () => {
      current.current += 1;
    };
  }, []);

  const inputId = useId();
  const helpId = useId();
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const asking = exchange.status === "asking";

  if (reviewTotal === 0) {
    return (
      <EmptyState
        icon={<InboxIcon />}
        title="No reviews to ask about yet"
        message="ReviewLens answers only from this target's saved reviews. Use Ingest reviews above to collect them, then ask your question here."
      />
    );
  }

  async function ask(text: string) {
    const found = questionProblem(text);
    setProblem(found);
    if (found) {
      inputRef.current?.focus();
      return;
    }
    const mine = ++request.current;
    setExchange({ status: "asking", question: text });
    try {
      const { answer } = await askQuestion(target.id, text);
      if (mine === request.current) setExchange({ status: "answered", question: text, answer });
    } catch (e) {
      if (mine !== request.current) return;
      if (e instanceof ApiError && e.status === 401) {
        router.replace("/login");
      } else if (e instanceof ApiError && e.code === "NOT_FOUND") {
        // The target is gone or not this user's. A 404 without this code (e.g. an
        // endpoint not deployed yet) is a failed question, not a missing target.
        onNotFound();
      } else {
        // The question stays in the box, so the user can try again or edit it (§6.3).
        setExchange({ status: "failed", question: text, message: askFailureMessage(e) });
      }
    }
  }

  function askSuggestion(text: string) {
    setQuestion(text);
    void ask(text);
  }

  let help = "Ask in your own words. Be as specific as you like.";
  if (asking) help = "Your question is being processed.";
  else if (question.trim() === "") help = "Enter a question to ask ReviewLens.";

  return (
    <section aria-labelledby={`${inputId}-heading`} className="flex flex-col gap-6">
      <div className="flex flex-col gap-1">
        <h2 id={`${inputId}-heading`} className="text-xl font-semibold">
          Ask your reviews
        </h2>
        <p className="text-sm text-muted-foreground">
          Get a focused answer grounded in {target.name}&apos;s customer reviews.
        </p>
      </div>

      <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_280px]">
        <div className="flex min-w-0 flex-col gap-6">
          <form
            className="flex flex-col gap-3 rounded-xl border bg-card p-6"
            noValidate
            onSubmit={(e) => {
              e.preventDefault();
              if (!asking) void ask(question);
            }}
          >
            <div className="flex items-baseline justify-between gap-2">
              <label htmlFor={inputId} className="text-sm font-medium">
                Your question
              </label>
              <span className="text-xs text-muted-foreground tabular-nums" aria-hidden="true">
                {question.length.toLocaleString()} / {QUESTION_MAX_LENGTH.toLocaleString()}
              </span>
            </div>
            <textarea
              id={inputId}
              ref={inputRef}
              value={question}
              onChange={(e) => {
                setQuestion(e.target.value);
                if (problem) setProblem(null);
              }}
              onKeyDown={(e) => {
                // Cmd/Ctrl+Enter asks; plain Enter adds a new line.
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                  e.preventDefault();
                  e.currentTarget.form?.requestSubmit();
                }
              }}
              readOnly={asking}
              maxLength={QUESTION_MAX_LENGTH}
              rows={3}
              placeholder="What would you like to know about these reviews?"
              aria-invalid={problem ? true : undefined}
              aria-describedby={helpId}
              className="min-h-24 w-full resize-y rounded-md border border-input bg-transparent px-3 py-2 text-base shadow-xs outline-none placeholder:text-muted-foreground read-only:bg-muted/50 focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 aria-invalid:border-destructive aria-invalid:ring-destructive/20 md:text-sm"
            />
            <div className="flex flex-wrap items-center justify-between gap-3">
              {problem ? (
                <p id={helpId} className="flex items-center gap-1 text-sm text-destructive">
                  <CircleAlertIcon className="size-4" aria-hidden="true" />
                  {problem}
                </p>
              ) : (
                <p id={helpId} className="text-xs text-muted-foreground">
                  {help}
                </p>
              )}
              <Button type="submit" disabled={asking} className="ml-auto">
                {asking ? (
                  <Loader2Icon className="animate-spin" aria-hidden="true" />
                ) : (
                  <ArrowUpIcon aria-hidden="true" />
                )}
                {asking ? "Analyzing…" : "Ask ReviewLens"}
              </Button>
            </div>
          </form>

          {/* Announces each new state to screen readers (§10). */}
          <div aria-live="polite">
            <ExchangeResult
              exchange={exchange}
              idPrefix={inputId}
              targetName={target.name}
              onSuggestion={askSuggestion}
              onRetry={(text) => {
                setQuestion(text);
                void ask(text);
              }}
              onEdit={() => inputRef.current?.focus()}
            />
          </div>
        </div>

        <AnswerContext target={target} reviewTotal={reviewTotal} />
      </div>
    </section>
  );
}

function ExchangeResult({
  exchange,
  idPrefix,
  targetName,
  onSuggestion,
  onRetry,
  onEdit,
}: {
  exchange: Exchange;
  idPrefix: string;
  targetName: string;
  onSuggestion: (question: string) => void;
  onRetry: (question: string) => void;
  onEdit: () => void;
}) {
  switch (exchange.status) {
    case "idle":
      return (
        <div className="flex flex-col gap-4 rounded-xl border bg-card p-6">
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
                <button
                  type="button"
                  onClick={() => onSuggestion(suggestion)}
                  className="flex w-full items-center justify-between gap-2 rounded-lg border px-4 py-3 text-left text-sm font-medium transition-colors outline-none hover:bg-muted focus-visible:ring-[3px] focus-visible:ring-ring/50"
                >
                  {suggestion}
                  <ArrowRightIcon className="size-4 shrink-0" aria-hidden="true" />
                </button>
              </li>
            ))}
          </ul>
        </div>
      );

    case "asking":
      return (
        <div
          role="status"
          aria-label="ReviewLens is reading the reviews"
          className="flex flex-col gap-4 rounded-xl border bg-card p-6"
        >
          <div className="flex items-start gap-3">
            <Loader2Icon className="mt-0.5 size-5 animate-spin text-primary" aria-hidden="true" />
            <div>
              <h3 className="font-semibold">ReviewLens is reading the reviews</h3>
              <p className="text-sm text-muted-foreground">
                Finding relevant feedback for {targetName}…
              </p>
            </div>
          </div>
          <div className="flex flex-col gap-3" aria-hidden="true">
            <Skeleton className="h-2.5 w-11/12" />
            <Skeleton className="h-2.5 w-4/5" />
            <Skeleton className="h-2.5 w-3/5" />
          </div>
          <p className="text-xs text-muted-foreground">
            This may take a few moments. Your question will stay here.
          </p>
        </div>
      );

    case "answered":
      return (
        <div className="flex flex-col gap-3">
          <article
            aria-label="ReviewLens response"
            className="flex flex-col gap-4 rounded-xl border bg-card p-6"
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 className="flex items-center gap-2 font-semibold">
                <SparklesIcon className="size-4 text-primary" aria-hidden="true" />
                ReviewLens response
              </h3>
              <span className="text-xs text-muted-foreground">
                Based on {targetName}&apos;s reviews
              </span>
            </div>
            {/* The question this answer belongs to, in case the box has changed since. */}
            <p className="text-sm text-muted-foreground">
              You asked: <span className="text-foreground">{exchange.question}</span>
            </p>
            <p className="text-sm leading-relaxed whitespace-pre-line">{exchange.answer}</p>
          </article>
          <p className="text-xs text-muted-foreground">
            AI-generated answer. Check the source reviews before acting on it. Ask another question
            above.
          </p>
        </div>
      );

    case "failed":
      return (
        <div
          role="alert"
          aria-labelledby={`${idPrefix}-failed`}
          className="flex flex-col gap-3 rounded-xl border border-destructive/30 bg-destructive/5 p-6"
        >
          <h3
            id={`${idPrefix}-failed`}
            className="flex items-center gap-2 font-semibold text-destructive"
          >
            <CircleAlertIcon className="size-5" aria-hidden="true" />
            ReviewLens couldn&apos;t answer this question
          </h3>
          <p className="text-sm">
            {exchange.message} Your question is still here, and your reviews haven&apos;t changed.
          </p>
          <p className="text-xs text-muted-foreground">
            Try again in a moment. If this keeps happening, please contact support.
          </p>
          <div className="flex flex-wrap items-center gap-3">
            <Button variant="outline" onClick={() => onRetry(exchange.question)}>
              <RotateCcwIcon aria-hidden="true" />
              Try again
            </Button>
            <Button variant="link" className="px-0 text-muted-foreground" onClick={onEdit}>
              Edit your question above
            </Button>
          </div>
        </div>
      );
  }
}

// Which dataset answers come from, so the active target stays clear (S2-BR-014).
function AnswerContext({ target, reviewTotal }: { target: Target; reviewTotal: number | null }) {
  return (
    <aside aria-label="Reviews used for answers" className="flex flex-col gap-4">
      <div className="flex flex-col gap-3 rounded-xl border bg-card p-6 text-sm">
        <p className="flex items-center gap-2 text-xs font-medium text-muted-foreground uppercase">
          <MessageSquareTextIcon className="size-4 text-primary" aria-hidden="true" />
          In this answer
        </p>
        <p className="text-base font-semibold break-words">{target.name}</p>
        <p className="text-muted-foreground">
          {reviewTotal === null ? "Loading reviews…" : reviewCount(reviewTotal)} ·{" "}
          {platformLabel(target.platform)}
        </p>
        {reviewTotal !== null && (
          <p className="flex items-center gap-1.5 text-success">
            <CircleCheckIcon className="size-4" aria-hidden="true" />
            Reviews ready
          </p>
        )}
      </div>
      <div className="flex flex-col gap-2 px-1 text-xs text-muted-foreground">
        <p className="text-sm font-medium text-foreground">A helpful question is specific.</p>
        <p>Ask about recurring complaints, standout features, or opportunities to improve.</p>
        <p>
          Answers only use this target&apos;s reviews. AI can make mistakes; check the source
          reviews.
        </p>
      </div>
    </aside>
  );
}
