import React, { useState, useEffect, useRef } from 'react';
import { getApiHeaders }                       from '../../hooks/useAuth';

const API_URL = process.env.REACT_APP_API_URL;
const PAGE_SIZE = 10;

const PERMANENT_REPORTS = [
  { id: 'users',              label: 'All Users',          endpoint: '/reports/users',              type: 'users',   description: 'All users with status, organisation and migration info' },
  { id: 'inactive',           label: 'Inactive Users',     endpoint: '/reports/inactive',           type: 'users',   description: 'Users who have never logged in' },
  { id: 'organisations',      label: 'Organisations',      endpoint: '/reports/organisations',      type: 'orgs',    description: 'All broker firms with member counts from Cognito' },
  { id: 'migration',          label: 'Migration Report',   endpoint: '/reports/migration',          type: 'users',   description: 'All users with migration status' },
  { id: 'migration-pending',  label: 'Migration Pending',  endpoint: '/reports/migration/pending',  type: 'users',   description: 'Users not yet logged into Cognito' },
  { id: 'migration-complete', label: 'Migration Complete', endpoint: '/reports/migration/complete', type: 'users',   description: 'Users who completed JIT migration' },
  { id: 'migration-summary',  label: 'Migration Summary',  endpoint: '/reports/migration/summary',  type: 'summary', description: 'Migration progress by organisation with percentage' },
];

const FORGEROCK_REPORTS = [
  { id: 'forgerock-organisations', label: 'FR Organisations', endpoint: '/reports/forgerock/organisations', type: 'orgs',  description: 'ForgeRock orgs vs DynamoDB gap' },
  { id: 'forgerock-gap',           label: 'FR Gap Report',    endpoint: '/reports/forgerock/gap',           type: 'users', description: 'Users in ForgeRock not in Cognito' },
];

// -------------------------------------------------------
// Date preset helpers
// -------------------------------------------------------
function getDatePresets() {
  const now   = new Date();
  const fmt   = d => d.toISOString().slice(0, 10);
  const today = fmt(now);

  const last7 = new Date(now);
  last7.setDate(last7.getDate() - 7);

  const last30 = new Date(now);
  last30.setDate(last30.getDate() - 30);

  const thisMonthStart = new Date(now.getFullYear(), now.getMonth(), 1);

  const lastMonthStart = new Date(now.getFullYear(), now.getMonth() - 1, 1);
  const lastMonthEnd   = new Date(now.getFullYear(), now.getMonth(), 0);

  const thisYearStart  = new Date(now.getFullYear(), 0, 1);

  return [
    { label: 'Today',      fromDate: today,               toDate: today },
    { label: 'Last 7 days', fromDate: fmt(last7),          toDate: today },
    { label: 'Last 30 days',fromDate: fmt(last30),         toDate: today },
    { label: 'This month',  fromDate: fmt(thisMonthStart), toDate: today },
    { label: 'Last month',  fromDate: fmt(lastMonthStart), toDate: fmt(lastMonthEnd) },
    { label: 'This year',   fromDate: fmt(thisYearStart),  toDate: today },
  ];
}

const DATE_PRESETS = getDatePresets();

const STATUS_OPTIONS  = [
  { value: 'ACTIVE',                label: 'Active' },
  { value: 'INACTIVE',              label: 'Inactive' },
  { value: 'CONFIRMED',             label: 'Confirmed' },
  { value: 'FORCE_CHANGE_PASSWORD', label: 'Force Change Password' },
];

const COUNTRY_OPTIONS = [
  { value: 'SG', label: 'Singapore' },
  { value: 'MY', label: 'Malaysia' },
  { value: 'DE', label: 'Germany' },
];

