import { useCallback, useEffect, useState } from "react";
import { adminConversationsApi } from "../api/admin/conversations";
import type { AdminConversation } from "../types";

export function useAdminConversations(limit = 200) {
  const [conversations, setConversations] = useState<AdminConversation[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await adminConversationsApi.listAll(limit);
      setConversations(response.conversations);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoading(false);
    }
  }, [limit]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { conversations, loading, error, refresh };
}
