import React, { useEffect, useState } from 'react';
import { Navigate }                    from 'react-router-dom';
import { getUserGroups }               from '../hooks/useAuth';
import GROUPS                          from '../config/groups';

/**
 * Protects a route by Cognito group.
 * Only allows access if group exists in GROUPS config
 * AND user is a member of that group.
 */
export default function GroupRoute({ group, children }) {
  const [allowed,  setAllowed]  = useState(null);
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    // check group exists in config
    const groupExists = Object.values(GROUPS).some(g => g.name === group);

    if (!groupExists) {
      setAllowed(false);
      setChecking(false);
      return;
    }

    getUserGroups().then(groups => {
      setAllowed(groups.includes(group));
      setChecking(false);
    });
  }, [group]);

  if (checking) return <div className="loading">Checking access...</div>;
  if (!allowed)  return <Navigate to="/access-denied" replace />;
  return children;
}
