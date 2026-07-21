import Image from 'next/image';
import { Sparkles } from 'lucide-react';

/**
 * Decorative, theme-aware mock of the chatbot UI for the landing hero —
 * a contextual product visual (uses the real URA logo).
 */
export function HeroChat() {
  return (
    <div className="rounded-2xl border border-border bg-card shadow-xl shadow-black/5">
      <div className="flex items-center gap-2 border-b border-border px-4 py-3">
        <Image src="/URA-logo.png" alt="URA" width={20} height={20} className="rounded" />
        <span className="text-sm font-medium text-foreground">URA Tax Assistant</span>
        <span className="ml-auto flex items-center gap-1.5 text-xs text-muted-foreground">
          <span className="h-2 w-2 rounded-full bg-[#22c55e]" /> Online
        </span>
      </div>

      <div className="space-y-4 p-4">
        <div className="flex justify-end">
          <p className="max-w-[80%] rounded-2xl rounded-br-sm bg-accent px-4 py-2 text-sm text-accent-foreground">
            How do I register for a TIN?
          </p>
        </div>

        <div className="flex justify-start">
          <div className="max-w-[88%] rounded-2xl rounded-bl-sm bg-secondary px-4 py-3 text-sm text-foreground">
            <p className="leading-relaxed">
              To register for a Taxpayer Identification Number (TIN), visit the URA web portal,
              create an account, and complete the e-registration form with your details.
            </p>
            <div className="mt-3 flex items-center gap-2 border-t border-border pt-2 text-xs text-muted-foreground">
              <span className="rounded bg-accent/10 px-1.5 py-0.5 font-medium text-accent">Source</span>
              URA TIN Registration Guide
            </div>
          </div>
        </div>

        <div className="flex justify-end">
          <p className="max-w-[80%] rounded-2xl rounded-br-sm bg-accent px-4 py-2 text-sm text-accent-foreground">
            Mu Luganda?
          </p>
        </div>

        <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <Sparkles className="h-3.5 w-3.5 text-accent" />
          Generating answer in Luganda…
        </div>
      </div>
    </div>
  );
}
