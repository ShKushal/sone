import React, { useEffect, useState } from 'react';
import { NavLink }                    from 'react-router-dom';
import { signOutLocal }               from '../authConfig';
import { getUserGroups }              from '../hooks/useAuth';
import { accessibleGroups, accessiblePages, isSuperAdmin } from '../config/groups';
import Icon                           from './Icon';

export default function NavBar({ user, onNavigate, onClose }) {
  const [groups, setGroups] = useState([]);

  useEffect(() => {
    getUserGroups().then(setGroups);
  }, []);

  const handleSignOut = async () => {
    try { await signOutLocal(); }
    catch (e) { console.error(e); }
  };

  // user = the signed-in Microsoft account (MSAL): username is the sign-in name, name the display name
  const email    = user?.username || '';
  const name     = (user?.name || '').trim();
  const initials = (name
    ? name.split(/\s+/).map(w => w[0]).join('').slice(0, 2)
    : email.slice(0, 2)
  ).toUpperCase();

  // the pages this user may open (a SUPER_ADMIN gets all of them), and the groups they hold
  const userPages  = accessiblePages(groups);
  const userGroups = accessibleGroups(groups);

  // group nav items by section
  const sections = userPages.reduce((acc, p) => {
    const section = p.nav.section;
    if (!acc[section]) acc[section] = [];
    acc[section].push(p);
    return acc;
  }, {});

  // role label
  const roleLabel = isSuperAdmin(groups)
    ? 'Super Admin'
    : (userGroups.map(g => g.label).join(' · ') || 'No role assigned');

  return (
    <aside className="sidebar">

      {/* Brand */}
      <div className="sidebar-brand">
        <div className="brand-mark">
          <div className="brand-icon">AZ</div>
          <div className="brand-text">
            <span className="brand-name">AZSG Admin</span>
            <span className="brand-sub">Identity Portal</span>
          </div>
        </div>
        {/* small screens: the sidebar opens over the page, so it needs its own close button */}
        <button className="sidebar-close" onClick={onClose} aria-label="Close menu">
          <Icon name="close" size={20} />
        </button>
      </div>

      {/* Nav — dynamic based on groups */}
      <nav className="sidebar-nav">
        {Object.entries(sections).map(([section, items]) => (
          <React.Fragment key={section}>
            <div className="nav-section-label">{section}</div>
            {items.map(page => (
              <NavLink
                key={page.key}
                to={page.route}
                onClick={onNavigate}
                className={({ isActive }) =>
                  'nav-item' + (isActive ? ' active' : '')
                }
              >
                <span className="nav-icon"><Icon name={page.nav.icon} size={18} /></span>
                {page.nav.label}
                {page.nav.tag && <span className="nav-tag">{page.nav.tag}</span>}
              </NavLink>
            ))}
          </React.Fragment>
        ))}

        {/* No groups assigned */}
        {userPages.length === 0 && (
          <div style={{ padding: '8px 10px', color: 'rgba(255,255,255,.4)', fontSize: '13px' }}>
            No access assigned
          </div>
        )}
      </nav>

      {/* User footer */}
      <div className="sidebar-footer">
        <div className="user-row">
          <div className="user-avatar">{initials}</div>
          <div className="user-info">
            <div className="user-name-sidebar" title={email}>{name || email}</div>
            <div className="user-role">{roleLabel}</div>
          </div>
          <button
            className="signout-btn"
            title="Sign out"
            aria-label="Sign out"
            onClick={handleSignOut}
          >
            <Icon name="logout" size={18} />
          </button>
        </div>
      </div>

    </aside>
  );
}
