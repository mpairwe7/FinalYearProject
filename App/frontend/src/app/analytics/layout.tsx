import React from "react";
import type { Metadata } from "next";
import Providers from "../../components/Providers";

export const metadata: Metadata = {
  title: "URA Chatbot — Analytics Dashboard",
  description: "Production observability: SLO gauges, RAG evaluation, quality comparison, and feedback analytics.",
};

export default function AnalyticsLayout({ children }: { children: React.ReactNode }) {
  return (
    <>
      <nav className="analytics-nav">
        <a href="/analytics" className="nav-link">Overview</a>
        <a href="/analytics/evaluation" className="nav-link">Evaluation</a>
        <a href="/" className="nav-link nav-back">Back to Chat</a>
      </nav>
      <Providers>{children}</Providers>
    </>
  );
}
