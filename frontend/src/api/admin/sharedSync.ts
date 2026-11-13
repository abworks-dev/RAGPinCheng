import { api } from "../client";

export const adminSharedSyncApi = {
  status: () => api.adminSharedSyncStatus(),
  runs: (params?: { sourceId?: string; limit?: number }) => api.adminSharedSyncRuns(params),
  events: (params?: { sourceId?: string; runId?: string; before?: number; limit?: number }) =>
    api.adminSharedSyncEvents(params),
  withdrawals: (params?: { sourceId?: string; state?: string; limit?: number }) =>
    api.adminSharedSyncWithdrawals(params),
  restore: (withdrawalId: string) => api.adminSharedSyncRestore(withdrawalId),
  runNow: (sourceIds?: string[] | null) => api.adminSharedSyncRun(sourceIds),
};
