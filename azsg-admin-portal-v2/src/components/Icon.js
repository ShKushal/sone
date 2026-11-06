import React from 'react';

// One consistent set of interface icons: 24×24 outlines that take the colour of the text around them.
// No library needed. To add one, add its shapes below and use <Icon name="…" />.
const SHAPES = {
  chart:    <><path d="M5 21V11" /><path d="M12 21V3" /><path d="M19 21V15" /></>,
  lifebuoy: <><circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="3.5" /><path d="M5.6 5.6l3.9 3.9M14.5 14.5l3.9 3.9M18.4 5.6l-3.9 3.9M9.5 14.5l-3.9 3.9" /></>,
  mail:     <><rect x="3" y="5" width="18" height="14" rx="2" /><path d="M3.5 7l8.5 6 8.5-6" /></>,
  logout:   <><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" /><path d="M16 17l5-5-5-5" /><path d="M21 12H9" /></>,
  user:     <><circle cx="12" cy="8" r="4" /><path d="M4 21a8 8 0 0 1 16 0" /></>,
  alert:    <><path d="M12 3l10 18H2L12 3z" /><path d="M12 10v5" /><path d="M12 18.2v.1" /></>,
  search:   <><circle cx="11" cy="11" r="7" /><path d="M20 20l-3.5-3.5" /></>,
  enter:    <><path d="M20 5v7a3 3 0 0 1-3 3H5" /><path d="M9 11l-4 4 4 4" /></>,
  menu:     <><path d="M4 6h16" /><path d="M4 12h16" /><path d="M4 18h16" /></>,
  close:    <><path d="M6 6l12 12" /><path d="M18 6L6 18" /></>,
  copy:     <><rect x="9" y="9" width="11" height="11" rx="2" /><path d="M5 15V6a2 2 0 0 1 2-2h9" /></>,
  link:     <><path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1" /><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1" /></>,
  clock:    <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
  migrate:  <><path d="M4 7h13" /><path d="M14 4l3 3-3 3" /><path d="M20 17H7" /><path d="M10 14l-3 3 3 3" /></>,
};

/**
 * <Icon name="mail" />                  decorative (hidden from screen readers; put the meaning in text next to it)
 * <Icon name="logout" title="Sign out" />  an icon that stands alone: gets a label for screen readers
 * An unknown name is shown as plain text, so an emoji put in the configuration still appears.
 */
export default function Icon({ name, size = 18, title }) {
  const shapes = SHAPES[name];
  if (!shapes) return <span className="icon-fallback">{name}</span>;
  return (
    <svg
      className="icon"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      focusable="false"
      aria-hidden={title ? undefined : 'true'}
      role={title ? 'img' : undefined}
      aria-label={title}
    >
      {shapes}
    </svg>
  );
}
