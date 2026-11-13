"use client";

// ReviewCard (ui-ux-standards.md §8): rating, text and date when available. Reviewer
// names are deliberately absent: the backend does not store them.
import { useEffect, useRef, useState } from "react";
import { StarIcon } from "lucide-react";
import { Card, CardContent } from "@/app/_components/ui/card";
import { formatReviewDate, isLongReview, type Review } from "@/lib/reviews";

// highlighted: this is the review a link pointed at. It is scrolled into view, outlined,
// and glows for a few seconds (not at all for people who prefer reduced motion).
export function ReviewCard({
  review,
  highlighted = false,
}: {
  review: Review;
  highlighted?: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const long = isLongReview(review.review_text);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (highlighted) ref.current?.scrollIntoView?.({ block: "center", behavior: "smooth" });
  }, [highlighted]);

  return (
    <Card
      ref={ref}
      id={`review-${review.id}`}
      data-highlighted={highlighted || undefined}
      className={`py-5 ${highlighted ? "ring-2 ring-primary motion-safe:animate-[review-glow_1.2s_ease-in-out_3]" : ""}`}
    >
      <CardContent className="flex flex-col gap-3 px-5">
        <div className="flex items-center justify-between gap-2">
          <span
            role="img"
            aria-label={`${review.rating} out of 5 stars`}
            className="flex gap-0.5 text-warning"
          >
            {[1, 2, 3, 4, 5].map((star) => (
              <StarIcon
                key={star}
                aria-hidden="true"
                className={`size-4 ${star <= review.rating ? "fill-current" : ""}`}
              />
            ))}
          </span>
          {review.reviewed_at && (
            <span className="text-xs text-muted-foreground">
              {formatReviewDate(review.reviewed_at)}
            </span>
          )}
        </div>
        <p className={`text-sm leading-6 ${long && !expanded ? "line-clamp-5" : ""}`}>
          {review.review_text}
        </p>
        {long && (
          <button
            type="button"
            onClick={() => setExpanded(!expanded)}
            className="w-fit text-sm font-medium text-primary hover:underline"
          >
            {expanded ? "Show less" : "Show more"}
          </button>
        )}
      </CardContent>
    </Card>
  );
}
