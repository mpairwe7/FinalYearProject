/**
 * Server-side instrumentation for the Next.js process.
 *
 * `onRequestError` turns every error Next.js catches while rendering a route
 * into one JSON log line on stderr, in the field names the API uses
 * (docs/MONITORING.md §9), so the platform's log pipeline can alert on it and
 * the digest shown on the error page can be found in the logs. The error
 * message is not logged: it can quote request data.
 */
import type { Instrumentation } from 'next';

export const onRequestError: Instrumentation.onRequestError = async (error, request, context) => {
  const err = error as Error & { digest?: string };
  console.error(
    JSON.stringify({
      timestamp: new Date().toISOString(),
      level: 'ERROR',
      severity_text: 'ERROR',
      severity_number: 17,
      logger: 'next.server',
      message: 'request error',
      service: 'ura-chatbot-frontend',
      attributes: {
        event: 'server.request_error',
        error_name: err?.name ?? 'Error',
        digest: err?.digest ?? '',
        http_method: request.method,
        http_route: context.routePath,
        route_type: context.routeType,
        router_kind: context.routerKind,
      },
    }),
  );
};
