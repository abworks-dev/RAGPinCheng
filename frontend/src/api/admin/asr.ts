import { api } from "../client";

export const adminAsrApi = {
  get: () => api.adminAsrSettings(),
  bases: () => api.adminTranscriptionBases(),
  schemes: (...args: Parameters<typeof api.adminTranscriptionSchemes>) => api.adminTranscriptionSchemes(...args),
  createScheme: (...args: Parameters<typeof api.adminCreateTranscriptionScheme>) => api.adminCreateTranscriptionScheme(...args),
  copyScheme: (...args: Parameters<typeof api.adminCopyTranscriptionScheme>) => api.adminCopyTranscriptionScheme(...args),
  updateScheme: (...args: Parameters<typeof api.adminUpdateTranscriptionScheme>) => api.adminUpdateTranscriptionScheme(...args),
  reorderSchemes: (...args: Parameters<typeof api.adminReorderTranscriptionSchemes>) => api.adminReorderTranscriptionSchemes(...args),
  requestRelease: (...args: Parameters<typeof api.adminCreateAsrReleaseRequest>) =>
    api.adminCreateAsrReleaseRequest(...args),
};
