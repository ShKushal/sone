import { PublicClientApplication, EventType } from '@azure/msal-browser';

/**
 * Employee sign-in with Microsoft Entra ID (MSAL, authorization code flow with PKCE).
 *
 * Set per environment in .env.*:
 *   REACT_APP_ENTRA_TENANT_ID   Directory (tenant) ID
 *   REACT_APP_ENTRA_CLIENT_ID   Application (client) ID of the portal's app registration
 *   REACT_APP_ENTRA_API_SCOPE   api://<client ID>/access_as_user  (the "Expose an API" scope)
 *
 * The redirect URI is this site's own root (e.g. http://localhost:4000/); it must be registered,
 * character for character, as a "Single-page application" redirect URI on the app registration.
 */
const TENANT_ID = (process.env.REACT_APP_ENTRA_TENANT_ID || '').trim();
const CLIENT_ID = (process.env.REACT_APP_ENTRA_CLIENT_ID || '').trim();
export const API_SCOPE = (process.env.REACT_APP_ENTRA_API_SCOPE || '').trim();

export const entraConfigured = Boolean(TENANT_ID && CLIENT_ID && API_SCOPE);

const ROOT = `${window.location.origin}/`;

export const msalInstance = new PublicClientApplication({
  auth: {
    clientId:              CLIENT_ID || 'not-configured',
    authority:             `https://login.microsoftonline.com/${TENANT_ID || 'organizations'}`,
    redirectUri:           ROOT,
    postLogoutRedirectUri: ROOT,
  },
  cache: {
    // per browser tab, gone when the tab closes; nothing kept in localStorage
    cacheLocation: 'sessionStorage',
  },
});

// Sign-in asks for our API's scope straight away, so the first access token is ready on return
// (openid, profile and offline_access are added by MSAL).
export const loginRequest = {
  scopes: [API_SCOPE],
  prompt: 'select_account',
};

export const apiRequest = { scopes: [API_SCOPE] };

// Remember who signed in, so token requests know which account to use
msalInstance.addEventCallback(event => {
  if (
    (event.eventType === EventType.LOGIN_SUCCESS ||
     event.eventType === EventType.ACQUIRE_TOKEN_SUCCESS) &&
    event.payload?.account
  ) {
    msalInstance.setActiveAccount(event.payload.account);
  }
});

/** Sets up MSAL and finishes a sign-in that is coming back from Microsoft. Call before rendering. */
export async function initAuth() {
  await msalInstance.initialize();
  let redirectError = null;
  try {
    const result = await msalInstance.handleRedirectPromise();
    if (result?.account) msalInstance.setActiveAccount(result.account);
  } catch (e) {
    redirectError = e;
  }
  if (!msalInstance.getActiveAccount()) {
    const [first] = msalInstance.getAllAccounts();
    if (first) msalInstance.setActiveAccount(first);
  }
  return { redirectError };
}

/** AADSTS50105: signed in to Microsoft, but not assigned to this application. */
export function isNoAccessError(error) {
  const text = `${error?.errorCode || ''} ${error?.errorMessage || error?.message || ''}`;
  return /AADSTS50105/.test(text);
}

/**
 * Sign out of the portal only: forget this tab's tokens, without signing the person out of
 * Microsoft (Teams, Outlook ... stay signed in). The next sign-in still asks which account to use.
 */
export async function signOutLocal() {
  await msalInstance.logoutRedirect({
    account:            msalInstance.getActiveAccount() || undefined,
    onRedirectNavigate: () => false,
  });
  window.location.replace(ROOT);
}
