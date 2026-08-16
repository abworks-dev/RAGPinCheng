import { api } from "../client";

export const adminFeedbackApi = {
  list: (...args: Parameters<typeof api.adminFeedback>) => api.adminFeedback(...args),
  patch: (...args: Parameters<typeof api.adminPatchFeedback>) => api.adminPatchFeedback(...args),
};
