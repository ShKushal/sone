import React, { useEffect, useState } from 'react';
import { NavLink }                    from 'react-router-dom';
import { signOut }                    from 'aws-amplify/auth';
import { getUserGroups }              from '../hooks/useAuth';
import { accessibleGroups, isSuperAdmin } from '../config/groups';

export default function NavBar({ user }) {
  const [groups, setGroups] = useState([]);

  useEffect(() => {
    getUserGroups().then(setGroups);
  }, []);

  const handleSignOut = async () => {
    try { await signOut(); }
    catch (e) { console.error(e); }
  };

  const email    = user?.signInDetails?.loginId || user?.username || '';
  const initials = email.slice(0, 2).toUpperCase();

  // the sections this user may open (a SUPER_ADMIN gets all of them)
  const userGroups = accessibleGroups(groups);

  // group nav items by section
  const sections = userGroups.reduce((acc, g) => {
    const section = g.nav.section;
    if (!acc[section]) acc[section] = [];
    acc[section].push(g);
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
      </div>

      {/* Nav — dynamic based on groups */}
      <nav className="sidebar-nav">
        {Object.entries(sections).map(([section, items]) => (
          <React.Fragment key={section}>
            <div className="nav-section-label">{section}</div>
            {items.map(group => (
              <NavLink
                key={group.name}
                to={group.route}
                className={({ isActive }) =>
                  'nav-item' + (isActive ? ' active' : '')
                }
              >
                <span className="nav-icon">{group.nav.icon}</span>
                {group.nav.label}
              </NavLink>
            ))}
          </React.Fragment>
        ))}

        {/* No groups assigned */}
        {userGroups.length === 0 && (
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
            <div className="user-name-sidebar">{email}</div>
            <div className="user-role">{roleLabel}</div>
          </div>
          <button
            className="signout-btn"
            title="Sign out"
            onClick={handleSignOut}
          >
            ↩
          </button>
        </div>
      </div>

    </aside>
  );
}
