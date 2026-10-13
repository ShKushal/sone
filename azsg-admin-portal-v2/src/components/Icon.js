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
