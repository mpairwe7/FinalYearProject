import React, { memo } from 'react';
import { SparklesIcon } from './Icons';

const STARTER_PROMPTS = [
  'What services does URA provide?',
  'How do I register for a TIN?',
  'What is the current VAT rate in Uganda?',
  'How do I file my annual tax returns?',
] as const;

interface StarterPromptsProps {
  onSelect: (prompt: string) => void;
}

function StarterPromptsInner({ onSelect }: StarterPromptsProps) {
  return (
    <aside className="card">
      <header className="section-title">
        <div><h3>Quick prompts</h3><span className="small">Tap to try a question</span></div>
        <div className="pill"><SparklesIcon /> Suggestions</div>
      </header>
      <div className="chip-grid">
        {STARTER_PROMPTS.map((p) => (
          <button key={p} className="chip" onClick={() => onSelect(p)}>
            <SparklesIcon /> {p}
          </button>
        ))}
      </div>
      <div className="panel-note">
        <h4>Voice capabilities</h4>
        <ul>
          <li>Speech recognition in English and Luganda.</li>
          <li>Audio narration of every reply — tap Listen or enable auto-narrate.</li>
          <li>Voice mode: speak naturally and hear the answer aloud.</li>
          <li>Automatic translation between English and Luganda.</li>
        </ul>
      </div>
      <div className="panel-note">
        <h4>How grounding works</h4>
        <ul>
          <li>Hybrid dense + BM25 retrieval over indexed URA FAQs.</li>
          <li>Each reply shows the exact source files it was built from.</li>
          <li>Faithfulness score indicates how well the answer is supported.</li>
        </ul>
      </div>
    </aside>
  );
}

const StarterPrompts = memo(StarterPromptsInner);

export default StarterPrompts;
