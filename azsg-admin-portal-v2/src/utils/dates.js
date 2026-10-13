// Dates and times in the portal are shown in the VIEWER'S time zone (the browser's), and the zone is always
// stated, so a screenshot pasted into a ticket still says which time it means.
//
// The order of day and month follows the browser's language, but the month is always a word ("9 Oct 2026"),
// so 10/9 and 9/10 can never be misread. `locale` and `timeZone` can be passed to override the browser's
// (only the tests do that).

const toDate = value => {
  if (value === null || value === undefined || value === '') return null;
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
};

// the zone is stated as an offset ("GMT+8"); older browsers without that option get the short name
function withZone(date, options, { locale, timeZone } = {}) {
  const base = { ...options, timeZone };
  try {
    return new Intl.DateTimeFormat(locale, { ...base, timeZoneName: 'shortOffset' }).format(date);
  } catch (e) {
    return new Intl.DateTimeFormat(locale, { ...base, timeZoneName: 'short' }).format(date);
  }
}

const DATE_PART = { day: 'numeric', month: 'short', year: 'numeric' };

// "9 Oct 2026, 12:22 pm GMT+8"   (seconds: true adds them, for events where the second matters)
export function formatDateTime(value, { seconds = false, locale, timeZone } = {}) {
  const date = toDate(value);
  if (!date) return value === null || value === undefined ? '' : String(value);
  return withZone(date, { ...DATE_PART, hour: 'numeric', minute: '2-digit', ...(seconds ? { second: '2-digit' } : {}) }, { locale, timeZone });
}

// "12:22:45 pm GMT+8"
export function formatTimeOfDay(value, { locale, timeZone } = {}) {
  const date = toDate(value);
  if (!date) return value === null || value === undefined ? '' : String(value);
  return withZone(date, { hour: 'numeric', minute: '2-digit', second: '2-digit' }, { locale, timeZone });
}

// "9 Oct 2026"  — a date has no zone shown; hovering gives the full moment (see describeMoment)
export function formatDate(value, { locale, timeZone } = {}) {
  const date = toDate(value);
  if (!date) return value === null || value === undefined ? '' : String(value);
  return new Intl.DateTimeFormat(locale, { ...DATE_PART, timeZone }).format(date);
}

// For the hover text: "Singapore Standard Time (Asia/Singapore) · 2026-10-09 04:22:45 UTC"
export function describeMoment(value, { locale, timeZone } = {}) {
  const date = toDate(value);
  if (!date) return '';
  const zone = new Intl.DateTimeFormat(locale, { timeZone }).resolvedOptions().timeZone;
  let longName = '';
  try {
    longName = new Intl.DateTimeFormat(locale, { timeZone, timeZoneName: 'long' }).formatToParts(date).find(p => p.type === 'timeZoneName').value;
  } catch (e) { /* the name is a nicety; the zone id and the UTC time below are enough */ }
  const utc = date.toISOString().replace('T', ' ').slice(0, 19) + ' UTC';
  return `${longName ? longName + ' ' : ''}(${zone}) · ${utc}`;
}
