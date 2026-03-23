import "@/styles/globals.css";
import type { AppProps } from "next/app";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { appWithTranslation } from "next-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { AnalyticsProvider } from "@/lib/analytics-provider";
import { reportWebVitals } from "@/utils/performance";
import { ThemeProvider } from "@/components/ThemeProvider"; // 1. Added this

function App({ Component, pageProps }: AppProps) {
  const [queryClient] = useState(() => new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 60 * 1000,
        refetchOnWindowFocus: false,
      },
    },
  }));

  return (
    <AnalyticsProvider>
      <ErrorBoundary>
        <QueryClientProvider client={queryClient}>
          {/* 2. Wrap everything in ThemeProvider */}
          <ThemeProvider>
            <Component {...pageProps} />
          </ThemeProvider>
        </QueryClientProvider>
      </ErrorBoundary>
    </AnalyticsProvider>
  );
}

export { reportWebVitals };
export default appWithTranslation(App);