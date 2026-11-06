/**
 * Plain-language errors for API calls.
 *
 * The Lambdas answer a refused request with { errors: [...] } (older checks use { error }); that
 * text is written for people, so it is shown as is. Anything else (API Gateway's own answers,
 * timeouts, no network) gets a sentence that says what happened and what to do.
 */

// API Gateway's own bodies, which mean nothing to a reader
const GATEWAY_MESSAGES = new Set(['Forbidden', 'Unauthorized', 'Internal Server Error', 'Service Unavailable',
                                  'Endpoint request timed out', 'Not Found', 'Too Many Requests']);

export function friendlyError(status, data, fallback = 'Something went wrong.') {
  const fromApi = data?.errors?.[0] || data?.error || data?.message;
  if (fromApi && !GATEWAY_MESSAGES.has(fromApi)) return fromApi;

  if (status === 401 || status === 403) {
    return "You don't have access to this, or your session has ended. Refresh the page to sign in again.";
  }
  if (status === 404) return fallback;
  if (status === 429) return 'Too many requests at once. Wait a few seconds and try again.';
  if (status === 502 || status === 503 || status === 504) {
    return "The server took too long to answer. Try again in a moment.";
  }
  if (status >= 500) {
    return 'Something went wrong on the server. Try again; if it keeps happening, contact support.';
  }
  return fallback;
}

/** A fetch failure that never got an answer (offline, blocked, DNS ...). */
export function networkError(e) {
  if (e instanceof TypeError) {
    return new Error("Couldn't reach the server. Check your connection and try again.");
  }
  return e;
}

/** The response body as JSON, or null when there is none (or it is not JSON). */
export async function readJson(response) {
  try { return await response.json(); } catch { return null; }
}

/**
 * fetch + JSON + plain-language errors. Throws an Error whose message can be shown as is.
 * Returns { status, data } so a caller can treat e.g. 404 as "not found" instead of an error
 * (pass allow: [404]).
 */
export async function apiFetch(url, options = {}, { fallback, allow = [] } = {}) {
  let response;
  try {
    response = await fetch(url, options);
  } catch (e) {
    throw networkError(e);
  }
  const data = await readJson(response);
  if (!response.ok && !allow.includes(response.status)) {
    const err  = new Error(friendlyError(response.status, data, fallback));
    err.status = response.status;
    throw err;
  }
  return { status: response.status, data };
}
