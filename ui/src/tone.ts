/**
 * Colouring a table cell.
 *
 * The vocabulary is the framework's own — `TaskStatus`, `QueueHealth`,
 * `CircuitState`, HTTP status classes — so a state Sillo does not have cannot be
 * rendered as though it does. This mirrors `toneFor()` in the website's
 * `AppMock.tsx`, and where the two disagree the website is right.
 *
 * A word not in any list gets no pill at all, which is deliberate: an unknown
 * value should look like text, not like a state somebody has to interpret.
 */

const GOOD = new Set([
  'completed',
  'healthy',
  'closed',
  'hit',
  'stored',
  'resolved',
  'required',
  'published',
  'active',
  'enabled',
  'public',
  'live',
  'sent',
  'touched',
])

const INFO = new Set([
  'running',
  'scheduled',
  'info',
  'queued',
  'in_review',
  'invited',
  'signed',
  'connect',
  'send',
  'receive',
])

const WARN = new Set([
  'retrying',
  'degraded',
  'half_open',
  'warning',
  'warn',
  'muted',
  'evicted',
  'draft',
  'private',
  'suppressed',
  'slow',
  'handled',
])

const BAD = new Set([
  'failed',
  'open',
  'stalled',
  'error',
  'critical',
  'crit',
  'cancelled',
  'locked',
  'disabled',
  'archived',
  'closed_code',
])

const NEUTRAL = new Set([
  'pending',
  'miss',
  'debug',
  'get',
  'set',
  'forget',
  'delete',
  'generated',
  'handler',
  'unknown',
  'cleared',
  'closed_conn',
])

/**
 * The pill class for a cell, or an empty string for no pill.
 *
 * HTTP status codes are matched by their class rather than listed: 2xx is good,
 * 4xx is the client's problem and therefore amber, 5xx is the application's and
 * therefore the brand red.
 */
export function pillFor(value: string): string {
  const word = value.toLowerCase()

  if (GOOD.has(word)) return 'pill pill-good'
  if (INFO.has(word)) return 'pill pill-info'
  if (WARN.has(word)) return 'pill pill-warn'
  if (BAD.has(word)) return 'pill pill-bad'
  if (NEUTRAL.has(word)) return 'pill pill-muted'

  if (/^2\d\d$/.test(word)) return 'pill pill-good'
  if (/^3\d\d$/.test(word)) return 'pill pill-info'
  if (/^4\d\d$/.test(word)) return 'pill pill-warn'
  if (/^5\d\d$/.test(word)) return 'pill pill-bad'

  return ''
}

/** The dot class for a side-rail state. */
export function dotFor(state: string): string {
  if (state === 'healthy') return 'rail__state dot-healthy'
  if (state === 'degraded') return 'rail__state dot-degraded'
  return 'rail__state dot-stalled'
}

/**
 * A column's responsive class.
 *
 * The Python side sends Tailwind-shaped strings — `hidden md:table-cell` — so
 * that one vocabulary describes the columns in the mockups, in the panels and
 * here. This reduces one to the class that actually exists in `app.css`.
 */
export function columnClass(cls: string | undefined): string {
  if (!cls) return ''
  if (cls.includes('sm:')) return 'hide-sm'
  if (cls.includes('md:')) return 'hide-md'
  if (cls.includes('lg:')) return 'hide-lg'
  if (cls.includes('xl:')) return 'hide-xl'
  return ''
}
