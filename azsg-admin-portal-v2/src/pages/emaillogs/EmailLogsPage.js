import React, { useState, useEffect, useRef, useCallback } from 'react';
import { getApiHeaders }                                    from '../../hooks/useAuth';

const API_URL = process.env.REACT_APP_API_URL;

const PAGE_SIZE          = 25;     // rows per page (the API returns up to a few hundred at once)
const SEARCH_DEBOUNCE_MS = 400;    // wait for typing to pause before asking the API
const REFRESH_MIN_MS     = 600;    // a manual refresh shows its loader at least this long
const EMAIL_RE           = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

// SES event types, in the order they appear as filters.
// The table keeps ONE row per email — a later event replaces the earlier one — so "Send" only
// shows while no delivery result has arrived yet, which is why it is labelled Pending.
const STATUSES = [
  { key: 'Delivery',      label: 'Delivered', tone: 'green' },
  { key: 'Bounce',        label: 'Bounced',   tone: 'red'   },
  { key: 'Complaint',     label: 'Complaint', tone: 'red'   },
  { key: 'Reject',        label: 'Rejected',  tone: 'red'   },
  { key: 'DeliveryDelay', label: 'Delayed',   tone: 'amber' },
  { key: 'Send',          label: 'Pending',   tone: 'blue', hint: 'Handed to the mail service — no delivery result yet' },
];
const STATUS_BY_KEY = Object.fromEntries(STATUSES.map(s => [s.key, s]));
const statusInfo    = key => STATUS_BY_KEY[key] || { key, label: key || 'Unknown', tone: 'grey' };

const HOUR = 3600 * 1000;
const RANGES = [
  { key: '24h',    label: 'Last 24 hours', ms: 24 * HOUR      },
  { key: '7d',     label: 'Last 7 days',   ms: 7 * 24 * HOUR  },
  { key: '30d',    label: 'Last 30 days',  ms: 30 * 24 * HOUR },
  { key: 'custom', label: 'Custom range'                      },
];
const DEFAULT_RANGE = '7d';

// -------------------------------------------------------
// API
// -------------------------------------------------------
function buildParams({ statuses, search, range, from, to }) {
  const params = new URLSearchParams();
  if (statuses.length) params.set('status', statuses.join(','));

  const text = search.trim();
  if (text) params.set(EMAIL_RE.test(text) ? 'email' : 'search', text);   // a full address is an indexed lookup

  if (range === 'custom') {
    if (from) params.set('from', from);
    if (to)   params.set('to', to);
  } else {
    const preset = RANGES.find(r => r.key === range);
    params.set('from', new Date(Date.now() - preset.ms).toISOString());
  }
  return params;
}

async function fetchEmailLogs(params) {
  const headers  = await getApiHeaders();
  const response = await fetch(`${API_URL}/email-logs?${params}`, { headers });
  let data = null;
  try { data = await response.json(); } catch { /* reported below */ }

  if (!response.ok) {
    if (response.status === 403) throw new Error('You do not have access to the email logs.');
    throw new Error(data?.errors?.[0] || data?.error || `Server error: ${response.status}`);
  }
  return data || {};
}

// -------------------------------------------------------
// Helpers
// -------------------------------------------------------
// The API sends `details` as an object; accept the raw JSON string too
function detailsOf(item) {
  const raw = item.details;
  if (raw && typeof raw === 'object') return raw;
  if (typeof raw === 'string') {
    try {
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed === 'object') return parsed;
    } catch { /* not JSON */ }
  }
  return {};
}

// when the event happened (delivery / bounce time), falling back to when the mail was sent
const eventTime = item => detailsOf(item).timestamp || item.timestamp || '';

function formatTime(value) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
}

const rowKey = item => `${item.email}|${item.timestamp}|${item.eventType}|${item.messageId || ''}`;

