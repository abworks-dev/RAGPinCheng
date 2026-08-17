import { api } from "../client";

export const adminAnswerPolicyApi = {
  get: () => api.adminAnswerPolicy(),
  update: (...args: Parameters<typeof api.adminUpdateAnswerPolicy>) => api.adminUpdateAnswerPolicy(...args),
  reset: () => api.adminResetAnswerPolicy(),
  audit: (...args: Parameters<typeof api.adminAnswerPolicyAudit>) => api.adminAnswerPolicyAudit(...args),
};
