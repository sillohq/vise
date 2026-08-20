/**
 * The drawer a table row opens.
 *
 * One event, in full — which means something different per kind, and the
 * difference is the point. A request brings its headers both ways, its bodies
 * and everything it caused; a query brings its statement and bindings; a job
 * brings its payload and its traceback. The table showed a cell; this is where
 * the cell came from.
 *
 * It slides over rather than replacing the table, so the row somebody clicked
 * is still on screen behind it and the next one is one click away.
 */

import { useEffect } from 'react'

import type { Detail as DetailData } from '../types'
import { pillFor } from '../tone'

/** Fields shown as the summary line rather than in the field list. */
const HEADLINE: Record<string, string[]> = {
  request: ['method', 'path'],
  query: ['sql'],
  cache: ['key'],
  outgoing: ['method', 'url'],
  job: ['task'],
  log: ['message'],
  mail: ['subject'],
  exception: ['type', 'message'],
  schedule: ['job'],
  websocket: ['channel'],
  signal: ['name'],
}

/**
 * Fields the table formats and the drawer should too.
 *
 * Without this a duration arrives as `1.0121770028490573`, which is true and
 * unreadable — and worse, is a different number from the `1ms` in the row the
 * reader just clicked. One value should not have two spellings.
 */
const FORMAT: Record<string, (value: number) => string> = {
  duration_ms: duration,
  at: when,
  response_bytes: size,
  request_bytes: size,
  bytes: size,
  ttl: seconds,
  uptime: seconds,
}

/** Fields never listed: internal, or shown somewhere better. */
const HIDDEN = new Set([
  'kind',
  'id',
  'request_id',
  'headers',
  'response_headers',
  'body',
  'response_body',
  'traceback',
  'payload',
  'params',
  'fields',
])

export function Detail({
  detail,
  loading,
  error,
  onClose,
  onOpen,
}: {
  detail: DetailData | null
  loading: boolean
  error: string
  onClose: () => void
  onOpen: (kind: string, id: string) => void
}) {
  // Escape closes it. A drawer that can only be dismissed by finding a small
  // button is a drawer people leave open.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <>
      <div className="drawer__scrim" onClick={onClose} role="presentation" />
      <aside className="drawer" aria-label="Event detail">
        <div className="drawer__head">
          <div className="drawer__title">
            {detail ? headline(detail) : loading ? 'Reading…' : 'Not available'}
          </div>
          <button type="button" className="drawer__close" onClick={onClose} title="Close (Esc)">
            ✕
          </button>
        </div>

        <div className="drawer__body">
          {error ? <p className="drawer__note">{error}</p> : null}

          {detail ? (
            <>
              {detail.request ? (
                <Section label="Caused by">
                  <button
                    type="button"
                    className="drawer__link"
                    onClick={() => onOpen('request', detail.request!.id)}
                  >
                    {detail.request.method} {detail.request.path}
                    {detail.request.route ? ` · ${detail.request.route}` : ''}
                  </button>
                </Section>
              ) : null}

              <Fields event={detail.event} />

              <Headers
                label="Request headers"
                pairs={detail.event.headers as [string, string][] | undefined}
              />
              <Headers
                label="Response headers"
                pairs={detail.event.response_headers as [string, string][] | undefined}
              />

              <Body
                label="Request body"
                text={detail.event.body as string | undefined}
                enabled={detail.bodies}
              />
              <Body
                label="Response body"
                text={detail.event.response_body as string | undefined}
                enabled={detail.bodies}
              />

              <Blob label="Arguments" value={detail.event.payload} />
              <Blob label="Bindings" value={detail.event.params} />
              <Blob label="Fields" value={detail.event.fields} />

              <Trace text={detail.event.traceback as string | undefined} />

              <Caused detail={detail} onOpen={onOpen} />
            </>
          ) : null}
        </div>
      </aside>
    </>
  )
}

function headline(detail: DetailData): string {
  const parts = HEADLINE[detail.kind] ?? []
  const text = parts
    .map(name => detail.event[name])
    .filter(Boolean)
    .join(' ')

  return text ? String(text).slice(0, 120) : detail.kind
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="drawer__section">
      <div className="drawer__label">{label}</div>
      {children}
    </div>
  )
}

/** Every scalar field the event carries, as a definition list. */
function Fields({ event }: { event: Record<string, unknown> }) {
  const rows = Object.entries(event).filter(
    ([name, value]) =>
      !HIDDEN.has(name) &&
      value !== '' &&
      value !== null &&
      value !== undefined &&
      typeof value !== 'object',
  )

  if (rows.length === 0) return null

  return (
    <Section label="Event">
      <dl className="drawer__fields">
        {rows.map(([name, value]) => {
          const format = FORMAT[name]
          const text =
            format && typeof value === 'number' ? format(value) : String(value)
          const pill = name === 'status' || name === 'level' ? pillFor(text) : ''

          return (
            <div className="drawer__field" key={name}>
              <dt>{name}</dt>
              <dd>{pill ? <span className={pill}>{text}</span> : text}</dd>
            </div>
          )
        })}
      </dl>
    </Section>
  )
}

