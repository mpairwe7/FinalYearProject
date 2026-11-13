/**
 * What the "Found helpful by taxpayers" card says when it has no figure to show.
 *
 * "No answers have been rated" is a finding, so it is said only after a
 * response with no ratings: not while the request is on its way (on first load
 * or after the period changes), and not when it failed.
 */
export function ratingsEmptyText(query: {
  isError: boolean;
  isPending: boolean;
  data?: { total: number };
}): string {
  if (query.isError) return "Ratings could not be loaded. Nothing is known about this period from here.";
  if (query.isPending || !query.data) return "Loading this period's ratings…";
  return "No answers have been rated in this period.";
}
