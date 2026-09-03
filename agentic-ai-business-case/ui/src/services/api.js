import axios from 'axios';
import { getApiUrl } from '../utils/apiConfig';

const API_BASE_URL = getApiUrl();

const POLL_INTERVAL_MS = 5000;
const POLL_TIMEOUT_MS = 45 * 60 * 1000; // generation is minutes, not seconds

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// The browser needs a case id before it can ask for upload URLs, so it picks
// one rather than waiting for the server to assign it. Matches the server's
// accepted shape: alphanumeric, hyphens and underscores only.
const newCaseId = () => {
  const t = new Date().toISOString().replace(/[-:T]/g, '').slice(0, 14);
  return `case-${t.slice(0, 8)}-${t.slice(8)}`;
};

/**
 * Upload one file straight to S3 using a presigned URL.
 *
 * Files deliberately do not pass through the application: the backend runs as a
 * Lambda, whose request body is capped at 6 MB, and a real RVTools export
 * exceeds that easily.
 */
const uploadViaPresignedUrl = async (caseId, file) => {
  const { data } = await axios.post(`${API_BASE_URL}/upload-url`, {
    caseId,
    filename: file.name,
  });

  if (!data?.success || !data.url) {
    throw new Error(data?.message || `Could not get an upload URL for ${file.name}`);
  }

  // Plain fetch with credentials omitted: S3 must not receive the app's session
  // cookie, and the presigned URL carries its own authorisation.
  const response = await fetch(data.url, {
    method: 'PUT',
    body: file,
    credentials: 'omit',
  });

  if (!response.ok) {
    throw new Error(`Upload of ${file.name} failed (${response.status})`);
  }

  return data.filename;
};

/**
 * Generate a business case.
 *
 * Generation runs as a separate Fargate task and can take many minutes, so this
 * queues the job and polls until it finishes. The resolved shape is unchanged
 * from when generation was a single blocking request, so callers do not need to
 * know any of this happened.
 *
 * `onProgress` is optional and receives the raw status payload on each poll.
 */
export const generateBusinessCase = async ({
  projectInfo,
  uploadedFiles,
  selectedAgents,
  onProgress,
}) => {
  const caseId = newCaseId();

  // 1. Put every file in S3 first.
  const uploaded = {};
  for (const [key, value] of Object.entries(uploadedFiles)) {
    if (!value) continue;
    if (Array.isArray(value)) {
      const names = [];
      for (const file of value) {
        if (file) names.push(await uploadViaPresignedUrl(caseId, file));
      }
      if (names.length) uploaded[key] = names;
    } else {
      uploaded[key] = await uploadViaPresignedUrl(caseId, value);
    }
  }

  // 2. Queue the job. Returns immediately with a job id.
  const { data: queued } = await axios.post(`${API_BASE_URL}/generate`, {
    caseId,
    projectInfo,
    selectedAgents,
    uploadedFiles: uploaded,
  });

  if (!queued?.success) {
    throw new Error(queued?.message || 'Failed to queue business case generation');
  }

  // 3. Poll until the task reports a terminal state.
  const startedAt = Date.now();
  while (Date.now() - startedAt < POLL_TIMEOUT_MS) {
    await sleep(POLL_INTERVAL_MS);

    const status = await checkStatus(queued.jobId, queued.createdAt);
    if (onProgress) onProgress(status);

    if (status.status === 'COMPLETED') {
      return {
        success: true,
        content: status.content,
        projectInfo,
        caseId: status.caseId,
        createdAt: status.createdAt,
        agentsExecuted: status.executionStats?.agentsExecuted ?? selectedAgents.length,
        executionTime: status.executionStats?.executionTime ?? 'N/A',
        tokenUsage: status.executionStats?.tokenUsage ?? 'N/A',
        outputS3Keys: status.outputS3Keys || null,
        uploadedFiles: Object.keys(uploaded),
      };
    }

    if (status.status === 'FAILED') {
      throw new Error(status.message || 'Business case generation failed');
    }
  }

  throw new Error(
    'Generation is taking longer than expected. It may still finish — check saved cases shortly.'
  );
};

export const checkStatus = async (jobId, createdAt) => {
  try {
    const response = await axios.get(`${API_BASE_URL}/status/${jobId}`, {
      params: createdAt ? { createdAt } : undefined,
    });
    return response.data;
  } catch (error) {
    console.error('Status check error:', error);
    throw new Error(error.response?.data?.message || 'Failed to check generation status');
  }
};
