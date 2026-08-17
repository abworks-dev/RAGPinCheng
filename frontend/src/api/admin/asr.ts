import { api } from "../client";

export const adminAsrApi = {
  get: () => api.adminAsrSettings(),
  requestRelease: (...args: Parameters<typeof api.adminCreateAsrReleaseRequest>) =>
    api.adminCreateAsrReleaseRequest(...args),
};
