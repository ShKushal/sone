import React from 'react';

// Microsoft's four-square mark, as Microsoft's sign-in button guidelines require
function MicrosoftMark() {
  return (
    <svg width="20" height="20" viewBox="0 0 21 21" aria-hidden="true" focusable="false">
      <rect x="1"  y="1"  width="9" height="9" fill="#f25022" />
      <rect x="11" y="1"  width="9" height="9" fill="#7fba00" />
      <rect x="1"  y="11" width="9" height="9" fill="#00a4ef" />
      <rect x="11" y="11" width="9" height="9" fill="#ffb900" />
    </svg>
  );
}

/**
 * Employee sign-in through Microsoft Entra ID (MSAL redirect).
 *   status: 'idle' | 'redirecting' | 'no-access' | 'error' | 'not-configured'
 */
export default function SignInPage({ status = 'idle', envName, onSignIn, contact = 'the portal owner' }) {
  const busy = status === 'redirecting';

  return (
    <div className="signin-page">
      <main className="signin-card" aria-labelledby="signin-title">
        <div className="signin-header">
          <img className="signin-logo" src={`${process.env.PUBLIC_URL}/allianz-logo.png`} alt="Allianz" />
          <h1 className="signin-title" id="signin-title">Admin Portal</h1>
          <p className="signin-sub">Identity platform administration</p>
          {envName && <span className="signin-env">{envName}</span>}
        </div>

        {status === 'not-configured' ? (
          <div className="signin-body">
            <div className="signin-alert" role="alert">
              <strong>Sign-in isn't set up for this environment</strong>
              <span>REACT_APP_ENTRA_TENANT_ID, REACT_APP_ENTRA_CLIENT_ID and REACT_APP_ENTRA_API_SCOPE
                must be set in this environment's .env file.</span>
            </div>
          </div>
        ) : status === 'no-access' ? (
          <div className="signin-body">
            <div className="signin-alert" role="alert">
              <strong>You don't have access to this portal</strong>
              <span>Your Allianz account signed in, but it isn't assigned to this application.
                Ask {contact} to add you.</span>
            </div>
            <button type="button" className="signin-secondary" onClick={onSignIn}>
              Sign in with a different account
            </button>
          </div>
        ) : (
          <div className="signin-body">
            {status === 'error' && (
              <div className="signin-alert" role="alert">
                <strong>Sign-in didn't complete</strong>
                <span>Something went wrong with Microsoft sign-in. Please try again.</span>
              </div>
            )}
            <button type="button" className="signin-ms" onClick={onSignIn} disabled={busy} aria-busy={busy}>
              {busy ? <span className="signin-spinner" aria-hidden="true" /> : <MicrosoftMark />}
              <span>{busy ? 'Redirecting to Microsoft…' : 'Sign in with Microsoft'}</span>
            </button>
            <p className="signin-hint">Use your Allianz work account. Access is limited to authorised staff.</p>
          </div>
        )}

        <footer className="signin-footer">Protected by Microsoft Entra ID · single sign-on and MFA</footer>
      </main>
    </div>
  );
}
