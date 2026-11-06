import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate }                from 'react-router-dom';
import { signOutLocal }               from '../authConfig';
import { getUserGroups }              from '../hooks/useAuth';
import { PAGES, accessiblePages, hasGroupAccess } from '../config/groups';
import Icon                           from './Icon';

const HELPDESK = PAGES.find(p => p.key === 'HELPDESK');

// "⌘K" on a Mac, "Ctrl K" elsewhere
export const SHORTCUT_LABEL = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent) ? '⌘K' : 'Ctrl K';

/**
 * Ctrl/⌘ K anywhere: jump to a section, or look a user up in the Helpdesk.
 * The user being looked up travels in router state, not the URL, so an email
 * address never lands in browser history or the CDN access log.
 */
export default function CommandPalette({ open, onClose }) {
  const navigate               = useNavigate();
  const [groups, setGroups]    = useState([]);
  const [query,  setQuery]     = useState('');
  const [active, setActive]    = useState(0);
  const returnFocus            = useRef(null);

  useEffect(() => { getUserGroups().then(setGroups); }, []);

  // fresh every time it opens, and focus goes back where it was on close
  useEffect(() => {
    if (!open) return undefined;
    returnFocus.current = document.activeElement;
    setQuery('');
    setActive(0);
    return () => { returnFocus.current?.focus?.(); };
  }, [open]);

  const items = useMemo(() => {
    const q    = query.trim();
    const ql   = q.toLowerCase();
    const list = [];

    const pages = accessiblePages(groups)
      .filter(p => !ql || p.nav.label.toLowerCase().includes(ql) || p.title.toLowerCase().includes(ql)
                       || (p.blurb || '').toLowerCase().includes(ql))
      .map(p => ({
        id: p.key, icon: p.nav.icon, section: 'Go to',
        label: p.nav.label, hint: p.blurb,
        run: () => navigate(p.route),
      }));

    const lookup = q && HELPDESK && hasGroupAccess(groups, HELPDESK.group) && {
      id: 'lookup', icon: 'search', section: 'Users',
      label: <>Look up <strong>{q}</strong> in Helpdesk</>,
      run: () => navigate(HELPDESK.route, { state: { lookup: q } }),
    };

    // something that looks like an email is a user lookup; otherwise matching sections come first
    if (lookup && q.includes('@')) list.push(lookup, ...pages);
    else                           list.push(...pages, ...(lookup ? [lookup] : []));

    if (!ql || 'sign out'.includes(ql)) {
      list.push({ id: 'signout', icon: 'logout', section: 'Account', label: 'Sign out',
                  run: () => signOutLocal().catch(console.error) });
    }
    return list;
  }, [query, groups, navigate]);

  useEffect(() => { setActive(0); }, [query]);

  if (!open) return null;

  const choose = item => { onClose(); item.run(); };

  const onKeyDown = e => {
    if (e.key === 'Escape')    { e.preventDefault(); onClose(); }
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive(i => Math.min(i + 1, items.length - 1)); }
    if (e.key === 'ArrowUp')   { e.preventDefault(); setActive(i => Math.max(i - 1, 0)); }
    if (e.key === 'Enter' && items[active]) { e.preventDefault(); choose(items[active]); }
    if (e.key === 'Tab')       e.preventDefault();   // the input is the only stop
  };

  let lastSection = null;

  return (
    <div className="cmdk-overlay" onMouseDown={onClose}>
      <div
        className="cmdk"
        role="dialog"
        aria-modal="true"
        aria-label="Search and jump to"
        onMouseDown={e => e.stopPropagation()}
        onKeyDown={onKeyDown}
      >
        <div className="cmdk-input-row">
          <Icon name="search" size={18} />
          <input
            autoFocus
            className="cmdk-input"
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="Search sections, or type an email to look up a user…"
            role="combobox"
            aria-expanded="true"
            aria-controls="cmdk-list"
            aria-activedescendant={items[active] ? `cmdk-${items[active].id}` : undefined}
            autoComplete="off"
            spellCheck="false"
          />
          <kbd className="kbd">Esc</kbd>
        </div>

        <ul className="cmdk-list" id="cmdk-list" role="listbox">
          {items.length === 0 && <li className="cmdk-empty">Nothing matches “{query}”.</li>}
          {items.map((item, i) => {
            const header = item.section !== lastSection ? item.section : null;
            lastSection = item.section;
            return (
              <React.Fragment key={item.id}>
                {header && <li className="cmdk-section" role="presentation">{header}</li>}
                <li
                  id={`cmdk-${item.id}`}
                  role="option"
                  aria-selected={i === active}
                  className={`cmdk-item${i === active ? ' active' : ''}`}
                  onMouseMove={() => setActive(i)}
                  onClick={() => choose(item)}
                >
                  <span className="cmdk-icon"><Icon name={item.icon} size={16} /></span>
                  <span className="cmdk-label">{item.label}</span>
                  {item.hint && <span className="cmdk-hint">{item.hint}</span>}
                  {i === active && <Icon name="enter" size={14} />}
                </li>
              </React.Fragment>
            );
          })}
        </ul>

        <div className="cmdk-footer">
          <span><kbd className="kbd">↑</kbd><kbd className="kbd">↓</kbd> move</span>
          <span><kbd className="kbd">Enter</kbd> open</span>
          <span><kbd className="kbd">{SHORTCUT_LABEL}</kbd> toggle</span>
        </div>
      </div>
    </div>
  );
}
