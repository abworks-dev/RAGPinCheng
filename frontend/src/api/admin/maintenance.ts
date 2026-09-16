import { api } from "../client";

export const adminMaintenanceApi = {
  status: (...args: Parameters<typeof api.adminMaintenance>) => api.adminMaintenance(...args),
  preview: (...args: Parameters<typeof api.adminMaintenancePreview>) => api.adminMaintenancePreview(...args),
  runs: (...args: Parameters<typeof api.adminMaintenanceRuns>) => api.adminMaintenanceRuns(...args),
  updateSettings: (...args: Parameters<typeof api.adminUpdateMaintenanceSettings>) => api.adminUpdateMaintenanceSettings(...args),
  cleanup: (...args: Parameters<typeof api.adminRunMaintenanceCleanup>) => api.adminRunMaintenanceCleanup(...args),
  listPrompts: (...args: Parameters<typeof api.adminListPrompts>) => api.adminListPrompts(...args),
  updatePrompt: (...args: Parameters<typeof api.adminUpdatePrompt>) => api.adminUpdatePrompt(...args),
  restorePrompt: (...args: Parameters<typeof api.adminRestorePrompt>) => api.adminRestorePrompt(...args),
};