// Everything we know about one event, as label/value rows
function detailRows(item) {
  const d    = detailsOf(item);
  const rows = [];
  const add  = (label, value, mono) => {
    if (value !== undefined && value !== null && value !== '') rows.push({ label, value: String(value), mono: !!mono });
  };

  add('Status',    statusInfo(item.eventType).label);
  add('Recipient', item.email, true);
  add('Subject',   item.subject);
  if (d.timestamp) add('Event time', `${formatTime(d.timestamp)}  (${d.timestamp})`);
  if (item.timestamp) add('Sent at', `${formatTime(item.timestamp)}  (${item.timestamp})`);
  add('Message ID', item.messageId, true);

  // delivery
  add('SMTP response',       d.smtpResponse, true);
  add('Receiving server IP', d.remoteMtaIp, true);
  add('Sending MTA',         d.reportingMTA, true);
  if (d.processingTimeMillis !== undefined && d.processingTimeMillis !== null) {
    add('Processing time', `${d.processingTimeMillis} ms`);
  }
  if (Array.isArray(d.recipients) && (d.recipients.length > 1 || d.recipients[0] !== item.email)) {
    add('Recipients', d.recipients.join(', '), true);
  }

  // bounce
  if (d.bounceType) add('Bounce type', `${d.bounceType}${d.bounceSubType ? ' / ' + d.bounceSubType : ''}`);
  (d.bouncedRecipients || []).forEach(r => {
    add('Bounced recipient', r.emailAddress, true);
    add('Action',            r.action);
    add('Status code',       r.status, true);
    add('Diagnostic',        r.diagnosticCode, true);
  });
  add('Feedback ID', d.feedbackId, true);

  // complaint
  add('Complaint type', d.complaintFeedbackType);
  add('User agent',     d.userAgent);
  add('Arrival date',   d.arrivalDate);
  (d.complainedRecipients || []).forEach(r => add('Complained recipient', r.emailAddress, true));

  // delay
  add('Delay type', d.delayType);
  add('Retrying until', d.expirationTime);
  (d.delayedRecipients || []).forEach(r => {
    add('Delayed recipient', r.emailAddress, true);
    add('Status code',       r.status, true);
    add('Diagnostic',        r.diagnosticCode, true);
  });

  // reject / anything the logger stored that we could not parse
  add('Reason', d.reason);
  if (typeof item.details === 'string' && !Object.keys(d).length) add('Raw details', item.details, true);

  return rows;
}

// -------------------------------------------------------
// One event's full details
// -------------------------------------------------------
function EventDetails({ item }) {
  const [showRaw, setShowRaw] = useState(false);
  const rows = detailRows(item);

  return (
    <div className="el-detail">
      <dl className="el-detail-grid">
        {rows.map((row, i) => (
          <React.Fragment key={`${row.label}-${i}`}>
            <dt>{row.label}</dt>
            <dd className={row.mono ? 'el-mono' : undefined}>{row.value}</dd>
          </React.Fragment>
        ))}
      </dl>
      <button className="btn-link" onClick={() => setShowRaw(v => !v)}>
        {showRaw ? 'Hide raw event' : 'View raw event'}
      </button>
      {showRaw && <pre className="el-raw">{JSON.stringify(item, null, 2)}</pre>}
    </div>
  );
}

