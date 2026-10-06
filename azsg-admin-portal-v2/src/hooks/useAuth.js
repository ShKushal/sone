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
 * Get API headers — the signed-in user's JWT only.
 *
 * There is deliberately NO API-key fallback: Create React App bakes every REACT_APP_* value into
 * the public JavaScript bundle, so a key here is readable by anyone who can load the site, and
 * API-key callers skip every group check. Postman / backend jobs keep using x-api-key on their own.
 * With no session, the request goes out without credentials and the API rejects it.
 */
export async function getApiHeaders() {
  try {
    const session = await fetchAuthSession();
    const token   = session.tokens?.idToken?.toString();

    if (token) {
      return {
        'Authorization': `Bearer ${token}`,
        'x-api-key':     'jwt',   // tells the authorizer to validate the Bearer token
        'Content-Type':  'application/json'
      };
    }
  } catch (e) {
    console.warn('[useAuth] could not read the session:', e);
  }

  return { 'Content-Type': 'application/json' };
}

/**
 * Check if user is in a specific group
 */
export async function isInGroup(groupName) {
  const groups = await getUserGroups();
  return groups.includes(groupName);
}
