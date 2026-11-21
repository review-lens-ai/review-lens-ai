// S3-007, S3-008: a target's saved questions and answers.
//
// TEMPORARY HAND-WRITTEN CLIENT. The endpoints come with S3-006 (backend), which is not
// merged yet. This is the contract agreed for it:
//
//   GET    /api/v1/targets/{target_id}/questions  -> { items: SavedQaExchange[] }, oldest first
//   DELETE /api/v1/targets/{target_id}/questions  -> 204; the target, its reviews and its
//                                                    ingestion summary are not touched
//
// Another user's target is a 404 with code NOT_FOUND, as for every target endpoint.
// Once the backend is merged, run `npm run api:generate` and replace the types and the two
// request functions below with the generated ones (coding-standards.md, "API").
import { apiFetch } from "@/lib/api";
import type { AnswerResponse } from "@/lib/generated/api";

// One question and the reply ReviewLens gave, as saved. The reply is in the same shape
// the ask endpoint returns, so a restored answer looks exactly like a new one.
export type SavedQaExchange = {
  id: string;
  question: string;
  created_at: string;
  response: AnswerResponse;
};

export type QaHistory = { items: SavedQaExchange[] };

function historyPath(targetId: string): string {
  return `/api/v1/targets/${targetId}/questions`;
}

export function getQaHistory(targetId: string): Promise<QaHistory> {
  return apiFetch<QaHistory>(historyPath(targetId));
}

export function clearQaHistory(targetId: string): Promise<void> {
  return apiFetch<void>(historyPath(targetId), { method: "DELETE" });
}

export function qaHistoryQueryKey(targetId: string) {
  return ["qaHistory", targetId] as const;
}

// "1 question", "2 questions"
export function questionCount(n: number): string {
  return `${n} ${n === 1 ? "question" : "questions"}`;
}

function sameDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  );
}

// The heading above the questions asked on one day, in the user's time zone:
// "Today", "Yesterday · Nov 19", or "Nov 18, 2026".
export function dayLabel(iso: string, now: Date = new Date()): string {
  const day = new Date(iso);
  if (sameDay(day, now)) return "Today";
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (sameDay(day, yesterday)) {
    return `Yesterday · ${day.toLocaleDateString(undefined, { month: "short", day: "numeric" })}`;
  }
  return day.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

// When a question was asked, e.g. "2:31 PM".
export function timeLabel(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

// The day heading to show above each question: only the first question of each day gets
// one. Keyed by the question's id; questions without a heading are left out.
export function dayHeadings(
  questions: { id: string; askedAt: string }[],
  now: Date = new Date(),
): Map<string, string> {
  const headings = new Map<string, string>();
  let previous: string | null = null;
  for (const { id, askedAt } of questions) {
    const label = dayLabel(askedAt, now);
    if (label !== previous) headings.set(id, label);
    previous = label;
  }
  return headings;
}
