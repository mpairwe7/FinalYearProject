import React from "react";
import { OpsPanel, TableScroll } from "../ops/OpsPage";
import { EmptyState } from "../ops/States";
import type { JourneyFunnel, JourneyStats, JourneyStepStats } from "../../services/analyticsApi";

/**
 * Guided journeys — how far taxpayers get through each step-by-step guide.
 *
 * Reads GET /v1/analytics/journeys, which is built from stored journey
 * sessions, so the numbers follow the period picker and survive restarts
 * (unlike the request counters elsewhere on this page).
 *
 * The two right-hand columns are what the customer experience team acts on:
 * the step where most journeys stop is the drop-off to fix first, and the
 * step taxpayers rated least helpful is the reply to reword first.
 */

function worst(steps: JourneyStepStats[], key: "stopped" | "not_helpful"): JourneyStepStats | null {
  let best: JourneyStepStats | null = null;
  for (const step of steps) {
    if (step[key] > 0 && (!best || step[key] > best[key])) best = step;
  }
  return best;
}

function Row({ journey }: { journey: JourneyStats }) {
  const stopped = journey.cancelled + journey.abandoned;
  const dropOff = worst(journey.steps, "stopped");
  const unhelpful = worst(journey.steps, "not_helpful");
  return (
    <tr>
      <th scope="row">{journey.name}</th>
      <td className="an-num">{journey.started}</td>
      <td className="an-num">
        {journey.started > 0 ? `${journey.completed} (${Math.round(journey.completion_pct)}%)` : "—"}
      </td>
      <td className="an-num">
        {stopped > 0 ? (
          <span title={`${journey.cancelled} cancelled, ${journey.abandoned} abandoned`}>{stopped}</span>
        ) : (
          "0"
        )}
      </td>
      <td>{dropOff ? `${dropOff.title} (${dropOff.stopped})` : "—"}</td>
      <td>{unhelpful ? `${unhelpful.title} (${unhelpful.not_helpful} not helpful)` : "—"}</td>
    </tr>
  );
}

export default function JourneyFunnelTable({ data }: { data: JourneyFunnel }) {
  const used = data.journeys.filter((j) => j.started > 0);
  return (
    <OpsPanel
      id="an-journeys"
      title="Guided journeys"
      note={`How far taxpayers get through each step-by-step guide in this period. A journey left untouched for ${data.abandon_after_hours} hours counts as abandoned; where it stopped is the step it was waiting at.`}
      flush
      bare
    >
      {used.length === 0 ? (
        <EmptyState
          title="No guided journeys in this period"
          body="Nobody started a step-by-step guide in this period. Every guide is listed here once someone does."
        />
      ) : (
        <TableScroll label="Guided journeys">
          <table className="ops-table">
            <thead>
              <tr>
                <th scope="col">Journey</th>
                <th scope="col">Started</th>
                <th scope="col">Finished</th>
                <th scope="col">Stopped</th>
                <th scope="col">Where most stop</th>
                <th scope="col">Least helpful step</th>
              </tr>
            </thead>
            <tbody>
              {data.journeys.map((journey) => (
                <Row key={journey.workflow_id} journey={journey} />
              ))}
            </tbody>
          </table>
        </TableScroll>
      )}
    </OpsPanel>
  );
}
