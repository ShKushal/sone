import React, { useState, useEffect, useRef, useCallback } from 'react';
import { getApiHeaders }               from '../../hooks/useAuth';
import { fetchAuthSession }            from 'aws-amplify/auth';

const API_URL  = process.env.REACT_APP_API_URL;
const PER_PAGE = 10;

// TEMPORARY copy of the limit set in the email logger Lambda's environment variables
// (it sets custom:emailBlocked once custom:emailFailCount reaches it). Display only —
// blocking itself is enforced by that Lambda. Keep the two in step until this moves
// to global config.
const EMAIL_FAIL_LIMIT = 5;

// A bounce is not known when the reset call returns: SES -> SNS -> logger Lambda ->
// email history + Cognito attributes takes a few seconds. After a reset the card keeps
// re-checking for a while instead of showing a stale state until someone refreshes.
const DELIVERY_POLL_MS   = 3000;    // how often to re-check
const DELIVERY_WATCH_MS  = 90000;   // stop waiting for a result after this long
const DELIVERY_SETTLE_MS = 8000;    // after a failure, a few more checks so the logger's counter update shows
const CLOCK_SKEW_MS      = 5000;    // browser clock vs SES timestamps
const REFRESH_MIN_MS     = 600;     // a manual refresh shows its loader at least this long, even when instant
const DELIVERY_TERMINAL  = ['Delivery', 'Bounce', 'Complaint', 'Reject', 'RenderingFailure'];

const DELIVERY_TEXT = {
  waiting:   '⏳ Email sent — checking delivery status…',
  delivered: '✅ Email delivered',
  failed:    '❌ Email could not be delivered (bounced) — see Email delivery above',
  timeout:   'No delivery result yet — it can take a little longer. Use ↻ Refresh to check again.',
};

// Email delivery state from the user's custom attributes. Cognito omits
// attributes that were never set, so "missing" means not blocked, 0 failures.
function emailStatus(attrs = {}) {
  const blocked   = String(attrs['custom:emailBlocked'] || '').toLowerCase() === 'true';
  const failCount = parseInt(attrs['custom:emailFailCount'], 10) || 0;
  return { blocked, failCount };
}

// -------------------------------------------------------
// API helpers
// -------------------------------------------------------

// The Lambdas answer failures with { errors: [...] } (older checks use { error })
function errorMessage(data, fallback) {
  return data?.errors?.[0] || data?.error || fallback;
}

async function getTriggeredBy() {
  try {
    const session = await fetchAuthSession();
    return session.tokens?.idToken?.payload?.email || '';
  } catch { return ''; }
}

async function apiSearchUser(username) {
  const headers  = await getApiHeaders();
  const response = await fetch(
    `${API_URL}/users/${encodeURIComponent(username)}`,
    { headers }
  );
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(`Server error: ${response.status}`);
  return response.json();
}

async function apiListUsers(limit, paginationToken) {
  const headers = await getApiHeaders();
  const url     = new URL(`${API_URL}/users`);
  url.searchParams.append('limit', limit);
  if (paginationToken) url.searchParams.append('paginationToken', paginationToken);
  const response = await fetch(url.toString(), { headers });
  if (!response.ok) throw new Error(`Server error: ${response.status}`);
  return response.json();
}

async function apiResetPassword(username, triggeredBy) {
  const headers  = await getApiHeaders();
  const response = await fetch(
    `${API_URL}/users/${encodeURIComponent(username)}/reset-password`,
    { method: 'POST', headers, body: JSON.stringify({ triggeredBy }) }
  );
  const data = await response.json();
  if (!response.ok) throw new Error(errorMessage(data, 'Reset failed'));
  return data;
}

async function apiToggleUser(username, enable) {
  const headers  = await getApiHeaders();
  const response = await fetch(
    `${API_URL}/users/${encodeURIComponent(username)}`,
    {
      method:  'PUT',
      headers,
      body:    JSON.stringify({ frUnindexedString1: enable ? 'TRUE' : 'FALSE' })
    }
  );
  const data = await response.json();
  if (!response.ok) throw new Error(errorMessage(data, 'Update failed'));
  return data;
}

