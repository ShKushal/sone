import { InteractionRequiredAuthError } from '@azure/msal-browser';
import { msalInstance, apiRequest }     from '../authConfig';

/**
 * The signed-in employee's access token for the portal API (Microsoft Entra ID).
 * MSAL returns it from its cache and renews it silently; only when that is impossible (session
 * ended, consent changed) does the browser go back to Microsoft to sign in again.
 */
async function getAccessToken() {
  const account = msalInstance.getActiveAccount();
  if (!account) return null;
  try {
    const result = await msalInstance.acquireTokenSilent({ ...apiRequest, account });
    return result.accessToken;
  } catch (e) {
    if (e instanceof InteractionRequiredAuthError) {
      await msalInstance.acquireTokenRedirect({ ...apiRequest, account });
      return null;
    }
    console.warn('[useAuth] could not get a token:', e);
    return null;
  }
}

function claimsOf(token) {
  try {
    const part = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
    const json = decodeURIComponent(
      atob(part + '==='.slice((part.length + 3) % 4))
        .split('').map(c => '%' + c.charCodeAt(0).toString(16).padStart(2, '0')).join('')
    );
    return JSON.parse(json);
  } catch {
    return {};
  }
}

/**
 * Who is signed in — from the access token the API also sees, so the page can recognise "this is me".
 * The app roles assigned in Entra (e.g. SUPER_ADMIN) are the portal's groups.
 */
export async function getCurrentUser() {
  const token = await getAccessToken();
  const c     = token ? claimsOf(token) : {};
  const login = String(c.email || c.preferred_username || msalInstance.getActiveAccount()?.username || '');
  return {
    username: String(c.preferred_username || login),
    email:    login,
    name:     String(c.name || msalInstance.getActiveAccount()?.name || ''),
    sub:      String(c.sub || ''),
    groups:   Array.isArray(c.roles) ? c.roles : [],
  };
}

/** The signed-in employee's portal groups (their Entra app roles). */
export async function getUserGroups() {
  return (await getCurrentUser()).groups;
}

/**
 * Get API headers — the signed-in employee's access token only.
 *
 * There is deliberately NO API-key fallback: Create React App bakes every REACT_APP_* value into
 * the public JavaScript bundle, so a key here is readable by anyone who can load the site, and
 * API-key callers skip every group check. Postman / backend jobs keep using x-api-key on their own.
 * With no session, the request goes out without credentials and the API rejects it.
 */
export async function getApiHeaders() {
  const token = await getAccessToken();
  if (token) {
    return {
      'Authorization': `Bearer ${token}`,
      'x-api-key':     'jwt',   // tells the authorizer to validate the Bearer token
      'Content-Type':  'application/json'
    };
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
