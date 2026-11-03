// Fake HTTP responses shaped like the backend's (coding-standards.md §5).
// They work both with a vi.fn() fetch (unit tests) and in MSW handlers (integration tests).

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

// The backend's error format: { error: { code, message, details } }.
export function apiErrorResponse(status: number, code: string, message: string): Response {
  return jsonResponse({ error: { code, message, details: [] } }, status);
}