// Resets custom:emailFailCount to 0 and custom:emailBlocked to false
async function apiUnblockEmail(username, triggeredBy) {
  const headers  = await getApiHeaders();
  const response = await fetch(
    `${API_URL}/users/${encodeURIComponent(username)}/email-unblock`,
    { method: 'POST', headers, body: JSON.stringify({ triggeredBy }) }
  );
  const data = await response.json();
  if (!response.ok) throw new Error(errorMessage(data, 'Unblock failed'));
  return data;
}

async function apiGetEmailLogs(username) {
  try {
    const headers  = await getApiHeaders();
    const response = await fetch(
      `${API_URL}/users/${encodeURIComponent(username)}/email-logs`,
      { headers }
    );
    if (!response.ok) return null;
    const data = await response.json();
    return data.logs || [];
  } catch { return null; }   // null = failed, so a background re-check keeps what is on screen
}

// -------------------------------------------------------
// Confirmation dialog
// -------------------------------------------------------
function ConfirmDialog({ title, body, confirmLabel, danger, onConfirm, onCancel }) {
  return (
    <div className="confirm-overlay" onClick={onCancel}>
      <div className="confirm-card" onClick={e => e.stopPropagation()}>
        <div className="confirm-title">{title}</div>
        <div className="confirm-body">{body}</div>
        <div className="confirm-actions">
          <button className="btn-cancel" onClick={onCancel}>Cancel</button>
          <button
            className={`btn-confirm${danger ? ' danger' : ''}`}
            onClick={onConfirm}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

// -------------------------------------------------------
// Email logs
// -------------------------------------------------------
function EmailLogs({ username, refresh, onLoaded, onManualRefresh }) {
  const [logs,    setLogs]    = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshing,     setRefreshing]     = useState(false);   // a manual refresh is running
  const [refreshFailed,  setRefreshFailed]  = useState(false);
  const [updatedAt,      setUpdatedAt]      = useState(null);    // when the history was last read
  const firstLoad = useRef(true);   // only the first load shows "Loading..." — re-checks update quietly
  const latest    = useRef(0);      // a slow response must not overwrite a newer one
  const alive     = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; };
  }, []);

  // one read of the history; resolves false if it failed (so the caller can say so)
  const loadLogs = useCallback(async () => {
    const mine = ++latest.current;
    if (firstLoad.current) setLoading(true);
    const result = await apiGetEmailLogs(username);
    if (!alive.current || mine !== latest.current) return true;   // a newer read took over
    firstLoad.current = false;
    setLoading(false);
    if (result === null) return false;
    setLogs(result);
    setUpdatedAt(new Date());
    if (onLoaded) onLoaded(result);
    return true;
  }, [username, onLoaded]);

  // re-reads when the user changes or the parent bumps `refresh`
  useEffect(() => { loadLogs(); }, [loadLogs, refresh]);

  // The Refresh button: locked and showing a loader until BOTH the user and the history
  // are re-read, and visible for at least REFRESH_MIN_MS so an instant refresh is still noticed.
  const manualRefresh = async () => {
    if (refreshing) return;
    setRefreshing(true);
    setRefreshFailed(false);
    const started = Date.now();
    const [userOk, logsOk] = await Promise.all([
      Promise.resolve(onManualRefresh ? onManualRefresh() : true).catch(() => false),
      loadLogs(),
    ]);
    const rest = REFRESH_MIN_MS - (Date.now() - started);
    if (rest > 0) await new Promise(resolve => setTimeout(resolve, rest));
    if (!alive.current) return;
    setRefreshFailed(userOk === false || !logsOk);
    setRefreshing(false);
  };

  const eventColor = type => {
    if (['Delivery', 'Send'].includes(type)) return 'var(--green)';
    if (['Bounce', 'Complaint', 'Reject'].includes(type)) return 'var(--red)';
    return 'var(--grey-600)';
  };

  const eventIcon = type => {
    if (['Delivery', 'Send'].includes(type)) return '✅';
    if (['Bounce', 'Complaint', 'Reject'].includes(type)) return '❌';
    return '•';
  };

  return (
    <div className="reset-history">
      <div className="logs-heading">
        <h3>Email History</h3>
        {onManualRefresh && (
          <div className="logs-refresh">
            {!refreshing && refreshFailed && (
              <span className="logs-updated failed">Refresh failed — try again</span>
            )}
            {!refreshing && !refreshFailed && updatedAt && (
              <span className="logs-updated">Updated {updatedAt.toLocaleTimeString()}</span>
            )}
            <button
              className="btn-link"
              onClick={manualRefresh}
              disabled={refreshing}
              aria-busy={refreshing}
            >
              {refreshing
                ? <><span className="spinner" aria-hidden="true" />Refreshing…</>
                : '↻ Refresh'}
            </button>
          </div>
        )}
      </div>
      {loading && (
        <p style={{ fontSize: 12, color: 'var(--grey-400)', padding: '8px 0' }}>
          Loading...
        </p>
      )}
      {!loading && logs.length === 0 && (
        <p style={{ fontSize: 12, color: 'var(--grey-400)', padding: '8px 0' }}>
          No email logs found
        </p>
      )}
      {!loading && logs.length > 0 && (
        <div className={`reset-logs${refreshing ? ' refreshing' : ''}`}>
          {logs.map((log, i) => (
            <div key={i} className="reset-log">
              <span className="log-status" style={{ color: eventColor(log.eventType) }}>
                {eventIcon(log.eventType)} {log.eventType}
              </span>
              <div className="log-detail">
                <span className="log-by">{log.subject || '—'}</span>
                <span className="log-time">
                  {new Date(log.timestamp).toLocaleString()}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// -------------------------------------------------------
// User card
// -------------------------------------------------------
function UserCard({ user, onRefresh }) {
  const [loading,      setLoading]      = useState('');
  const [message,      setMessage]      = useState('');
  const [msgType,      setMsgType]      = useState('');
  const [emailRefresh, setEmailRefresh] = useState(0);
  const [confirm,      setConfirm]      = useState(null);

  // waiting for the delivery result of a password-reset email
  const [watch,      setWatch]      = useState(null);   // { since, until, result }
  const [delivery,   setDelivery]   = useState(null);   // waiting | delivered | failed | timeout
  const [latestLogs, setLatestLogs] = useState([]);
  const watchRef     = useRef(null);
  const onRefreshRef = useRef(onRefresh);
  watchRef.current     = watch;
  onRefreshRef.current = onRefresh;

  // While waiting: re-read the user (the logger's failure count / block) and the email
  // history every few seconds, until a result for this reset is in or time runs out.
  const isWatching = watch !== null;
  useEffect(() => {
    if (!isWatching) return undefined;
    const timer = setInterval(() => {
      const w = watchRef.current;
      if (!w) return;
      if (Date.now() >= w.until) {
        setWatch(null);
        setDelivery(w.result || 'timeout');
        return;
      }
      if (document.hidden) return;                  // nobody is looking — skip the request
      onRefreshRef.current();
      setEmailRefresh(r => r + 1);
    }, DELIVERY_POLL_MS);
    return () => clearInterval(timer);
  }, [isWatching]);

  // A result for THIS reset = a final delivery event newer than the click
  useEffect(() => {
    if (!watch || watch.result) return;
    const hit = latestLogs.find(log =>
      DELIVERY_TERMINAL.includes(log.eventType) &&
      new Date(log.timestamp).getTime() >= watch.since - CLOCK_SKEW_MS
    );
    if (!hit) return;
    const failed = hit.eventType !== 'Delivery';
    setDelivery(failed ? 'failed' : 'delivered');
    // delivered: stop on the next tick. failed: a few more checks so the counter update shows up.
    setWatch(w => w && {
      ...w,
      result: failed ? 'failed' : 'delivered',
      until:  failed ? Math.min(w.until, Date.now() + DELIVERY_SETTLE_MS) : Date.now(),
    });
  }, [latestLogs, watch]);

  // the outcome line fades after a while (a timeout hint stays until the next action)
  useEffect(() => {
    if (!delivery || delivery === 'waiting' || delivery === 'timeout') return undefined;
    const t = setTimeout(() => setDelivery(null), 20000);
    return () => clearTimeout(t);
  }, [delivery]);

  const startWatch = (since) => {
    setDelivery('waiting');
    setWatch({ since, until: Date.now() + DELIVERY_WATCH_MS, result: null });
  };

  const attrs     = user.attributes || {};
  const isEnabled = user.enabled;

  const { blocked, failCount } = emailStatus(attrs);
  const unblockAction = blocked ? 'Email unblock' : 'Failure count reset';

  const notice = {
    success: { className: 'action-success', icon: '✓ ' },
    warning: { className: 'action-warning', icon: '⚠ ' },
    error:   { className: 'action-error',   icon: '✗ ' },
  }[msgType];

  const showMsg = (text, type) => {
    setMessage(text);
    setMsgType(type);
    // a warning is something the helpdesk must act on, so keep it up longer
    setTimeout(() => setMessage(''), type === 'warning' ? 10000 : 3000);
  };

  // fn may return { notice: { type, message } } to replace "<action> successful",
  // and { watchDelivery: true } to keep checking for the email's delivery result
  const handle = async (action, fn) => {
    setConfirm(null);
    setLoading(action);
    setMessage('');
    const startedAt = Date.now();
    try {
      const outcome = await fn();
      showMsg(
        outcome?.notice?.message || `${action} successful`,
        outcome?.notice?.type    || 'success'
      );
      onRefresh();
      setEmailRefresh(r => r + 1);
      if (outcome?.watchDelivery) startWatch(startedAt);
    } catch (e) {
      showMsg(e.message, 'error');
    } finally {
      setLoading('');
    }
  };

  const confirmReset = () => setConfirm({
    title:        'Reset password?',
    body:         `A new password will be generated and emailed directly to ${user.userName}. They can use it to log in immediately.`,
    confirmLabel: 'Reset password',
    danger:       false,
    action:       'Password reset',
    fn:           async () => {
      const by     = await getTriggeredBy();
      const result = await apiResetPassword(user.userName, by);

      // the password is changed either way — say so if the email did not go out
      if (result.emailBlocked) {
        return { notice: { type: 'warning', message:
          "Password was reset, but the email was NOT sent — this user's email is blocked after repeated failed deliveries. Fix the address, unblock email, then reset again." } };
      }
      if (result.emailSent === false) {
        return { notice: { type: 'warning', message:
          'Password was reset, but the email could not be sent. Check the email history below.' } };
      }

      // handed to SES — a bounce (if any) shows up a few seconds later, so keep checking
      return { watchDelivery: true };
    }
  });

  const confirmDisable = () => setConfirm({
    title:        'Disable user?',
    body:         `${user.userName} will no longer be able to sign in.`,
    confirmLabel: 'Disable user',
    danger:       true,
    action:       'Disable',
    fn:           () => apiToggleUser(user.userName, false)
  });

  const confirmEnable = () => setConfirm({
    title:        'Enable user?',
    body:         `${user.userName} will be able to sign in again.`,
    confirmLabel: 'Enable user',
    danger:       false,
    action:       'Enable',
    fn:           () => apiToggleUser(user.userName, true)
  });

  const confirmUnblock = () => setConfirm({
    title:        blocked ? 'Unblock email?' : 'Reset failure count?',
    body:         blocked
      ? `Email sending to ${user.userName} will resume and the failure count goes back to 0. Only do this once the email address has been corrected — if it still bounces, the failures will build up and the user will be blocked again.`
      : `The failed-delivery count for ${user.userName} will be reset to 0.`,
    confirmLabel: blocked ? 'Unblock email' : 'Reset count',
    danger:       false,
    action:       unblockAction,
    fn:           async () => {
      const by = await getTriggeredBy();
      await apiUnblockEmail(user.userName, by);
    }
  });

  return (
    <>
      {confirm && (
        <ConfirmDialog
          title={confirm.title}
          body={confirm.body}
          confirmLabel={confirm.confirmLabel}
          danger={confirm.danger}
          onConfirm={() => handle(confirm.action, confirm.fn)}
          onCancel={() => setConfirm(null)}
        />
      )}

      <div className="user-card">
        <div className="user-card-header">
          <div>
            <div className="user-name">
              {attrs.given_name || ''} {attrs.family_name || ''}
              {!attrs.given_name && !attrs.family_name && user.userName}
            </div>
            <div className="user-email-text">{user.userName}</div>
          </div>
          <div className="user-badges">
            <span className={`badge ${isEnabled ? 'badge-green' : 'badge-red'}`}>
              {isEnabled ? 'Enabled' : 'Disabled'}
            </span>
            <span className={`badge ${
              user.status === 'CONFIRMED' ? 'badge-blue' : 'badge-amber'
            }`}>
              {user.status?.replace(/_/g, ' ')}
            </span>
            {attrs['custom:migrationType'] === 'JIT' && (
              <span className="badge badge-green">JIT Migrated</span>
            )}
            {blocked && (
              <span className="badge badge-red">Email blocked</span>
            )}
            {!blocked && failCount > 0 && (
              <span className="badge badge-amber">
                {failCount}/{EMAIL_FAIL_LIMIT} email failures
              </span>
            )}
          </div>
        </div>

        <div className="user-details">
          <div className="detail-row">
            <span className="detail-label">Organisation</span>
            <span className="detail-value">{attrs['custom:organisation'] || '—'}</span>
          </div>
          <div className="detail-row">
            <span className="detail-label">Last Login</span>
            <span className="detail-value">
              {attrs['custom:lastLogin']
                ? new Date(attrs['custom:lastLogin']).toLocaleString()
                : 'Never'
              }
            </span>
          </div>
          <div className="detail-row">
            <span className="detail-label">Migration</span>
            <span className="detail-value">
              {attrs['custom:migrationType'] || 'Pending'}
            </span>
          </div>
          <div className="detail-row">
            <span className="detail-label">Created</span>
            <span className="detail-value">
              {new Date(user.createdAt).toLocaleDateString()}
            </span>
          </div>
          <div className="detail-row">
            <span className="detail-label">Email delivery</span>
            <span
              className="detail-value"
              style={{ color: blocked ? 'var(--red)' : failCount > 0 ? 'var(--amber)' : undefined }}
            >
              {blocked
                ? `Blocked (${failCount}/${EMAIL_FAIL_LIMIT} failures)`
                : failCount > 0
                  ? `${failCount}/${EMAIL_FAIL_LIMIT} failed deliveries`
                  : 'Active'}
            </span>
          </div>
        </div>

        {(blocked || failCount > 0) && (
          <div className={`email-alert ${blocked ? 'blocked' : 'warn'}`}>
            <div>
              <div className="email-alert-title">
                {blocked
                  ? 'Email sending is blocked for this user'
                  : 'Some emails to this user have failed to deliver'}
              </div>
              <div className="email-alert-body">
                {blocked
                  ? `${failCount} of ${EMAIL_FAIL_LIMIT} deliveries failed, so no emails (including password resets) are sent to this address. Once the email address has been corrected, use the Unblock email button below to resume sending.`
                  : `${failCount} of ${EMAIL_FAIL_LIMIT} failed deliveries before sending is blocked. If the address has been corrected, you can reset the count.`}
              </div>
            </div>
          </div>
        )}

        {message && notice && (
          <div className={notice.className}>
            {notice.icon}{message}
          </div>
        )}

        <div className="user-actions">
          {blocked && (
            <button
              className="btn-action btn-unblock primary"
              disabled={!!loading}
              onClick={confirmUnblock}
            >
              {loading === unblockAction ? 'Working...' : 'Unblock email'}
            </button>
          )}

          <button
            className="btn-action btn-reset"
            disabled={!!loading || blocked}
            title={blocked ? 'Email is blocked — unblock it first, otherwise the new password cannot be emailed' : undefined}
            onClick={confirmReset}
          >
            {loading === 'Password reset' ? 'Sending...' : 'Send Password Reset'}
          </button>

          {!blocked && failCount > 0 && (
            <button
              className="btn-action btn-unblock"
              disabled={!!loading}
              onClick={confirmUnblock}
            >
              {loading === unblockAction ? 'Working...' : 'Reset failure count'}
            </button>
          )}

          {isEnabled ? (
            <button
              className="btn-action btn-disable"
              disabled={!!loading}
              onClick={confirmDisable}
            >
              {loading === 'Disable' ? 'Disabling...' : 'Disable User'}
            </button>
          ) : (
            <button
              className="btn-action btn-enable"
              disabled={!!loading}
              onClick={confirmEnable}
            >
              {loading === 'Enable' ? 'Enabling...' : 'Enable User'}
            </button>
          )}
        </div>

        {blocked && (
          <div className="actions-hint">
            Password reset is unavailable while email is blocked — unblock email first.
          </div>
        )}

        {delivery && (
          <div className={`delivery-status ${delivery}`} role="status">
            {DELIVERY_TEXT[delivery]}
          </div>
        )}

        <EmailLogs
          username={user.userName}
          refresh={emailRefresh}
          onLoaded={setLatestLogs}
          onManualRefresh={onRefresh}
        />
      </div>
    </>
  );
}

// -------------------------------------------------------
// Paginated user list
// -------------------------------------------------------
function UserList({ onSelect, selectedUser }) {
  const [users,       setUsers]       = useState([]);
  const [tokens,      setTokens]      = useState([null]); // index = page, value = token
  const [currentPage, setCurrentPage] = useState(0);
  const [hasMore,     setHasMore]     = useState(false);
  const [loading,     setLoading]     = useState(false);
  const [error,       setError]       = useState('');

  const fetchPage = async (pageIndex) => {
    setLoading(true);
    setError('');
    try {
      const token  = tokens[pageIndex] || null;
      const result = await apiListUsers(PER_PAGE, token);

      setUsers(result.users || []);
      setHasMore(result.hasMore);
      setCurrentPage(pageIndex);

      // store next token
      if (result.nextToken) {
        setTokens(prev => {
          const updated = [...prev];
          updated[pageIndex + 1] = result.nextToken;
          return updated;
        });
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchPage(0); }, []);

  const hasPrev = currentPage > 0;

  return (
    <div className="user-list-panel">
      <div className="user-list-header">
        <span className="user-list-title">All Users</span>
        <span style={{ fontSize: 12, color: 'var(--grey-400)' }}>
          Page {currentPage + 1}
        </span>
      </div>

      {error && (
        <div className="search-error" style={{ margin: '8px 0' }}>{error}</div>
      )}

      {loading && (
        <div className="loading" style={{ padding: '24px' }}>Loading...</div>
      )}

      {!loading && users.map(listed => {
        // the selected row uses the freshest copy (after unblock / enable / disable)
        const isActive = selectedUser?.userName === listed.userName;
        const user     = isActive ? selectedUser : listed;
        const attrs    = user.attributes || {};
        const name     = `${attrs.given_name || ''} ${attrs.family_name || ''}`.trim();
        const { blocked } = emailStatus(attrs);

        return (
          <div
            key={user.userName}
            className={`user-list-row${isActive ? ' active' : ''}`}
            onClick={() => onSelect(user.userName)}
          >
            <div className="user-list-info">
              <div className="user-list-name">{name || user.userName}</div>
              <div className="user-list-email">{user.userName}</div>
            </div>
            <div style={{ flexShrink: 0, display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 4 }}>
              <span className={`badge ${user.enabled ? 'badge-green' : 'badge-red'}`}
                style={{ fontSize: 10 }}>
                {user.enabled ? 'Enabled' : 'Disabled'}
              </span>
              {blocked && (
                <span className="badge badge-red" style={{ fontSize: 10 }}>
                  Email blocked
                </span>
              )}
            </div>
          </div>
        );
      })}

      {!loading && users.length === 0 && !error && (
        <div style={{ padding: '24px', textAlign: 'center', color: 'var(--grey-400)', fontSize: 13 }}>
          No users found
        </div>
      )}

      {/* Pagination */}
      <div className="user-list-pagination">
        <button
          className="btn-page"
          disabled={!hasPrev || loading}
          onClick={() => fetchPage(currentPage - 1)}
        >
          ← Prev
        </button>
        <span style={{ fontSize: 12, color: 'var(--grey-400)' }}>
          Page {currentPage + 1}
        </span>
        <button
          className="btn-page"
          disabled={!hasMore || loading}
          onClick={() => fetchPage(currentPage + 1)}
        >
          Next →
        </button>
      </div>
    </div>
  );
}

// -------------------------------------------------------
// Main Helpdesk Page
// -------------------------------------------------------
export default function HelpdeskPage() {
  const [searchEmail,  setSearchEmail]  = useState('');
  const [selectedUser, setSelectedUser] = useState(null);
  const [loadingUser,  setLoadingUser]  = useState(false);
  const [error,        setError]        = useState('');

  const latestRequest = useRef('');   // newest requested username — older responses are ignored

  // silent = refresh the selected user in place. Without it the card unmounts
  // during the reload and its action message disappears as soon as it appears.
  const loadUser = async (username, { silent = false } = {}) => {
    latestRequest.current = username;
    if (!silent) { setLoadingUser(true); setError(''); }
    try {
      const result = await apiSearchUser(username);
      if (latestRequest.current !== username) return true;   // switched to another user meanwhile
      setSelectedUser(result);
      return true;
    } catch (e) {
      if (latestRequest.current !== username) return true;
      // a failed background re-check keeps what is on screen; a failed load shows the error
      if (silent) console.warn('Background refresh failed:', e.message);
      else        setError(e.message);
      return false;
    } finally {
      if (!silent && latestRequest.current === username) setLoadingUser(false);
    }
  };

  const handleSearch = async (e) => {
    e.preventDefault();
    if (!searchEmail.trim()) return;
    await loadUser(searchEmail.trim());
  };

  const handleSelectFromList = async (username) => {
    await loadUser(username);
  };

  // resolves true/false so the Refresh button can say whether the re-read worked
  const handleRefresh = async () => {
    if (!selectedUser) return true;
    return loadUser(selectedUser.userName, { silent: true });
  };

  return (
    <div className="page">

      {/* Search */}
      <div className="search-panel">
        <form onSubmit={handleSearch} className="search-form">
          <input
            type="email"
            value={searchEmail}
            onChange={e => setSearchEmail(e.target.value)}
            placeholder="Search by email address"
            className="search-input"
          />
          <button type="submit" className="btn-search" disabled={loadingUser}>
            {loadingUser ? 'Loading...' : 'Search'}
          </button>
        </form>
      </div>

      {error && <div className="search-error">{error}</div>}

      {/* Two column layout */}
      <div className="helpdesk-layout">

        {/* Left — user list */}
        <UserList
          onSelect={handleSelectFromList}
          selectedUser={selectedUser}
        />

        {/* Right — user detail */}
        <div className="helpdesk-detail">
          {loadingUser && (
            <div className="loading">Loading user...</div>
          )}

          {!loadingUser && !selectedUser && (
            <div className="helpdesk-empty">
              <div style={{ fontSize: 32, marginBottom: 12 }}>👤</div>
              <div style={{ fontSize: 14, color: 'var(--grey-400)' }}>
                Select a user from the list or search by email
              </div>
            </div>
          )}

          {!loadingUser && selectedUser && (
            <UserCard user={selectedUser} onRefresh={handleRefresh} />
          )}
        </div>

      </div>
    </div>
  );
}
