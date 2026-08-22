import React from "react";
import type { Metadata } from "next";

/**
 * `/analytics` used to live outside the operations console entirely.
 *
 * It was listed in the console's nav and in the staff account menu, but the
 * pages themselves were not behind `StaffGuard` and rendered their own three-
 * link bar — "Overview · Evaluation · Back to Chat" — so an officer who
 * clicked Analytics lost the console's navigation, its live escalation strip
 * and its sign-out, and an anonymous visitor got empty panels and a raw
 * "Failed to load dashboard" string where every other staff route offers a
 * sign-in. The gate now lives on the pages, next to the roles they require, and
 * the nav is the console's.
 *
 * This layout also wrapped its children in a second `<Providers>`, nesting a
 * fresh QueryClient inside the root one: two caches, so an invalidation from a
 * staff page never reached the analytics tree and the same query could be in
 * flight twice. The root layout already provides it.
 */
export const metadata: Metadata = {
  title: "URA Chatbot — Analytics",
  description:
    "Production observability: service levels, RAG evaluation, quality comparison, and feedback analytics.",
};

export default function AnalyticsLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
