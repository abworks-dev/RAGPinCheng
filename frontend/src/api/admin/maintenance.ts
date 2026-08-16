import { api } from "../client";

export const adminMaintenanceApi = {
  status: (...args: Parameters<typeof api.adminMaintenance>) => api.adminMaintenance(...args),
  preview: (...args: Parameters<typeof api.adminMaintenancePreview>) => api.adminMaintenancePreview(...args),
  runs: (...args: Parameters<typeof api.adminMaintenanceRuns>) => api.adminMaintenanceRuns(...args),
  updateSettings: (...args: Parameters<typeof api.adminUpdateMaintenanceSettings>) => api.adminUpdateMaintenanceSettings(...args),
  cleanup: (...args: Parameters<typeof api.adminRunMaintenanceCleanup>) => api.adminRunMaintenanceCleanup(...args),
};
