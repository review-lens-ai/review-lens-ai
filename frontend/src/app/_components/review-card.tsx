"use client";

// ReviewCard (ui-ux-standards.md §8): rating, text and date when available. Reviewer
// names are deliberately absent: the backend does not store them.
import { useState } from "react";
import { StarIcon } from "lucide-react";
import { Card, CardContent } from "@/app/_components/ui/card";
import { formatReviewDate, isLongReview, type Review } from "@/lib/reviews";

export function ReviewCard({ review }: { review: Review }) {
  const [expanded, setExpanded] = useState(false);
  const long = isLongReview(review.review_text);

  return (
    <Card className="py-5">
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