function MultiSelect({ options, selected, onChange, placeholder, loading }) {
  const [open, setOpen] = useState(false);
  const ref             = useRef(null);

  useEffect(() => {
    const handler = e => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  const toggle = value => {
    if (selected.includes(value)) onChange(selected.filter(v => v !== value));
    else                          onChange([...selected, value]);
  };

  const clear = e => { e.stopPropagation(); onChange([]); };

  return (
    <div className="multiselect" ref={ref}>
      <div className={`multiselect-control${open ? ' open' : ''}`} onClick={() => setOpen(!open)}>
        <div className="multiselect-tags">
          {selected.length === 0
            ? <span className="multiselect-placeholder">{loading ? 'Loading...' : placeholder}</span>
            : selected.map(v => {
                const opt = options.find(o => o.value === v);
                return (
                  <span key={v} className="multiselect-tag">
                    {opt ? opt.label : v}
                    <span className="multiselect-tag-x" onClick={e => { e.stopPropagation(); toggle(v); }}>×</span>
                  </span>
                );
              })
          }
        </div>
        {selected.length > 0 && (
          <span className="multiselect-tag-x" style={{fontSize:16,opacity:.5,flexShrink:0}} onClick={clear}>×</span>
        )}
        <span className={`multiselect-arrow${open ? ' open' : ''}`}>▼</span>
      </div>
      {open && (
        <div className="multiselect-dropdown">
          {options.length === 0
            ? <div className="multiselect-empty">No options</div>
            : options.map(opt => (
                <div key={opt.value} className={`multiselect-option${selected.includes(opt.value) ? ' selected' : ''}`} onClick={() => toggle(opt.value)}>
                  <div className={`multiselect-check${selected.includes(opt.value) ? ' checked' : ''}`}>{selected.includes(opt.value) ? '✓' : ''}</div>
                  <span>{opt.label}</span>
                  {opt.sub && <span className="multiselect-sub">{opt.sub}</span>}
                </div>
              ))
          }
        </div>
      )}
    </div>
  );
}

function buildParams(report, filters) {
  if (report.type === 'orgs' || report.type === 'summary') {
    return { uen: filters.organisations.join(','), country: filters.countries.join(','), search: filters.search };
  }
  return {
    organisation: filters.organisations.join(','), status: filters.statuses.join(','),
    fromDate: filters.fromDate, toDate: filters.toDate,
    lastLoginFrom: filters.lastLoginFrom, lastLoginTo: filters.lastLoginTo, search: filters.search
  };
}

async function downloadCSV(endpoint, params, filename) {
  const url = new URL(`${API_URL}${endpoint}`);
  Object.entries(params).forEach(([k, v]) => { if (v) url.searchParams.append(k, v); });
  url.searchParams.append('format', 'csv');
  const headers  = await getApiHeaders();
  const response = await fetch(url.toString(), { headers });
  if (!response.ok) throw new Error(`Server error: ${response.status}`);
  const blob  = await response.blob();
  const link  = document.createElement('a');
  link.href   = URL.createObjectURL(blob);
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(link.href);
}

async function fetchPreview(endpoint, params) {
  const url = new URL(`${API_URL}${endpoint}`);
  Object.entries(params).forEach(([k, v]) => { if (v) url.searchParams.append(k, v); });
  const headers  = await getApiHeaders();
  const response = await fetch(url.toString(), { headers });
  if (!response.ok) throw new Error(`Server error: ${response.status}`);
  return response.json();
}

function getColumns(report, rows) {
  if (!rows || rows.length === 0) return [];
  if (report.type === 'orgs') {
    return ['uen', 'name', 'displayName', 'country', 'memberCount'];
  }
  if (report.type === 'summary') {
    return ['organisation', 'total', 'migrated', 'pendingMigration', 'migratedPct'];
  }
  return ['userName', 'email', 'givenName', 'familyName', 'organisation', 'status', 'migrationType', 'lastLogin'];
}

function getRows(report, data) {
  if (!data) return [];
  if (report.type === 'orgs')    return data.organisations || data.allOrgs || [];
  if (report.type === 'summary') return data.byOrganisation || [];
  return data.users || [];
}

function PreviewTable({ report, rows, columns, page, setPage, total }) {
  const start    = page * PAGE_SIZE;
  const pageRows = rows.slice(start, start + PAGE_SIZE);
  const pages    = Math.ceil(rows.length / PAGE_SIZE);

  const formatCell = (col, val) => {
    if (val === null || val === undefined || val === '') return '—';
    if (col === 'lastLogin' && val) {
      try { return new Date(val).toLocaleDateString(); } catch { return val; }
    }
    if (col === 'migratedPct') return `${Number(val).toFixed(1)}%`;
    if (col === 'memberCount' || col === 'total' || col === 'migrated' || col === 'pendingMigration') {
      return Number(val).toLocaleString();
    }
    if (typeof val === 'boolean') return val ? 'Yes' : 'No';
    return String(val);
  };

  const statusBadge = (status) => {
    const map = {
      'CONFIRMED':             { bg: '#dcfce7', color: '#166534' },
      'FORCE_CHANGE_PASSWORD': { bg: '#fef3c7', color: '#92400e' },
      'JIT':                   { bg: '#e8edf5', color: '#1a5296' },
    };
    const s = map[status];
    if (!s) return <span>{status || '—'}</span>;
    return (
      <span style={{
        background: s.bg, color: s.color,
        padding: '2px 8px', borderRadius: 20,
        fontSize: 11, fontWeight: 600, whiteSpace: 'nowrap'
      }}>
        {status.replace(/_/g, ' ')}
      </span>
    );
  };

  const colLabel = col => ({
    userName: 'Username', email: 'Email', givenName: 'First name',
    familyName: 'Last name', organisation: 'Organisation', status: 'Status',
    migrationType: 'Migration', lastLogin: 'Last login', uen: 'UEN',
    name: 'Name', displayName: 'Display name', country: 'Country',
    memberCount: 'Members', total: 'Total', migrated: 'Migrated',
    pendingMigration: 'Pending', migratedPct: 'Progress'
  }[col] || col);

  return (
    <div>
      <div style={{ overflowX: 'auto', borderRadius: '0 0 10px 10px' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead>
            <tr style={{ background: '#f7f9fc', borderBottom: '1px solid #dde3ed' }}>
              {columns.map(col => (
                <th key={col} style={{
                  padding: '10px 14px', textAlign: 'left',
                  fontWeight: 600, color: '#52637a',
                  fontSize: 11, textTransform: 'uppercase',
                  letterSpacing: '.4px', whiteSpace: 'nowrap'
                }}>
                  {colLabel(col)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {pageRows.map((row, i) => (
              <tr key={i} style={{ borderBottom: '1px solid #eef1f6' }}
                onMouseEnter={e => e.currentTarget.style.background = '#f7f9fc'}
                onMouseLeave={e => e.currentTarget.style.background = ''}
              >
                {columns.map(col => (
                  <td key={col} style={{ padding: '10px 14px', color: '#111827', maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {(col === 'status' || col === 'migrationType')
                      ? statusBadge(row[col])
                      : formatCell(col, row[col])
                    }
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {pages > 1 && (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 18px', borderTop: '1px solid #eef1f6' }}>
          <span style={{ fontSize: 12, color: '#8e9bb0' }}>
            Showing {start + 1}–{Math.min(start + PAGE_SIZE, rows.length)} of {rows.length}
          </span>
          <div style={{ display: 'flex', gap: 6 }}>
            <button
              onClick={() => setPage(p => Math.max(0, p - 1))}
              disabled={page === 0}
              style={{ padding: '5px 12px', border: '1px solid #dde3ed', borderRadius: 6, background: '#fff', cursor: page === 0 ? 'not-allowed' : 'pointer', opacity: page === 0 ? .4 : 1, fontSize: 12 }}
            >← Prev</button>
            <button
              onClick={() => setPage(p => Math.min(pages - 1, p + 1))}
              disabled={page === pages - 1}
              style={{ padding: '5px 12px', border: '1px solid #dde3ed', borderRadius: 6, background: '#fff', cursor: page === pages - 1 ? 'not-allowed' : 'pointer', opacity: page === pages - 1 ? .4 : 1, fontSize: 12 }}
            >Next →</button>
          </div>
        </div>
      )}
    </div>
  );
}

function ReportSection({ report, filters }) {
  const [state,     setState]     = useState('idle');
  const [data,      setData]      = useState(null);
  const [error,     setError]     = useState('');
  const [page,      setPage]      = useState(0);
  const [dlState,   setDlState]   = useState('idle');
  const [dlError,   setDlError]   = useState('');
  const [collapsed, setCollapsed] = useState(false);

  const rows    = getRows(report, data);
  const columns = getColumns(report, rows);

  const runReport = async () => {
    setState('loading');
    setError('');
    setData(null);
    setPage(0);
    try {
      const params = buildParams(report, filters);
      const result = await fetchPreview(report.endpoint, params);
      setData(result);
      setState('done');
    } catch (e) {
      setError(e.message);
      setState('error');
    }
  };

  const download = async () => {
    setDlState('loading');
    setDlError('');
    try {
      const params   = buildParams(report, filters);
      const date     = new Date().toISOString().slice(0, 10);
      await downloadCSV(report.endpoint, params, `${report.id}-${date}.csv`);
      setDlState('done');
      setTimeout(() => setDlState('idle'), 3000);
    } catch (e) {
      setDlError(e.message);
      setDlState('error');
    }
  };

  return (
    <div className="report-section-card">
      <div
        className="report-section-header"
        onClick={() => setCollapsed(c => !c)}
        style={{ cursor: 'pointer' }}
      >
        <div className="report-info">
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{
              fontSize: 11, color: '#8e9bb0',
              transition: 'transform 160ms',
              display: 'inline-block',
              transform: collapsed ? 'rotate(-90deg)' : 'rotate(0deg)'
            }}>▼</span>
            <div className="report-label">{report.label}</div>
            {state === 'done' && rows.length > 0 && (
              <span style={{
                fontSize: 11, color: '#003781',
                background: '#e8edf5', padding: '1px 7px',
                borderRadius: 20, fontWeight: 600
              }}>
                {rows.length.toLocaleString()}
              </span>
            )}
          </div>
          {!collapsed && <div className="report-desc">{report.description}</div>}
        </div>
        <div
          style={{ display: 'flex', gap: 8, flexShrink: 0 }}
          onClick={e => e.stopPropagation()}
        >
          <button className="btn-run" onClick={runReport} disabled={state === 'loading'}>
            {state === 'loading' ? 'Loading...' : state === 'done' ? '↺ Refresh' : '▶ Run Report'}
          </button>
          <button className="btn-download" onClick={download} disabled={dlState === 'loading'}>
            {dlState === 'loading' ? 'Downloading...' : dlState === 'done' ? '✓ Downloaded' : '↓ CSV'}
          </button>
        </div>
      </div>

      {!collapsed && (
        <>
          {error   && <div style={{ padding: '10px 18px', color: '#991b1b', background: '#fee2e2', fontSize: 13 }}>{error}</div>}
          {dlError && <div style={{ padding: '10px 18px', color: '#991b1b', background: '#fee2e2', fontSize: 13 }}>{dlError}</div>}

          {state === 'loading' && (
            <div style={{ padding: '24px', textAlign: 'center', color: '#8e9bb0', fontSize: 13 }}>
              Loading data...
            </div>
          )}

          {state === 'done' && rows.length === 0 && (
            <div style={{ padding: '24px', textAlign: 'center', color: '#8e9bb0', fontSize: 13 }}>
              No results found for the selected filters
            </div>
          )}

          {state === 'done' && rows.length > 0 && (
            <PreviewTable
              report={report}
              rows={rows}
              columns={columns}
              page={page}
              setPage={setPage}
              total={rows.length}
            />
          )}
        </>
      )}
    </div>
  );
}

export default function ReportsPage() {
  const [filters, setFilters] = useState({
    organisations: [], statuses: [], countries: [],
    fromDate: '', toDate: '', lastLoginFrom: '', lastLoginTo: '', search: ''
  });
  const [orgOptions,  setOrgOptions]  = useState([]);
  const [loadingOrgs, setLoadingOrgs] = useState(true);
  const [presetType,  setPresetType]  = useState('created'); // 'created' or 'lastLogin'

  useEffect(() => {
    const loadOrgs = async () => {
      try {
        const headers = await getApiHeaders();
        const r       = await fetch(`${API_URL}/broker-firms`, { headers });
        const data    = await r.json();
        // handle array or object response
        const firms   = Array.isArray(data) ? data : (data.firms || data.items || []);
        setOrgOptions(
          firms.map(f => ({
            value: f.UEN || f.uen || '',
            label: f.displayName || f.name || '',
            sub:   f.UEN || f.uen || ''
          }))
        );
      } catch (e) {
        console.error('Failed to load orgs:', e);
      } finally {
        setLoadingOrgs(false);
      }
    };
    loadOrgs();
  }, []);

  const set   = (key, val) => setFilters(prev => ({ ...prev, [key]: val }));
  const clear = () => setFilters({ organisations: [], statuses: [], countries: [], fromDate: '', toDate: '', lastLoginFrom: '', lastLoginTo: '', search: '' });

  const activeCount = (
    filters.organisations.length + filters.statuses.length + filters.countries.length +
    (filters.fromDate ? 1 : 0) + (filters.toDate ? 1 : 0) +
    (filters.lastLoginFrom ? 1 : 0) + (filters.lastLoginTo ? 1 : 0) +
    (filters.search ? 1 : 0)
  );

  return (
    <div className="page">

      <div className="filter-panel">
        <div className="filter-header">
          <div className="filter-title">
            Filters
            {activeCount > 0 && <span className="filter-badge">{activeCount}</span>}
          </div>
          <button className="btn-clear" onClick={clear}>Clear all</button>
        </div>

        {/* Date presets */}
        <div className="date-presets-row">
          <div className="preset-type-toggle">
            <button
              className={`btn-preset-type${presetType === 'created' ? ' active' : ''}`}
              onClick={() => {
                setPresetType('created');
                // clear last login dates when switching
                setFilters(prev => ({ ...prev, lastLoginFrom: '', lastLoginTo: '' }));
              }}
            >
              Created date
            </button>
            <button
              className={`btn-preset-type${presetType === 'lastLogin' ? ' active' : ''}`}
              onClick={() => {
                setPresetType('lastLogin');
                // clear created dates when switching
                setFilters(prev => ({ ...prev, fromDate: '', toDate: '' }));
              }}
            >
              Last login
            </button>
          </div>

          <div className="date-presets">
            {DATE_PRESETS.map(preset => {
              const isActive = presetType === 'created'
                ? filters.fromDate === preset.fromDate && filters.toDate === preset.toDate
                : filters.lastLoginFrom === preset.fromDate && filters.lastLoginTo === preset.toDate;

              return (
                <button
                  key={preset.label}
                  className={`btn-preset${isActive ? ' active' : ''}`}
                  onClick={() => {
                    if (presetType === 'created') {
                      setFilters(prev => ({ ...prev, fromDate: preset.fromDate, toDate: preset.toDate }));
                    } else {
                      setFilters(prev => ({ ...prev, lastLoginFrom: preset.fromDate, lastLoginTo: preset.toDate }));
                    }
                  }}
                >
                  {preset.label}
                </button>
              );
            })}
          </div>
        </div>

        <div className="filters-grid">
          <div className="filter-group">
            <label>Organisation</label>
            <MultiSelect options={orgOptions} selected={filters.organisations} onChange={v => set('organisations', v)} placeholder="All organisations" loading={loadingOrgs} />
          </div>
          <div className="filter-group">
            <label>Status</label>
            <MultiSelect options={STATUS_OPTIONS} selected={filters.statuses} onChange={v => set('statuses', v)} placeholder="All statuses" />
          </div>
          <div className="filter-group">
            <label>Country</label>
            <MultiSelect options={COUNTRY_OPTIONS} selected={filters.countries} onChange={v => set('countries', v)} placeholder="All countries" />
          </div>
          <div className="filter-group">
            <label>Search</label>
            <input type="text" value={filters.search} onChange={e => set('search', e.target.value)} placeholder="Name or email" />
          </div>
          <div className="filter-group">
            <label>Created From</label>
            <input type="date" value={filters.fromDate} onChange={e => set('fromDate', e.target.value)} />
          </div>
          <div className="filter-group">
            <label>Created To</label>
            <input type="date" value={filters.toDate} onChange={e => set('toDate', e.target.value)} />
          </div>
          <div className="filter-group">
            <label>Last Login From</label>
            <input type="date" value={filters.lastLoginFrom} onChange={e => set('lastLoginFrom', e.target.value)} />
          </div>
          <div className="filter-group">
            <label>Last Login To</label>
            <input type="date" value={filters.lastLoginTo} onChange={e => set('lastLoginTo', e.target.value)} />
          </div>
        </div>
      </div>

      <div className="section-header">
        <span className="section-title">Reports</span>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {PERMANENT_REPORTS.map(r => (
          <ReportSection key={r.id} report={r} filters={filters} />
        ))}
      </div>

      <div className="section-header">
        <span className="section-title">ForgeRock Reports</span>
        <span className="section-badge">Temporary — remove after cutover</span>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {FORGEROCK_REPORTS.map(r => (
          <ReportSection key={r.id} report={r} filters={filters} />
        ))}
      </div>

    </div>
  );
}