function Headers({ label, pairs }: { label: string; pairs?: [string, string][] }) {
  if (!pairs || pairs.length === 0) return null

  return (
    <Section label={label}>
      <dl className="drawer__fields">
        {pairs.map(([name, value], index) => (
          <div className="drawer__field" key={`${name}-${index}`}>
            <dt>{name}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
    </Section>
  )
}

function Body({
  label,
  text,
  enabled,
}: {
  label: string
  text?: string
  enabled?: boolean
}) {
  // An absent body has two very different causes, and saying which is the
  // difference between "there was nothing to show" and "you can switch this on".
  if (!text && enabled === false) {
    return (
      <Section label={label}>
        <p className="drawer__note">
          Bodies are not being captured. Set <code>capture_bodies = true</code> under{' '}
          <code>[recorder]</code> in <code>.vise</code>.
        </p>
      </Section>
    )
  }

  if (!text) return null

  return (
    <Section label={label}>
      <pre className="drawer__pre">{pretty(text)}</pre>
    </Section>
  )
}

function Blob({ label, value }: { label: string; value: unknown }) {
  if (value === null || value === undefined || value === '') return null

  return (
    <Section label={label}>
      <pre className="drawer__pre">{JSON.stringify(value, null, 2)}</pre>
    </Section>
  )
}

function Trace({ text }: { text?: string }) {
  if (!text) return null

  return (
    <Section label="Traceback">
      <pre className="drawer__pre drawer__pre--trace">{text}</pre>
    </Section>
  )
}

/** Everything a request caused, each row clickable in turn. */
function Caused({
  detail,
  onOpen,
}: {
  detail: DetailData
  onOpen: (kind: string, id: string) => void
}) {
  const caused = detail.caused
  if (!caused || Object.keys(caused).length === 0) return null

  return (
    <Section label="What it caused">
      {Object.entries(caused).map(([kind, rows]) => (
        <div className="drawer__caused" key={kind}>
          <div className="drawer__causedHead">
            {kind} <span>{rows.length}</span>
          </div>
          {rows.map((row, index) => (
            <button
              type="button"
              className="drawer__link"
              key={`${kind}-${index}`}
              onClick={() => onOpen(kind, String(row.id))}
            >
              {summarise(kind, row)}
            </button>
          ))}
        </div>
      ))}
    </Section>
  )
}

function summarise(kind: string, row: Record<string, unknown>): string {
  const parts = HEADLINE[kind] ?? []
  const text = parts
    .map(name => row[name])
    .filter(Boolean)
    .join(' ')

  return (text ? String(text) : kind).slice(0, 90)
}

/** A unix timestamp as a readable local time. */
function when(at: number): string {
  return new Date(at * 1000).toLocaleTimeString()
}

/**
 * A duration in milliseconds, spelled the way the table spells it.
 *
 * Deliberately the same thresholds as `sillo_vise/logs/format.py`. Two
 * renderings of one number that disagree is worse than either.
 */
function duration(milliseconds: number): string {
  if (milliseconds < 1) return `${Math.round(milliseconds * 1000)}\u00b5s`
  if (milliseconds < 1000) return `${Math.round(milliseconds)}ms`
  if (milliseconds < 60_000) return `${(milliseconds / 1000).toFixed(2)}s`

  const minutes = Math.floor(milliseconds / 60_000)
  const rest = String(Math.floor((milliseconds % 60_000) / 1000)).padStart(2, '0')
  return `${minutes}m ${rest}s`
}

/** A byte count, decimal, matching every browser network panel. */
function size(count: number): string {
  const units = ['B', 'kB', 'MB', 'GB', 'TB']
  let value = count
  let unit = 0

  while (value >= 1000 && unit < units.length - 1) {
    value /= 1000
    unit += 1
  }

  return unit === 0 ? `${Math.round(value)} B` : `${value.toFixed(1)} ${units[unit]}`
}

/** A count of seconds. */
function seconds(value: number): string {
  return `${value}s`
}

/**
 * Indent a body when it is JSON.
 *
 * Tried and discarded rather than assumed: a body that is not JSON is left
 * exactly as it arrived, because reformatting somebody's payload is a way of
 * hiding what was actually sent.
 */
function pretty(text: string): string {
  const trimmed = text.trim()
  if (!trimmed.startsWith('{') && !trimmed.startsWith('[')) return text

  try {
    return JSON.stringify(JSON.parse(trimmed), null, 2)
  } catch {
    return text
  }
}
