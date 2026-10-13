import React from 'react';
import { formatDateTime, formatDate, formatTimeOfDay, describeMoment } from '../utils/dates';

/**
 * A date or time in the viewer's own time zone, with the zone stated and the full moment on hover.
 *   <DateTime value={iso} />                 9 Oct 2026, 12:22 pm GMT+8
 *   <DateTime value={iso} seconds />         9 Oct 2026, 12:22:45 pm GMT+8
 *   <DateTime value={iso} dateOnly />        9 Oct 2026
 *   <DateTime value={date} timeOnly />       12:22:45 pm GMT+8
 * A value that is not a date is shown as it is.
 */
export default function DateTime({ value, seconds = false, dateOnly = false, timeOnly = false }) {
  const date = value instanceof Date ? value : new Date(value);
  if (value === null || value === undefined || value === '' || Number.isNaN(date.getTime())) {
    return <>{value === null || value === undefined ? '' : String(value)}</>;
  }
  const text = dateOnly ? formatDate(date) : timeOnly ? formatTimeOfDay(date) : formatDateTime(date, { seconds });
  return <time dateTime={date.toISOString()} title={describeMoment(date)}>{text}</time>;
}
