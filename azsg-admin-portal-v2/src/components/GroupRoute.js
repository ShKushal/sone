import React, { useEffect, useState } from 'react';
import { Navigate }                    from 'react-router-dom';
import { getUserGroups }               from '../hooks/useAuth';
import GROUPS, { hasGroupAccess }      from '../config/groups';

/**
 * Protects a route by portal group.
 * Only allows access if the group exists in the GROUPS config
 * AND the user holds that group (or is a SUPER_ADMIN).
 */
export default function GroupRoute({ group, children }) {
  const [allowed,  setAllowed]  = useState(null);
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    if (!GROUPS[group]) {
      setAllowed(false);
      setChecking(false);
      return;
    }

    getUserGroups().then(groups => {
      setAllowed(hasGroupAccess(groups, group));
      setChecking(false);
    });
  }, [group]);

  if (checking) return <div className="loading">Checking access...</div>;
  if (!allowed)  return <Navigate to="/access-denied" replace />;
  return children;
}
