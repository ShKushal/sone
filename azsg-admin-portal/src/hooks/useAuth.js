import { fetchAuthSession } from 'aws-amplify/auth';

/**
 * Get current user groups from JWT token
 */
export async function getUserGroups() {
  try {
    const session = await fetchAuthSession();
    const payload = session.tokens?.idToken?.payload;
    return payload?.['cognito:groups'] || [];
  } catch {
    return [];
  }
}

/**
 * Get API headers — prefers JWT, falls back to API key
 *
 * React app sends JWT token so no static API key
 * is exposed in the browser network tab.
 *
 * Postman/backend still uses x-api-key — no change needed there.
 */
export async function getApiHeaders() {
  try {
    const session = await fetchAuthSession();
    const token   = session.tokens?.idToken?.toString();

    if (token) {
      return {
        'Authorization': `Bearer ${token}`,
        'x-api-key':     'jwt',   // triggers authorizer, Lambda validates JWT
        'Content-Type':  'application/json'
      };
    }
  } catch (e) {
    console.warn('[useAuth] JWT fetch failed, falling back to API key:', e);
  }

  // fallback to API key if JWT not available
  return {
    'x-api-key':    process.env.REACT_APP_API_KEY || '',
    'Content-Type': 'application/json'
  };
}

/**
 * Check if user is in a specific group
 */
export async function isInGroup(groupName) {
  const groups = await getUserGroups();
  return groups.includes(groupName);
}
