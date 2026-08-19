import { AdminMediaPage } from "./AdminMediaPage";

/**
 * The managed-content tab is the only entry point for video uploads. Reuse
 * the existing media workbench so replacement, archive, failure recovery and
 * transcript review keep their established contracts.
 */
export function AdminTranscriptionTasksPage() {
  return <AdminMediaPage embedded />;
}
