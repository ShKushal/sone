import React                                      from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { useMsal, useIsAuthenticated }         from '@azure/msal-react';
import { InteractionStatus }                      from '@azure/msal-browser';
import './styles/App.css';

import { loginRequest, entraConfigured, isNoAccessError, signOutLocal } from './authConfig';
import SignInPage     from './components/SignInPage';
import CommandPalette, { SHORTCUT_LABEL } from './components/CommandPalette';
import { ToastProvider } from './components/Toast';
import Icon           from './components/Icon';
import NavBar         from './components/NavBar';
import GroupRoute     from './components/GroupRoute';
import ErrorBoundary  from './components/ErrorBoundary';
import ReportsPage, { MigrationReportsPage } from './pages/reports/ReportsPage';
import HelpdeskPage   from './pages/helpdesk/HelpdeskPage';
import EmailLogsPage  from './pages/emaillogs/EmailLogsPage';
import { PAGES, accessiblePages } from './config/groups';

// -------------------------------------------------------
// Idle timeout — after 30 minutes without activity, a 60-second warning, then sign out
// -------------------------------------------------------
const WARN_SECONDS = 60;

function useIdleTimeout(minutes = 30) {
  const [secondsLeft, setSecondsLeft] = React.useState(null);   // null = no warning showing
  const warnTimer  = React.useRef(null);
  const countdown  = React.useRef(null);
  const warningRef = React.useRef(false);

  const signOutNow = React.useCallback(async () => {
    console.log('[IDLE] Session expired — signing out');
    try { await signOutLocal(); }
    catch (e) { window.location.reload(); }
  }, []);

  const reset = React.useCallback(() => {
    clearTimeout(warnTimer.current);
    clearInterval(countdown.current);
    warningRef.current = false;
    setSecondsLeft(null);
    warnTimer.current = setTimeout(() => {
      warningRef.current = true;
      const endsAt = Date.now() + WARN_SECONDS * 1000;
      setSecondsLeft(WARN_SECONDS);
      countdown.current = setInterval(() => {
        const left = Math.max(0, Math.round((endsAt - Date.now()) / 1000));
        setSecondsLeft(left);
        if (left === 0) { clearInterval(countdown.current); signOutNow(); }
      }, 1000);
    }, Math.max(0, minutes * 60 - WARN_SECONDS) * 1000);
  }, [minutes, signOutNow]);

  React.useEffect(() => {
    // while the warning shows, only "Stay signed in" keeps the session (not a stray mouse move)
    const onActivity = () => { if (!warningRef.current) reset(); };
    const events = ['mousedown', 'keydown', 'scroll', 'touchstart'];
    events.forEach(e => window.addEventListener(e, onActivity, { passive: true }));
    reset();
    return () => {
      clearTimeout(warnTimer.current);
      clearInterval(countdown.current);
      events.forEach(e => window.removeEventListener(e, onActivity));
    };
  }, [reset]);

  return { secondsLeft, stay: reset, signOutNow };
}

function SessionWarning({ secondsLeft, onStay, onSignOut }) {
  return (
    <div className="confirm-overlay">
      <div className="confirm-card session-card" role="alertdialog" aria-modal="true"
           aria-labelledby="session-title" aria-describedby="session-body">
        <div className="session-icon"><Icon name="clock" size={22} /></div>
        <div className="confirm-title" id="session-title">Still there?</div>
        <div className="confirm-body" id="session-body">
          You've been inactive for a while. For security you'll be signed out in{' '}
          <strong className="session-count">{secondsLeft} second{secondsLeft === 1 ? '' : 's'}</strong>.
        </div>
        <div className="confirm-actions">
          <button className="btn-cancel" onClick={onSignOut}>Sign out now</button>
          <button className="btn-confirm" onClick={onStay} autoFocus>Stay signed in</button>
        </div>
      </div>
    </div>
  );
}

// -------------------------------------------------------
// Access denied
// -------------------------------------------------------
function AccessDenied() {
  return (
    <div className="access-denied">
      <div className="access-denied-content">
        <h1>Access Denied</h1>
        <p>
          You do not have permission to access this page.
          Contact your administrator to request access.
        </p>
      </div>
    </div>
  );
}

// -------------------------------------------------------
// Home — redirect based on first matching group
// -------------------------------------------------------
function Home() {
  const [redirectTo, setRedirectTo] = React.useState(null);

  React.useEffect(() => {
    import('./hooks/useAuth').then(({ getUserGroups }) => {
      getUserGroups().then(userGroups => {
        // first section this user may open (a SUPER_ADMIN gets the first of all)
        const match = accessiblePages(userGroups)[0];
        setRedirectTo(match ? match.route : '/access-denied');
      });
    });
  }, []);

  if (!redirectTo) return <div className="loading">Loading...</div>;
  return <Navigate to={redirectTo} replace />;
}

// -------------------------------------------------------
// Topbar
// -------------------------------------------------------
// The environment this build is for ("Dev", "Preprod", ...), set with REACT_APP_ENV_NAME.
// Leave it unset to show no badge — e.g. in production.
const ENV_NAME = (process.env.REACT_APP_ENV_NAME || '').trim();