// -------------------------------------------------------
// Page
// -------------------------------------------------------
export default function EmailLogsPage() {
  const [statuses,   setStatuses]   = useState([]);          // selected event types; empty = all
  const [searchText, setSearchText] = useState('');          // what is typed
  const [search,     setSearch]     = useState('');          // what is sent (after the typing pause)
  const [range,      setRange]      = useState(DEFAULT_RANGE);
  const [from,       setFrom]       = useState('');
  const [to,         setTo]         = useState('');

  const [data,       setData]       = useState(null);
  const [loading,    setLoading]    = useState(true);
  const [error,      setError]      = useState('');
  const [page,       setPage]       = useState(0);
  const [expanded,   setExpanded]   = useState(() => new Set());
  const [refreshing, setRefreshing] = useState(false);
  const [updatedAt,  setUpdatedAt]  = useState(null);

  const latest = useRef(0);        // only the newest request may update the screen
  const alive  = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; };
  }, []);

  // wait for typing to pause before asking the API
  useEffect(() => {
    const timer = setTimeout(() => setSearch(searchText), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [searchText]);

  const statusKey = statuses.join(',');

  const load = useCallback(async (manual = false) => {
    const mine    = ++latest.current;
    const started = Date.now();
    setLoading(true);
    setError('');
    if (manual) setRefreshing(true);
    try {
      const result = await fetchEmailLogs(buildParams({
        statuses: statusKey ? statusKey.split(',') : [], search, range, from, to,
      }));
      if (!alive.current || mine !== latest.current) return;
      setData(result);
      setPage(0);
      setExpanded(new Set());
      setUpdatedAt(new Date());
    } catch (e) {
      if (alive.current && mine === latest.current) setError(e.message);
    } finally {
      if (alive.current && mine === latest.current) setLoading(false);
      if (manual) {
        // show the loader long enough to be noticed, even when the answer is instant
        const rest = REFRESH_MIN_MS - (Date.now() - started);
        if (rest > 0) await new Promise(resolve => setTimeout(resolve, rest));
        if (alive.current) setRefreshing(false);
      }
    }
  }, [statusKey, search, range, from, to]);

  // load on the first visit and whenever a filter changes
  useEffect(() => { load(); }, [load]);

  const items    = data?.items || [];
  const summary  = data?.summary || {};
  const total    = data?.total ?? items.length;
  const start    = page * PAGE_SIZE;
  const pageRows = items.slice(start, start + PAGE_SIZE);
  const pages    = Math.ceil(items.length / PAGE_SIZE);

  const countOf   = key => summary[key] || 0;
  const windowAll = Object.values(summary).reduce((sum, n) => sum + n, 0);
  const extraKeys = Object.keys(summary).filter(key => !STATUS_BY_KEY[key]);   // e.g. Open, Click

  const toggleStatus = key =>
    setStatuses(current => current.includes(key) ? current.filter(k => k !== key) : [...current, key]);
  const onlyStatus   = key =>
    setStatuses(current => (current.length === 1 && current[0] === key) ? [] : [key]);

  const toggleRow = key =>
    setExpanded(current => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });

  const activeFilters = statuses.length + (searchText.trim() ? 1 : 0) + (range !== DEFAULT_RANGE ? 1 : 0);
  const clearAll = () => {
    setStatuses([]); setSearchText(''); setSearch('');
    setRange(DEFAULT_RANGE); setFrom(''); setTo('');
  };

  const tile = (label, key, tone) => {
    const active = key ? (statuses.length === 1 && statuses[0] === key) : statuses.length === 0;
    return (
      <button
        type="button"
        className={`stat-card clickable${active ? ' active' : ''}`}
        onClick={() => (key ? onlyStatus(key) : setStatuses([]))}
      >
        <div className="stat-label">{label}</div>
        <div className="stat-value">{(key ? countOf(key) : windowAll).toLocaleString()}</div>
        {key && <span className={`stat-chip chip-${tone}`}>{statusInfo(key).label}</span>}
      </button>
    );
  };

  return (
    <div className="page email-logs-page">

      {/* Filters */}
      <div className="filter-panel">
        <div className="filter-header">
          <div className="filter-title">
            Filters
            {activeFilters > 0 && <span className="filter-badge">{activeFilters}</span>}
          </div>
          <button className="btn-clear" onClick={clearAll}>Clear all</button>
        </div>

        <div className="el-toolbar">
          <input
            type="text"
            className="search-input el-search"
            value={searchText}
            onChange={e => setSearchText(e.target.value)}
            placeholder="Search recipient email"
            aria-label="Search recipient email"
          />
          <div className="el-ranges">
            {RANGES.map(r => (
              <button
                key={r.key}
                className={`btn-preset${range === r.key ? ' active' : ''}`}
                onClick={() => setRange(r.key)}
              >
                {r.label}
              </button>
            ))}
          </div>
        </div>

        {range === 'custom' && (
          <div className="el-custom">
            <label>From <input type="date" value={from} onChange={e => setFrom(e.target.value)} aria-label="From date" /></label>
            <label>To   <input type="date" value={to}   onChange={e => setTo(e.target.value)}   aria-label="To date" /></label>
          </div>
        )}

        <div className="el-chips">
          <button className={`el-chip${statuses.length === 0 ? ' active' : ''}`} onClick={() => setStatuses([])}>
            All <span className="count">{windowAll.toLocaleString()}</span>
          </button>
          {STATUSES.map(s => (
            <button
              key={s.key}
              className={`el-chip tone-${s.tone}${statuses.includes(s.key) ? ' active' : ''}`}
              onClick={() => toggleStatus(s.key)}
              title={s.hint}
            >
              {s.label} <span className="count">{countOf(s.key).toLocaleString()}</span>
            </button>
          ))}
          {extraKeys.map(key => (
            <button
              key={key}
              className={`el-chip tone-grey${statuses.includes(key) ? ' active' : ''}`}
              onClick={() => toggleStatus(key)}
            >
              {statusInfo(key).label} <span className="count">{countOf(key).toLocaleString()}</span>
            </button>
          ))}
        </div>
      </div>

      {/* Counts for this window — click one to filter */}
      <div className="stats-row">
        {tile('All events', null, 'blue')}
        {tile('Delivered', 'Delivery', 'green')}
        {tile('Bounced', 'Bounce', 'amber')}
        {tile('Complaints', 'Complaint', 'amber')}
      </div>

      {/* Activity */}
      <div className="el-panel">
        <div className="el-panel-header">
          <div>
            <span className="el-panel-title">Email activity</span>
            <span className="el-panel-sub">
              {loading && !data ? 'Loading…' : `${total.toLocaleString()} event${total === 1 ? '' : 's'}`}
            </span>
          </div>
          <div className="logs-refresh">
            {!refreshing && updatedAt && (
              <span className="logs-updated">Updated {updatedAt.toLocaleTimeString()}</span>
            )}
            <button className="btn-link" onClick={() => load(true)} disabled={refreshing} aria-busy={refreshing}>
              {refreshing ? <><span className="spinner" aria-hidden="true" />Refreshing…</> : '↻ Refresh'}
            </button>
          </div>
        </div>

        {error && <div className="el-notice error" role="alert">{error}</div>}
        {data?.truncated && (
          <div className="el-notice warn">
            Showing the newest {items.length.toLocaleString()} of {total.toLocaleString()} matching events — narrow the date range or filters to see the rest.
          </div>
        )}
        {data?.incomplete && (
          <div className="el-notice warn">
            The log table is large, so this view may not include every event. Narrow the date range to be sure.
          </div>
        )}

        {loading && !data && <div className="loading">Loading email activity…</div>}

        {data && items.length === 0 && !loading && (
          <div className="el-empty">No emails match these filters.</div>
        )}

        {items.length > 0 && (
          <div className={`el-table-wrap${loading ? ' loading' : ''}`}>
            <table className="el-table">
              <thead>
                <tr>
                  <th>Status</th>
                  <th>Recipient</th>
                  <th>Subject</th>
                  <th>Time</th>
                  <th title="IP of the mail server that received the message">Receiving IP</th>
                  <th aria-label="Details" />
                </tr>
              </thead>
              <tbody>
                {pageRows.map(item => {
                  const key    = rowKey(item);
                  const open   = expanded.has(key);
                  const info   = statusInfo(item.eventType);
                  const detail = detailsOf(item);
                  return (
                    <React.Fragment key={key}>
                      <tr
                        className={`el-row${open ? ' open' : ''}`}
                        onClick={() => toggleRow(key)}
                        onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggleRow(key); } }}
                        tabIndex={0}
                        aria-expanded={open}
                      >
                        <td><span className={`badge badge-${info.tone}`} title={info.hint}>{info.label}</span></td>
                        <td className="el-mono">{item.email}</td>
                        <td className="el-subject">{item.subject || '—'}</td>
                        <td title={eventTime(item)}>{formatTime(eventTime(item))}</td>
                        <td className="el-mono">{detail.remoteMtaIp || '—'}</td>
                        <td className="el-chevron">{open ? '▲' : '▼'}</td>
                      </tr>
                      {open && (
                        <tr className="el-detail-row">
                          <td colSpan={6}><EventDetails item={item} /></td>
                        </tr>
                      )}
                    </React.Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {pages > 1 && (
          <div className="el-pager">
            <span className="el-pager-info">
              Showing {start + 1}–{Math.min(start + PAGE_SIZE, items.length)} of {items.length.toLocaleString()}
            </span>
            <div className="el-pager-buttons">
              <button className="btn-page" disabled={page === 0} onClick={() => setPage(p => p - 1)}>← Prev</button>
              <button className="btn-page" disabled={page >= pages - 1} onClick={() => setPage(p => p + 1)}>Next →</button>
            </div>
          </div>
        )}
      </div>

    </div>
  );
}
