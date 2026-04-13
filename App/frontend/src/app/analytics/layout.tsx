import React from "react";
import type { Metadata } from "next";
import Link from "next/link";
import Providers from "../../components/Providers";

export const metadata: Metadata = {
  title: "URA Chatbot — Analytics Dashboard",
  description: "Production observability: SLO gauges, RAG evaluation, quality comparison, and feedback analytics.",
};

export default function AnalyticsLayout({ children }: { children: React.ReactNode }) {
  return (
    <>
      <nav className="analytics-nav">
        <Link href="/analytics" className="nav-link">Overview</Link>
        <Link href="/analytics/evaluation" className="nav-link">Evaluation</Link>
        <Link href="/" className="nav-link nav-back">Back to Chat</Link>
      </nav>
      <Providers>{children}</Providers>
    </>
  );
}
