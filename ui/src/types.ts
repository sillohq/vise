/**
 * The shapes the Python side sends.
 *
 * These mirror `sillo_vise/panels/base.py` and `sillo_vise/dashboard/api.py`
 * field for field. Keeping them in step is a manual job, and the reason it is
 * bearable is that the interface does no computation: it draws what arrives, so
 * a field it does not know about is a field it does not need.
 */

export type Tone = 'good' | 'warn' | 'bad' | 'info' | 'muted'

export interface Tile {
  label: string
  value: string
  delta: string
  tone: Tone
  /** Sparkline points, oldest first. Already reduced server-side. */
  spark: number[]
}

export interface Chart {
  label: string
  note: string
  bars: number[]
}

export interface Column {
  label: string
  /** Breakpoint at which the column is hidden, as the mockups classify them. */
  cls?: string
  /**
   * `"state"` when the column holds a state and its cells should be drawn as
   * coloured pills. Decided by the panel, because only the panel knows what a
   * column means — a recorder buffer of `500` is not an HTTP server error.
   */
  kind?: string
}

export interface Table {
  columns: Column[]
  rows: string[][]
  /**
   * One event id per row, parallel to `rows`. Present only on panels whose
   * rows are events; a row with an id is clickable and opens it.
   */
  ids?: string[]
}

export interface Aside {
  label: string
  note: string
  /** name, detail, state — the third is optional. */
  rows: Array<[string, string] | [string, string, string]>
}

export interface Rendered {
  id: string
  /** The event kind this panel's rows are, or "" when they are summaries. */
  kind: string
  tiles: Tile[]
  chart: Chart | null
  table: Table | null
  aside: Aside | null
  toolbar: string[]
  /** Shown in place of data when there is a reason the panel is quiet. */
  note: string
}

export interface PanelSummary {
  id: string
  name: string
  group: string
  icon: string
  crumb: string
  summary: string
  watcher: string | null
  badge: string
}

export interface Group {
  name: string
  panels: PanelSummary[]
}

export interface MissingPanel {
  id: string
  name: string
  group: string
  reason: string
}

/** One event, in full, as `/api/detail/{kind}/{id}` returns it. */
export interface Detail {
  kind: string
  event: Record<string, unknown>
  /** Requests only: what this request caused, grouped by kind. */
  caused?: Record<string, Array<Record<string, unknown>>>
  counts?: Record<string, number>
  /** Requests only: whether body capture is on, so the panel can say why not. */
  bodies?: boolean
  /** Everything else: the request that was in flight when this was emitted. */
  request?: {
    id: string
    method: string
    path: string
    status: number
    route: string
  }
}

export interface Meta {
  app: {
    name: string
    target: string
    url: string
    environment: string
  }
  versions: {
    vise: string
    sillo: string
    python: string
    implementation: string
  }
  dashboard: {
    path: string
    title: string
    refresh_ms: number
  }
  groups: Group[]
  initial: string
  missing: MissingPanel[]
  recorder: {
    enabled: boolean
    buffer: number
    uptime: number
    totals: Record<string, number>
  }
}
