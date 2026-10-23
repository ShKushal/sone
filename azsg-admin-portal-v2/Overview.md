# AZSG Admin Portal — frontend overview

For a developer picking up the admin portal. Read this first, then open the code in the order given under
"Where to start reading".

## 1. What it is

An internal web app for the AZSG identity team. People sign in with their Cognito account and see only the
sections their Cognito **groups** allow:

| Section | Route | What it does | Group needed |
|---|---|---|---|
| Reports | `/reports` | Run and download user, organisation and migration reports | `REPORTS_ADMIN` |
| Helpdesk | `/helpdesk` | Find a user, edit name/phone, reset password, enable/disable, unblock email, see email history, manage portal groups | `HELPDESK` |
| Email Logs | `/email-logs` | Delivery activity across all recipients (delivered, bounced, complaints…) | `EMAIL_LOGS` |

`SUPER_ADMIN` sees every section and is the only role that can change another user's portal groups.

## 2. Stack (deliberately small)

React 18 (function components and hooks) · React Router 6 · AWS Amplify 6 (the sign-in screen) · Create React App 5
(`react-scripts`) · plain JavaScript · one stylesheet (`styles/App.css`) · plain `fetch`.
No UI library, no state library, no TypeScript. About 4,700 lines in 15 files.

## 3. Run it

```
npm install
npm run start:dev        # local, against dev          (start:preprod for preprod)
npm run build:preprod    # production build for an environment
```

Each environment has its own settings file (`.env.dev`, `.env.preprod`, `.env.prod`) and its own build — the
values are baked in at build time, so one build cannot serve two environments. There is no default `.env` on
purpose: a missing variable should fail loudly, not silently fall back to another environment.

| Variable | Meaning |
|---|---|
| `REACT_APP_USER_POOL_ID`, `REACT_APP_CLIENT_ID`, `REACT_APP_REGION` | The Cognito pool and app client for that environment |
| `REACT_APP_API_URL` | The API Gateway URL (including the stage) |
| `REACT_APP_ENV_NAME` | Text for the badge in the top bar (`Dev`, `Preprod`). Leave unset for no badge (production) |
| `PORT` | Local port. Use `4000`: the API's CORS list allows `http://localhost:4000` |

On Amplify, set the same variables in the console, per branch (one branch per environment). No settings files are
committed there.

## 4. Where things are

```
src/
  index.js, App.js            App shell: Authenticator (sign-in), routes, top bar, idle timeout, sign-in header
  aws-exports.js              Amplify/Cognito settings, read from the REACT_APP_* variables
  config/groups.js            THE list of sections: name, route, label, icon, nav section. Also the role helpers
  hooks/useAuth.js            getApiHeaders() (adds the token to every API call), getCurrentUser(), getUserGroups()
  components/
    NavBar.js                 Sidebar, built from config/groups.js; shows only the sections you may open
    GroupRoute.js             Blocks a route unless you hold the group (SUPER_ADMIN passes everywhere)
    ErrorBoundary.js          Shows an error screen instead of a blank page if a page crashes
    DateTime.js               <DateTime value=…/> — ALWAYS use this (or utils/dates.js) to show a date
    Icon.js                   The icon set (small inline SVGs); add a shape here to add an icon
  utils/dates.js              Date formatting in the viewer's own time zone, zone always stated
  pages/
    helpdesk/HelpdeskPage.js  The largest page (user list + user card)
    emaillogs/EmailLogsPage.js
    reports/ReportsPage.js
  styles/App.css              All styling. CSS variables at the top (colours, radius…)
public/allianz-logo.png       Logo on the sign-in screen
```

## 5. How a request is authenticated

1. Amplify's `Authenticator` signs the person in with Cognito.
2. The ID token carries their groups (`cognito:groups`).
3. `getApiHeaders()` sends `Authorization: Bearer <id token>` and `x-api-key: jwt` with every call. The value `jwt`
   tells the API Gateway authorizer to validate the token instead of an API key.
4. The API checks the groups again for each action.

**Important:** hiding a button or a route in the UI is only a convenience. The API is the real gate. Never rely on
the front end to protect something, and never remove a server-side check because "the UI already hides it".

Other behaviour to know: after **30 minutes without activity the person is signed out** (`useIdleTimeout` in `App.js`).

## 6. API calls each page makes

| Page | Calls |
|---|---|
| Helpdesk | `GET /users`, `GET /users/{u}`, `PUT /users/{u}`, `POST /users/{u}/reset-password`, `POST /users/{u}/email-unblock`, `GET /users/{u}/email-logs`, `GET /users/{u}/groups`, `PUT /users/{u}/groups` |
| Email Logs | `GET /email-logs?…` |
| Reports | `GET /reports/…` (users, inactive, organisations, migration, migration/pending, migration/complete, migration/summary, forgerock/organisations, forgerock/gap), `GET /broker-firms` |

The two ForgeRock reports are **temporary**: remove their cards from `ReportsPage.js` (and the backend routes) after
the cutover.

## 7. Rules and conventions in this code

- **Organisation of a user** is `custom:frUnindexedString5` (the source of truth). The older `custom:organisation`
  is shown only as a fallback for users migrated earlier.
- **Portal groups** (`REPORTS_ADMIN`, `HELPDESK`, `EMAIL_LOGS`, `SUPER_ADMIN`) are recognised by `[portal]` at the
  start of their Cognito group description. The page asks the API for the list; no group names are hard-coded in
  the Groups section. Broker-firm groups are shown read-only.
- **Nobody edits their own account** in the helpdesk (the card shows a notice). The API enforces this too.
- **Dates**: shown in the browser's time zone with the zone written (`GMT+8`), the month always as a word. Use
  `DateTime`; do not call `toLocaleString()` directly.
- **Readability**: secondary text uses `var(--text-muted)` (4.5:1 contrast on every background); no text below
  12px. Keep this when adding styles. Don't put emoji in the interface; use `Icon`.
- **Adding a section** (three steps): (1) create the page component; (2) add an entry to `config/groups.js`
  (name, route, label, nav icon and section); (3) add it to `PAGE_MAP` in `App.js`. The navigation, the route
  and the access check then follow automatically. Create the Cognito group with a `[portal]` description, and
  the backend must enforce the same group.
- **Adding an environment variable**: it must start with `REACT_APP_`, be added to every `.env.*` file and to
  Amplify, and the app must be rebuilt.

## 8. Things that have caught people out

- **CORS**: each environment's API only accepts the portal URLs on its allowed-origins list. A "network error" with
  nothing else in the console is usually this. Add `http://localhost:4000` to a new environment's list.
- **A hard-coded value for one environment** is the main risk with several environments. Everything that differs
  lives in the `.env.*` files, and nowhere else.
- **Sign-in logo**: `public/allianz-logo.png`; the sign-in header is `components.Header` in `App.js`.
- **Automated tests are not in this repository yet.** During development about 300 checks (helpdesk, email logs,
  reports, access control, dates, icons) were run from a separate setup. Moving them into the repo, and running them
  on every push, is the most valuable next step for maintainability.

## 9. Where to start reading

1. `config/groups.js` and `App.js` — what exists and how it is wired (15 minutes).
2. `hooks/useAuth.js` — how calls are authenticated.
3. `pages/emaillogs/EmailLogsPage.js` — the simplest full page (one endpoint, filters, a table).
4. `pages/helpdesk/HelpdeskPage.js` — the most involved page; read `UserCard` last.
