"use client";

import * as React from "react";
import { ThemeProvider } from "@/components/theme/theme-provider";

// M1 SEAM: import { QueryClientProvider } from "@tanstack/react-query"
//          and the singleton client from "@/lib/query-client".
//          Wrap <ThemeProvider>{...}</ThemeProvider> with it then.

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <ThemeProvider
      attribute="class"
      defaultTheme="system"
      enableSystem
      disableTransitionOnChange
    >
      {children}
    </ThemeProvider>
  );
}
