"use client";

// S3-016: the rating and date filters above the Reviews list.
// The user edits pending choices in a form, then applies them (or cancels, which leaves
// the list as it was). The applied filters show as a summary with "Clear all", so it is
// always visible when the list is not the complete dataset. The filtering itself
// happens on the server, on the active target's saved reviews only.
import { type FormEvent, useId, useState } from "react";
import { SlidersHorizontalIcon } from "lucide-react";
import { Button } from "@/app/_components/ui/button";
import { Input } from "@/app/_components/ui/input";
import {
  describeFilters,
  filterProblem,
  hasFilters,
  NO_FILTERS,
  type ReviewFilters as Filters,
} from "@/lib/review-filters";

const RATINGS = [1, 2, 3, 4, 5];

// Styled like <Input>, for the two rating choices.
const SELECT_CLASS =
  "h-9 w-full rounded-md border border-input bg-transparent px-3 text-base shadow-xs outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 md:text-sm dark:bg-input/30";

// applied: the filters the list shows now. onApply resolves to false when the matching
// reviews couldn't load (the list then still shows the previous ones). busy: a page or
// a filter is loading.
export function ReviewFilters({
  applied,
  onApply,
  busy,
}: {
  applied: Filters;
  onApply: (filters: Filters) => Promise<boolean>;
  busy: boolean;
}) {
  const ids = useId();
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState<Filters>(applied);
  const [problem, setProblem] = useState<string | null>(null);
  const active = hasFilters(applied);

  function openForm() {
    setPending(applied);
    setProblem(null);
    setOpen(true);
  }

  function cancel() {
    setPending(applied);
    setProblem(null);
    setOpen(false);
  }

  async function apply(filters: Filters) {
    const invalid = filterProblem(filters);
    if (invalid) {
      setProblem(invalid);
      return;
    }
    setProblem(null);
    if (await onApply(filters)) {
      setOpen(false);
    } else {
      setProblem("We couldn't load the matching reviews. Try again in a few minutes.");
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void apply(pending);
  }

  async function clearAll() {
    setPending(NO_FILTERS);
    if (await onApply(NO_FILTERS)) {
      setOpen(false);
      setProblem(null);
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          aria-expanded={open}
          aria-controls={`${ids}-form`}
          onClick={open ? cancel : openForm}
        >
          <SlidersHorizontalIcon aria-hidden="true" />
          Filters
        </Button>
        {active && (
          <>
            <p className="rounded-full bg-primary/10 px-2.5 py-0.5 text-sm text-primary">
              <span className="font-medium">Filtered:</span> {describeFilters(applied)}
            </p>
            <Button type="button" variant="link" size="sm" onClick={clearAll} disabled={busy}>
              Clear all
            </Button>
          </>
        )}
      </div>

      {open && (
        <form
          id={`${ids}-form`}
          aria-label="Filter reviews"
          onSubmit={submit}
          noValidate
          className="flex flex-col gap-4 rounded-xl border bg-card p-4"
        >
          <div className="grid gap-4 @md:grid-cols-2 @3xl:grid-cols-4">
            <RatingChoice
              id={`${ids}-min`}
              label="Minimum rating"
              value={pending.minRating}
              onChange={(minRating) => setPending({ ...pending, minRating })}
            />
            <RatingChoice
              id={`${ids}-max`}
              label="Maximum rating"
              value={pending.maxRating}
              onChange={(maxRating) => setPending({ ...pending, maxRating })}
            />
            <DateChoice
              id={`${ids}-start`}
              label="From date"
              value={pending.startDate}
              onChange={(startDate) => setPending({ ...pending, startDate })}
            />
            <DateChoice
              id={`${ids}-end`}
              label="To date"
              value={pending.endDate}
              onChange={(endDate) => setPending({ ...pending, endDate })}
            />
          </div>
          <p className="text-xs text-muted-foreground">
            Ratings and dates include the values you choose. Reviews without a date are left out
            when you filter by date.
          </p>
          {problem && (
            <p role="alert" className="text-sm text-destructive">
              {problem}
            </p>
          )}
          <div className="flex flex-wrap gap-2">
            <Button type="submit" size="sm" disabled={busy}>
              Apply filters
            </Button>
            <Button type="button" size="sm" variant="outline" onClick={cancel}>
              Cancel
            </Button>
          </div>
        </form>
      )}
    </div>
  );
}

function RatingChoice({
  id,
  label,
  value,
  onChange,
}: {
  id: string;
  label: string;
  value: number | undefined;
  onChange: (value: number | undefined) => void;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-sm font-medium">
        {label}
      </label>
      <select
        id={id}
        value={value ?? ""}
        onChange={(event) =>
          onChange(event.target.value === "" ? undefined : Number(event.target.value))
        }
        className={SELECT_CLASS}
      >
        <option value="">Any</option>
        {RATINGS.map((rating) => (
          <option key={rating} value={rating}>
            {rating} {rating === 1 ? "star" : "stars"}
          </option>
        ))}
      </select>
    </div>
  );
}

function DateChoice({
  id,
  label,
  value,
  onChange,
}: {
  id: string;
  label: string;
  value: string | undefined;
  onChange: (value: string | undefined) => void;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-sm font-medium">
        {label}
      </label>
      <Input
        id={id}
        type="date"
        value={value ?? ""}
        onChange={(event) => onChange(event.target.value || undefined)}
      />
    </div>
  );
}
