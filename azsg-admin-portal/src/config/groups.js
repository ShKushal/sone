/**
 * Group configuration
 * Add new groups here — no other files need changing
 * except App.js to add the route and page component
 */

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
  }
};

export default GROUPS;
