import { api } from "../client";

export const adminMediaApi = {
  uploadManual: (...args: Parameters<typeof api.uploadMediaVideo>) => api.uploadMediaVideo(...args),
  uploadAutomatic: (...args: Parameters<typeof api.uploadAutomaticMediaVideo>) => api.uploadAutomaticMediaVideo(...args),
  uploadReplacement: (...args: Parameters<typeof api.uploadReplacementMediaVideo>) => api.uploadReplacementMediaVideo(...args),
  listAssets: (...args: Parameters<typeof api.listMediaAssets>) => api.listMediaAssets(...args),
  deleteFailedAsset: (...args: Parameters<typeof api.deleteFailedMediaAsset>) => api.deleteFailedMediaAsset(...args),
  archiveAsset: (...args: Parameters<typeof api.archiveMediaAsset>) => api.archiveMediaAsset(...args),
  profiles: (...args: Parameters<typeof api.listTranscriptionProfiles>) => api.listTranscriptionProfiles(...args),
  schemes: (...args: Parameters<typeof api.listTranscriptionSchemes>) => api.listTranscriptionSchemes(...args),
  jobs: (...args: Parameters<typeof api.listTranscriptionJobs>) => api.listTranscriptionJobs(...args),
  getJob: (...args: Parameters<typeof api.getTranscriptionJob>) => api.getTranscriptionJob(...args),
  cancelJob: (...args: Parameters<typeof api.cancelTranscriptionJob>) => api.cancelTranscriptionJob(...args),
  retryJob: (...args: Parameters<typeof api.retryTranscription>) => api.retryTranscription(...args),
  versions: (...args: Parameters<typeof api.listTranscriptVersions>) => api.listTranscriptVersions(...args),
  previewVersion: (...args: Parameters<typeof api.previewTranscriptVersion>) => api.previewTranscriptVersion(...args),
  previewVersionTimeline: (...args: Parameters<typeof api.previewTranscriptVersionTimeline>) => api.previewTranscriptVersionTimeline(...args),
  createRevision: (...args: Parameters<typeof api.createTranscriptRevision>) => api.createTranscriptRevision(...args),
  reviewVersion: (...args: Parameters<typeof api.reviewTranscriptVersion>) => api.reviewTranscriptVersion(...args),
  publishVersion: (...args: Parameters<typeof api.publishTranscriptVersion>) => api.publishTranscriptVersion(...args),
  publicationJob: (...args: Parameters<typeof api.getTranscriptPublicationJob>) => api.getTranscriptPublicationJob(...args),
};
