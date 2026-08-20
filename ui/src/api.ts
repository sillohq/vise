/**
 * Talking to the Python side.
 *
 * Every path is relative to where the interface was served from, never
 * absolute. The dashboard's mount point is configurable — `[dashboard] path`
 * defaults to `/__sillo/foreman` and a project can move it — so a hard-coded
 * path would work on the default and nowhere else.
 */

import type { Meta, Rendered } from './types'

/**
 * The prefix the dashboard is mounted under, worked out from the current URL.
 *
 * The index is served for any unknown path under the mount, so the browser may
 * be at `/__sillo/foreman`, at `/__sillo/foreman/`, or at a deeper path the
 * interface routed to itself. Trimming back to the mount means finding where
 * the interface's own routing begins, and the one thing known for certain is
 * that `/api/meta` sits directly under the mount.
 */
export function mountPath(): string {
  const path = window.location.pathname.replace(/\/+$/, '')

  // The interface's own routes are one segment deep at most, and are all
  // named after panels. Anything else is part of the mount.
  const segments = path.split('/')
  const last = segments[segments.length - 1] ?? ''

  return KNOWN_ROUTES.has(last) ? segments.slice(0, -1).join('/') : path
}

/**
 * Client-side routes the interface owns, so they can be trimmed off the mount.
 *
 * These are the panel ids, and they have to stay in step with
 * `sillo_vise/panels/registry.py`. The cost of drifting is small and specific:
 * a panel added there and not here would work when clicked and 404 its own API
 * calls when linked to directly, because the mount would be measured one
 * segment too long.
 */
const KNOWN_ROUTES = new Set([
  'overview',
  'requests',
  'queries',
  'cache',
  'outgoing',
  'queues',
  'workers',
  'schedules',
  'exceptions',
  'logs',
  'realtime',
  'mail',
  'routes',
  'config',
])

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${mountPath()}${path}`, {
    // Same-origin by construction; sending credentials is what lets the
    // dashboard sit behind a project's own session auth when `access = "open"`.
    credentials: 'same-origin',
    ...init,
  })

  if (!response.ok) {
    const body = await response.json().catch(() => ({ error: response.statusText }))
    throw new Error(body.error ?? `Request failed with ${response.status}`)
  }

  return response.json() as Promise<T>
}

/** Everything the interface needs before it draws anything. */
export function fetchMeta(): Promise<Meta> {
  return json<Meta>('/api/meta')
}

/** One panel's current contents. */
export function fetchPanel(id: string): Promise<Rendered> {
  return json<Rendered>(`/api/panels/${id}`)
}

/** One of the recorder's own actions: pause, resume or clear. */
export function performAction(name: string): Promise<{ enabled: boolean }> {
  return json<{ enabled: boolean }>(`/api/actions/${name}`, { method: 'POST' })
}

/** Open the live feed for one panel. */
export function openStream(id: string): EventSource {
  return new EventSource(`${mountPath()}/api/stream?panel=${encodeURIComponent(id)}`)
}
