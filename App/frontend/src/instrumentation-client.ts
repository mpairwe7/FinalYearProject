/**
 * Runs in the browser before the app hydrates (Next.js instrumentation-client).
 * Starts real-user monitoring early enough to see the first paint and any
 * error thrown during hydration. See src/lib/web-vitals.ts and
 * src/lib/client-errors.ts.
 */
import { installGlobalErrorHandlers } from '@/lib/client-errors';
import { initWebVitalsTracking } from '@/lib/web-vitals';

installGlobalErrorHandlers();
initWebVitalsTracking();
