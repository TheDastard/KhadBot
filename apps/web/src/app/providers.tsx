// apps/web/src/app/providers.tsx
//
// The client-side context every page sits in: TanStack Query, Radix's tooltip provider, and the capability gate,
// which reads /v1/health before anything else renders.

"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

import { TooltipProvider } from "@/components/ui/tooltip";
import { CapabilitiesGate } from "@/lib/capabilities";

export function Providers({ children }: { children: ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: { staleTime: 30_000, refetchOnWindowFocus: false },
        },
      }),
  );
  return (
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <CapabilitiesGate>{children}</CapabilitiesGate>
      </TooltipProvider>
    </QueryClientProvider>
  );
}
