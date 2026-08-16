import { useCallback, useEffect, useState } from "react";
import { adminMediaApi } from "../api/admin/media";
import type { MediaAsset } from "../types";

export function useAdminMediaAssets() {
  const [assets, setAssets] = useState<MediaAsset[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setAssets(await adminMediaApi.listAssets());
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const removeAsset = useCallback((mediaId: string) => {
    setAssets((current) => current.filter((asset) => asset.media_id !== mediaId));
  }, []);

  return { assets, loading, error, refresh, removeAsset };
}
