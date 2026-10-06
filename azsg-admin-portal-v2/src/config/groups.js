/**
 * Group configuration
 * Add new groups here — no other files need changing
 * except App.js to add the route and page component
 */

// Members of this group can open EVERY section, whatever other groups they have
export const SUPER_ADMIN = 'SUPER_ADMIN';

const GROUPS = {
  REPORTS_ADMIN: {
    name:  'REPORTS_ADMIN',
    label: 'Reports Admin',
    route: '/reports',
    nav: {
      icon:  '📊',
      label: 'Reports',
      section: 'Reporting'
    }
  },
  HELPDESK: {
    name:  'HELPDESK',
    label: 'Helpdesk',
    route: '/helpdesk',
    nav: {
      icon:  '🛠',
      label: 'Helpdesk',
      section: 'Support'
    }
  },
  EMAIL_LOGS: {
    name:  'EMAIL_LOGS',
    label: 'Email Logs',
    route: '/email-logs',
    nav: {
      icon:  '✉️',
      label: 'Email Logs',
      section: 'Monitoring'
    }
  }
};

export const isSuperAdmin = (userGroups = []) => userGroups.includes(SUPER_ADMIN);

// SUPER_ADMIN gets every section; everyone else needs that section's own group
export const hasGroupAccess = (userGroups = [], groupName) =>
  isSuperAdmin(userGroups) || userGroups.includes(groupName);

// the sections (from this config) a user may open, in config order
export const accessibleGroups = (userGroups = []) =>
  Object.values(GROUPS).filter(g => hasGroupAccess(userGroups, g.name));

export default GROUPS;
