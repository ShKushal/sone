/**
 * Group and page configuration
 *
 * GROUPS — the portal groups (Entra app roles / Cognito groups) the portal knows about. The
 *          Helpdesk page also uses these labels.
 * PAGES  — the sections in the sidebar, in order, and which group may open each one.
 * To add a page: add it to PAGES here and its component to PAGE_MAP in App.js.
 */

// Members of this group can open EVERY section, whatever other groups they have
export const SUPER_ADMIN = 'SUPER_ADMIN';

const GROUPS = {
  REPORTS_ADMIN: { name: 'REPORTS_ADMIN', label: 'Reports Admin' },
  HELPDESK:      { name: 'HELPDESK',      label: 'Helpdesk'      },
  EMAIL_LOGS:    { name: 'EMAIL_LOGS',    label: 'Email Logs'    },
};

export const PAGES = [
  {
    key:   'REPORTS',
    route: '/reports',
    group: 'REPORTS_ADMIN',
    title: 'Reports',
    blurb: 'All users, inactive users and broker firms',
    nav:   { icon: 'chart', label: 'Reports', section: 'Reporting' }
  },
  {
    key:   'HELPDESK',
    route: '/helpdesk',
    group: 'HELPDESK',
    title: 'Helpdesk',
    blurb: 'Look up a user and fix account issues',
    nav:   { icon: 'lifebuoy', label: 'Helpdesk', section: 'Support' }
  },
  {
    key:   'EMAIL_LOGS',
    route: '/email-logs',
    group: 'EMAIL_LOGS',
    title: 'Email Logs',
    blurb: 'Track delivery, bounces and failures',
    nav:   { icon: 'mail', label: 'Email Logs', section: 'Monitoring' }
  },
  // TEMPORARY — the ForgeRock → Cognito migration. After cutover delete this entry, MigrationPage
  // in App.js's PAGE_MAP and MIGRATION_REPORTS / FORGEROCK_REPORTS in ReportsPage.js.
  {
    key:   'MIGRATION',
    route: '/migration',
    group: 'REPORTS_ADMIN',
    title: 'Migration Reports',
    blurb: 'JIT migration and ForgeRock gap reports',
    nav:   { icon: 'migrate', label: 'Migration', section: 'Migration', tag: 'Temporary' }
  },
];

export const isSuperAdmin = (userGroups = []) => userGroups.includes(SUPER_ADMIN);

// SUPER_ADMIN gets every section; everyone else needs that section's own group
export const hasGroupAccess = (userGroups = [], groupName) =>
  isSuperAdmin(userGroups) || userGroups.includes(groupName);

// the portal groups (from this config) a user holds, in config order
export const accessibleGroups = (userGroups = []) =>
  Object.values(GROUPS).filter(g => hasGroupAccess(userGroups, g.name));

// the pages a user may open, in sidebar order
export const accessiblePages = (userGroups = []) =>
  PAGES.filter(p => hasGroupAccess(userGroups, p.group));

export default GROUPS;
