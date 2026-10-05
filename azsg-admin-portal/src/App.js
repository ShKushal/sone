import React                                      from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { Amplify }                                from 'aws-amplify';
import { Authenticator }                          from '@aws-amplify/ui-react';
import '@aws-amplify/ui-react/styles.css';
import './styles/App.css';

import awsconfig      from './aws-exports';
import NavBar         from './components/NavBar';
import GroupRoute     from './components/GroupRoute';
import ErrorBoundary  from './components/ErrorBoundary';
import ReportsPage    from './pages/reports/ReportsPage';
import HelpdeskPage   from './pages/helpdesk/HelpdeskPage';
import GROUPS         from './config/groups';

Amplify.configure(awsconfig);

// -------------------------------------------------------
// Idle timeout — sign out after 30 mins of inactivity
// -------------------------------------------------------
function useIdleTimeout(minutes = 30) {
  React.useEffect(() => {
    let timer;

    const reset = () => {
      clearTimeout(timer);
      timer = setTimeout(async () => {
        console.log('[IDLE] Session expired — signing out');
        try { await import('aws-amplify/auth').then(m => m.signOut()); }
        catch (e) { window.location.reload(); }
      }, minutes * 60 * 1000);
    };

    const events = ['mousedown', 'keypress', 'scroll', 'touchstart', 'click'];
    events.forEach(e => window.addEventListener(e, reset));
    reset();

    return () => {
      clearTimeout(timer);
      events.forEach(e => window.removeEventListener(e, reset));
    };
  }, [minutes]);
}

// -------------------------------------------------------
// Custom login header
// -------------------------------------------------------
const components = {};

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
        // find first group in config that user belongs to
        const match = Object.values(GROUPS).find(g =>
          userGroups.includes(g.name)
        );
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
function Topbar({ title }) {
  return (
    <div className="topbar">
      <span className="page-title">{title}</span>
      <div className="topbar-right">
        <span className="badge-env">Staging</span>
      </div>
    </div>
  );
}

// -------------------------------------------------------
// Page map — add new pages here when adding new groups
// -------------------------------------------------------
const PAGE_MAP = {
  REPORTS_ADMIN: {
    title:     'Reports',
    component: ReportsPage
  },
  HELPDESK: {
    title:     'Helpdesk',
    component: HelpdeskPage
  }
};

// -------------------------------------------------------
// App content — logged in view
// -------------------------------------------------------
function AppContent({ user }) {
  useIdleTimeout(30);

  return (
    <BrowserRouter>
      <div className="shell">

        <NavBar user={user} />

        <div className="main">
          <ErrorBoundary>
          <Routes>
            <Route path="/" element={<Home />} />

            {/* Dynamic routes from GROUPS config */}
            {Object.values(GROUPS).map(group => {
              const page = PAGE_MAP[group.name];
              if (!page) return null;
              const PageComponent = page.component;
              return (
                <Route
                  key={group.name}
                  path={group.route}
                  element={
                    <GroupRoute group={group.name}>
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
    </BrowserRouter>
  );
}

// -------------------------------------------------------
// Main App
// -------------------------------------------------------
export default function App() {
  return (
    <Authenticator
      loginMechanisms={['email']}
      hideSignUp={true}
      components={components}
    >
      {({ signOut, user }) => (
        <AppContent user={user} />
      )}
    </Authenticator>
  );
}
