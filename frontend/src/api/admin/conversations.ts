import { api } from "../client";

export const adminConversationsApi = {
  listAll: (...args: Parameters<typeof api.adminListAllConversations>) => api.adminListAllConversations(...args),
  listForUser: (...args: Parameters<typeof api.adminListUserConversations>) => api.adminListUserConversations(...args),
  get: (...args: Parameters<typeof api.adminGetConversation>) => api.adminGetConversation(...args),
};
