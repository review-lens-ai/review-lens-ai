import { MutationCache, QueryCache, QueryClient } from "@tanstack/react-query";
import { ApiError, isUnauthorized } from "@/lib/api";

const MAX_RETRIES = 3;

// Retry only failures that might pass next time: network errors and 5xx.
// A 4xx (not found, conflict, invalid input) answers the same way every time.
function shouldRetry(failureCount: number, error: Error): boolean {
  if (error instanceof ApiError && error.status < 500) return false;
  return failureCount < MAX_RETRIES;
}

// onUnauthorized runs for any query or mutation that gets a 401 (session ended),
// and receives the client so it can clear the cache.
export function createQueryClient(onUnauthorized: (client: QueryClient) => void): QueryClient {
  const onError = (error: Error) => {
    if (isUnauthorized(error)) onUnauthorized(client);
  };
  const client = new QueryClient({
    queryCache: new QueryCache({ onError }),
    mutationCache: new MutationCache({ onError }),
    defaultOptions: { queries: { retry: shouldRetry } },
  });
  return client;
}
