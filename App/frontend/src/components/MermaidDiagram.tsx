"use client";

import React, { memo, useEffect, useId, useRef, useState } from "react";

/**
 * Client-side Mermaid diagram renderer.
 *
 * Uses `mermaid.render()` (async, no eval) to produce an SVG string,
 * then injects it into the DOM via innerHTML. This avoids CSP issues
 * with `contentLoaded()` and supports React 19 concurrent rendering.
 *
 * Dark theme matches the Grok-inspired design tokens from globals.css.
 */

let mermaidInitialized = false;

async function ensureMermaid() {
  const mermaid = (await import("mermaid")).default;
  if (!mermaidInitialized) {
    mermaid.initialize({
      startOnLoad: false,
      theme: "dark",
      darkMode: true,
      fontFamily: "var(--font-geist-sans, system-ui, sans-serif)",
      themeVariables: {
        primaryColor: "#7c3aed",
        primaryBorderColor: "#a78bfa",
        primaryTextColor: "#fafafa",
        secondaryColor: "#1e1b4b",
        secondaryBorderColor: "#6366f1",
        secondaryTextColor: "#e2e8f0",
        tertiaryColor: "#0f172a",
        tertiaryBorderColor: "#334155",
        tertiaryTextColor: "#94a3b8",
        lineColor: "#60a5fa",
        textColor: "#e2e8f0",
        mainBkg: "#1e1b4b",
        nodeBorder: "#a78bfa",
        clusterBkg: "rgba(255,255,255,0.03)",
        clusterBorder: "rgba(255,255,255,0.08)",
        titleColor: "#fafafa",
        edgeLabelBackground: "#0a0a12",
        nodeTextColor: "#fafafa",
        // Pie chart
        pie1: "#8b5cf6",
        pie2: "#3b82f6",
        pie3: "#22d3ee",
        pie4: "#34d399",
        pie5: "#fbbf24",
        pie6: "#fb7185",
        pie7: "#a78bfa",
        pie8: "#60a5fa",
        pieStrokeColor: "#1e1b4b",
        pieTitleTextColor: "#fafafa",
        pieSectionTextColor: "#fafafa",
        pieLegendTextColor: "#e2e8f0",
        // Sequence
        actorBkg: "#1e1b4b",
        actorBorder: "#a78bfa",
        actorTextColor: "#fafafa",
        signalColor: "#60a5fa",
        signalTextColor: "#e2e8f0",
        noteBkgColor: "#1e293b",
        noteBorderColor: "#334155",
        noteTextColor: "#e2e8f0",
        activationBkgColor: "#312e81",
        activationBorderColor: "#6366f1",
      },
    });
    mermaidInitialized = true;
  }
  return mermaid;
}

function MermaidDiagramInner({ content }: { content: string }) {
  const containerId = useId().replace(/:/g, "_");
  const ref = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [rendering, setRendering] = useState(true);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        const mermaid = await ensureMermaid();
        const id = `mmd${containerId}${Date.now()}`;
        const { svg } = await mermaid.render(id, content.trim());
        if (!cancelled && ref.current) {
          // Sanitize SVG: strip <script>, event handlers, and foreign objects
          const clean = svg
            .replace(/<script[\s\S]*?<\/script>/gi, "")
            .replace(/\bon\w+\s*=\s*["'][^"']*["']/gi, "")
            .replace(/<foreignObject[\s\S]*?<\/foreignObject>/gi, "");
          ref.current.innerHTML = clean;
          // Make SVG responsive
          const svgEl = ref.current.querySelector("svg");
          if (svgEl) {
            svgEl.removeAttribute("height");
            svgEl.style.maxWidth = "100%";
            svgEl.style.height = "auto";
          }
        }
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : "Invalid diagram syntax");
        }
      } finally {
        if (!cancelled) setRendering(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [content, containerId]);

  if (error) {
    return (
      <div className="md-diagram-container md-diagram-error">
        <div className="md-diagram-error-label">Diagram error</div>
        <pre className="md-code-block">
          <code>{content}</code>
        </pre>
      </div>
    );
  }

  return (
    <div className="md-diagram-container">
      {rendering && (
        <div className="md-diagram-loading">Rendering diagram...</div>
      )}
      <div ref={ref} className="md-diagram-svg" />
    </div>
  );
}

const MermaidDiagram = memo(MermaidDiagramInner);
export default MermaidDiagram;
