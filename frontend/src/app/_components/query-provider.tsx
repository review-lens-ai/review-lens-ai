"use client";

import { QueryClientProvider } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { createQueryClient } from "@/lib/query-client";

export function QueryProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  // One client per browser tab. A 401 clears the cache, so the next user to sign in
  // never sees the previous user's data, then goes to /login.
  const [queryClient] = useState(() =>
    createQueryClient((client) => {
      client.clear();
      router.replace("/login");
    }),
  );
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}
