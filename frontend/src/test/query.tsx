// Wraps a component or hook under test in TanStack Query, as QueryProvider does in the app.
import { QueryClientProvider } from "@tanstack/react-query";
import { createQueryClient } from "@/lib/query-client";

// A fresh client per test, with the app's rules but no retries, so a failure shows at once.
// onUnauthorized is what the app does on a 401 (redirect to /login); pass a vi.fn() to check it.
export function queryWrapper(onUnauthorized: () => void = () => {}) {
  const client = createQueryClient(onUnauthorized);
  client.setDefaultOptions({ queries: { retry: false }, mutations: { retry: false } });
  return function QueryWrapper({ children }: { children: React.ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}
