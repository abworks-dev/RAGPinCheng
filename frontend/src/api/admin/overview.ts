import { api } from "../client";

export const adminOverviewApi = {
  stats: (...args: Parameters<typeof api.adminStats>) => api.adminStats(...args),
  maintenance: (...args: Parameters<typeof api.adminMaintenance>) => api.adminMaintenance(...args),
  systemOverview: (...args: Parameters<typeof api.adminSystemOverview>) => api.adminSystemOverview(...args),
};