// the shell's controls (open the search, open the menu on small screens), used by the top bar
const ShellContext = React.createContext({ openSearch: () => {}, openNav: () => {} });

function Topbar({ title }) {
  const { openSearch, openNav } = React.useContext(ShellContext);
  return (
    <div className="topbar">
      <button className="topbar-menu" onClick={openNav} aria-label="Open menu">
        <Icon name="menu" size={20} />
      </button>
      <span className="page-title">{title}</span>
      <div className="topbar-right">
        <button className="search-trigger" onClick={openSearch} aria-label={`Search (${SHORTCUT_LABEL})`}>
          <Icon name="search" size={16} />
          <span className="search-trigger-text">Search or look up a user</span>
          <kbd className="kbd">{SHORTCUT_LABEL}</kbd>
        </button>
        {ENV_NAME && <span className="badge-env">{ENV_NAME}</span>}
      </div>
    </div>
  );
}

// -------------------------------------------------------
// Page map — the component for each page in PAGES (config/groups.js)
// -------------------------------------------------------
const PAGE_MAP = {
  REPORTS:    ReportsPage,
  HELPDESK:   HelpdeskPage,
  EMAIL_LOGS: EmailLogsPage,
  MIGRATION:  MigrationReportsPage,   // TEMPORARY — remove after the ForgeRock cutover
};

// -------------------------------------------------------
// App content — logged in view
// -------------------------------------------------------
function AppContent({ user }) {
  const idle = useIdleTimeout(30);
  const [searchOpen, setSearchOpen] = React.useState(false);
  const [navOpen,    setNavOpen]    = React.useState(false);

  // Ctrl/⌘ K opens the search from anywhere; Esc closes the menu on small screens
  React.useEffect(() => {
    const onKey = e => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setSearchOpen(o => !o);
      }
      if (e.key === 'Escape') setNavOpen(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const shell = React.useMemo(() => ({
    openSearch: () => setSearchOpen(true),
    openNav:    () => setNavOpen(true),
  }), []);

  return (
    <BrowserRouter>
     <ToastProvider>
      <ShellContext.Provider value={shell}>
      <div className={`shell${navOpen ? ' nav-open' : ''}`}>

        <NavBar user={user} onNavigate={() => setNavOpen(false)} onClose={() => setNavOpen(false)} />
        {navOpen && <div className="nav-backdrop" onClick={() => setNavOpen(false)} aria-hidden="true" />}

        <div className="main">
          <ErrorBoundary>
          <Routes>
            <Route path="/" element={<Home />} />

            {/* Dynamic routes from the PAGES config */}
            {PAGES.map(page => {
              const PageComponent = PAGE_MAP[page.key];
              if (!PageComponent) return null;
              return (
                <Route
                  key={page.key}
                  path={page.route}
                  element={
                    <GroupRoute group={page.group}>
                      <Topbar title={page.title} />
                      <div className="content">
                        <PageComponent />
                      </div>
                    </GroupRoute>
                  }
                />
              );
            })}

            <Route path="/access-denied" element={
              <>
                <Topbar title="Access Denied" />
                <div className="content"><AccessDenied /></div>
              </>
            } />

            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
          </ErrorBoundary>
        </div>

      </div>

      <CommandPalette open={searchOpen} onClose={() => setSearchOpen(false)} />
      {idle.secondsLeft !== null && (
        <SessionWarning secondsLeft={idle.secondsLeft} onStay={idle.stay} onSignOut={idle.signOutNow} />
      )}
      </ShellContext.Provider>
     </ToastProvider>
    </BrowserRouter>
  );
}

// -------------------------------------------------------
// Main App
// -------------------------------------------------------
// Employees sign in with Microsoft Entra ID. Until then: the sign-in page, which also explains
// "not assigned to this application" (AADSTS50105) and other sign-in failures.
export default function App({ redirectError = null }) {
  const { instance, accounts, inProgress } = useMsal();
  const isAuthenticated = useIsAuthenticated();
  const [status, setStatus] = React.useState(() =>
    !entraConfigured ? 'not-configured'
      : redirectError ? (isNoAccessError(redirectError) ? 'no-access' : 'error')
      : 'idle'
  );

  React.useEffect(() => {
    if (redirectError && !isNoAccessError(redirectError)) {
      console.warn('[AUTH] sign-in failed:', redirectError.errorCode || redirectError.message);
    }
  }, [redirectError]);

  const signIn = () => {
    setStatus('redirecting');
    instance.loginRedirect(loginRequest).catch(e => {
      console.warn('[AUTH] could not start sign-in:', e.errorCode || e.message);
      setStatus(isNoAccessError(e) ? 'no-access' : 'error');
    });
  };

  if (isAuthenticated && entraConfigured) {
    const account = instance.getActiveAccount() || accounts[0];
    return <AppContent user={account} />;
  }

  if (inProgress !== InteractionStatus.None && status !== 'not-configured' && status !== 'no-access') {
    return <div className="loading">Signing in…</div>;
  }

  return <SignInPage status={status} envName={ENV_NAME} onSignIn={signIn} />;
}
