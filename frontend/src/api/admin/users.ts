import { api } from "../client";

export const adminUsersApi = {
  list: (...args: Parameters<typeof api.adminListUsers>) => api.adminListUsers(...args),
  patch: (...args: Parameters<typeof api.adminPatchUser>) => api.adminPatchUser(...args),
};
