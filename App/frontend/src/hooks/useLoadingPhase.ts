"use client";

import { useEffect, useState } from "react";

/**
 * Which rung of the loading ladder a pending request is on.
 *
 * The console used to answer "is it loading?" with a boolean and draw a
 * skeleton whenever it was true. That is wrong at both ends of the range:
 *
 *   - Under ~300ms the skeleton appears and vanishes as a flicker. It makes the
 *     console feel less stable, not more responsive — the reader registers that
 *     something happened but not what.
 *   - Past ~10s an endlessly looping shimmer is indistinguishable from a hung
 *     request. The queue has been "loading" for half a minute and the page is
 *     still cheerfully sweeping a gradient across an empty box.
 *
 * So the phases are:
 *
 *   "idle"      not loading
 *   "settling"  loading, but too briefly to be worth saying so — render nothing
 *   "loading"   show the skeleton
 *   "slow"      it has been long enough that silence is a lie — the caller
 *               should say what is happening, or offer a retry
 *
 * @param isLoading  the query's pending state
 * @param quietMs    how long to stay silent before showing anything
 * @param slowMs     how long before the wait is reported as slow
 */
export type LoadingPhase = "idle" | "settling" | "loading" | "slow";

export function useLoadingPhase(
  isLoading: boolean,
  { quietMs = 300, slowMs = 10_000 }: { quietMs?: number; slowMs?: number } = {},
): LoadingPhase {
  // How far along the ladder the current pending state has got: 0 settling,
  // 1 loading, 2 slow. Only the timers advance it.
  const [rung, setRung] = useState(0);

  // The ladder has to restart whenever a *new* pending state begins, or a
  // refetch that follows a slow first load would open straight into "slow".
  //
  // That reset happens here, during render, rather than in the effect below.
  // Resetting from inside an effect means React commits the stale phase, runs
  // the effect, then re-renders — a cascading render that briefly paints the
  // previous request's rung. Adjusting state during render is the documented
  // way to respond to a changed input, and React re-runs this component
  // immediately without committing the intermediate result.
  const [wasLoading, setWasLoading] = useState(isLoading);
  if (wasLoading !== isLoading) {
    setWasLoading(isLoading);
    setRung(0);
  }

  useEffect(() => {
    if (!isLoading) return;
    const toLoading = setTimeout(() => setRung(1), quietMs);
    const toSlow = setTimeout(() => setRung(2), slowMs);
    return () => {
      clearTimeout(toLoading);
      clearTimeout(toSlow);
    };
  }, [isLoading, quietMs, slowMs]);

  if (!isLoading) return "idle";
  return rung === 0 ? "settling" : rung === 1 ? "loading" : "slow";
}

/**
 * The common case: "should I draw a skeleton right now?"
 *
 * True through both the "loading" and "slow" phases — a caller that wants to
 * distinguish them (to add a retry, or a line saying the backend is taking
 * longer than usual) should use `useLoadingPhase` directly.
 */
export function useSkeletonVisible(isLoading: boolean): boolean {
  const phase = useLoadingPhase(isLoading);
  return phase === "loading" || phase === "slow";
}
