// Deployment state is supplied by this server, never a bundled endpoint or token.
export const SERVER_CONFIG = Object.freeze({enabled: false, endpoint: null, recipient: null, privacySummary: null});

export function validateDeploymentConfig(data, pageOrigin) {
  if (!data || data.schemaVersion !== 1 || typeof data.enabled !== 'boolean') {
    throw new Error('Server configuration is unavailable');
  }
  if (!data.enabled) return SERVER_CONFIG;
  const origin = new URL(pageOrigin);
  if (origin.protocol !== 'https:' || origin.origin !== pageOrigin ||
      data.mode !== 'anonymous-preview' || data.origin !== pageOrigin || data.apiBase !== '/v1/' ||
      typeof data.recipient !== 'string' || !data.recipient.trim() ||
      typeof data.privacySummary !== 'string' || !data.privacySummary.trim()) {
    throw new Error('Server search has not been activated for this origin');
  }
  return Object.freeze({enabled: true, endpoint: new URL('/v1/', origin).href,
    recipient: data.recipient, privacySummary: data.privacySummary});
}

export async function loadDeploymentConfig({pageOrigin, fetcher = fetch} = {}) {
  const response = await fetcher(new URL('/deployment-config.json', pageOrigin), {
    credentials: 'same-origin', mode: 'same-origin', cache: 'no-store', redirect: 'error',
  });
  if (!response.ok) throw new Error('Server configuration is unavailable');
  return validateDeploymentConfig(await response.json(), pageOrigin);
}
