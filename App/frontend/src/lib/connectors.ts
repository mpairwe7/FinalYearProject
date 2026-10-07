/** Browser-safe discovery shape for reviewed, deployment-configured MCP services. */
export interface ChatConnector {
  namespace: string;
  label: string;
  description: string;
  operation_count: number;
  read_only: true;
}
