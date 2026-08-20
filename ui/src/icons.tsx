/**
 * The sidebar icons.
 *
 * Drawn inline rather than pulled from a library. A dashboard shipped as a
 * self-contained bundle inside a Python wheel should not carry an icon package
 * to draw fourteen glyphs, and these are the same shapes the mockups use.
 *
 * Every icon is a 16-unit square on a 1.4 stroke, so they sit on one optical
 * weight in the sidebar. `currentColor` throughout, so the active state is a
 * colour change on the parent rather than a second icon.
 */

import type { SVGProps } from 'react'

type Icon = (props: SVGProps<SVGSVGElement>) => JSX.Element

function base(props: SVGProps<SVGSVGElement>) {
  return {
    viewBox: '0 0 16 16',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 1.4,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
    'aria-hidden': true,
    ...props,
  }
}

const Grid: Icon = props => (
  <svg {...base(props)}>
    <rect x="2" y="2" width="5" height="5" rx="1" />
    <rect x="9" y="2" width="5" height="5" rx="1" />
    <rect x="2" y="9" width="5" height="5" rx="1" />
    <rect x="9" y="9" width="5" height="5" rx="1" />
  </svg>
)

const Route: Icon = props => (
  <svg {...base(props)}>
    <circle cx="4" cy="4" r="2" />
    <circle cx="12" cy="12" r="2" />
    <path d="M4 6.5v2a3 3 0 0 0 3 3h3" />
  </svg>
)

const Orm: Icon = props => (
  <svg {...base(props)}>
    <ellipse cx="8" cy="4" rx="5" ry="2" />
    <path d="M3 4v8c0 1.1 2.24 2 5 2s5-.9 5-2V4" />
    <path d="M3 8c0 1.1 2.24 2 5 2s5-.9 5-2" />
  </svg>
)

const Cache: Icon = props => (
  <svg {...base(props)}>
    <rect x="2" y="3" width="12" height="10" rx="1.5" />
    <path d="M2 6.5h12M5.5 6.5v6.5" />
  </svg>
)

const Outbound: Icon = props => (
  <svg {...base(props)}>
    <path d="M6.5 9.5 13 3" />
    <path d="M9 3h4v4" />
    <path d="M13 9.5V12a1.5 1.5 0 0 1-1.5 1.5h-7A1.5 1.5 0 0 1 3 12V5a1.5 1.5 0 0 1 1.5-1.5H7" />
  </svg>
)

const Queue: Icon = props => (
  <svg {...base(props)}>
    <rect x="2" y="2.5" width="12" height="3" rx="1" />
    <rect x="2" y="6.5" width="12" height="3" rx="1" />
    <rect x="2" y="10.5" width="12" height="3" rx="1" />
  </svg>
)

const Worker: Icon = props => (
  <svg {...base(props)}>
    <rect x="3" y="3" width="10" height="10" rx="2" />
    <rect x="6" y="6" width="4" height="4" rx="0.5" />
    <path d="M6 1.5v1.5M10 1.5v1.5M6 13v1.5M10 13v1.5M1.5 6H3M1.5 10H3M13 6h1.5M13 10h1.5" />
  </svg>
)

const Schedule: Icon = props => (
  <svg {...base(props)}>
    <circle cx="8" cy="8" r="6" />
    <path d="M8 4.5V8l2.5 1.5" />
  </svg>
)

const Alert: Icon = props => (
  <svg {...base(props)}>
    <path d="M8 2.5 14 13H2L8 2.5Z" />
    <path d="M8 6.5v3M8 11.2v.3" />
  </svg>
)

const Terminal: Icon = props => (
  <svg {...base(props)}>
    <rect x="2" y="3" width="12" height="10" rx="1.5" />
    <path d="M5 6.5 7 8.5 5 10.5M8.5 10.5H11" />
  </svg>
)

const Realtime: Icon = props => (
  <svg {...base(props)}>
    <path d="M1.5 8h2.5l2-4 3 8 2-4h3.5" />
  </svg>
)

const Mail: Icon = props => (
  <svg {...base(props)}>
    <rect x="2" y="3.5" width="12" height="9" rx="1.5" />
    <path d="m2.5 5 5.5 4 5.5-4" />
  </svg>
)

const Spark: Icon = props => (
  <svg {...base(props)}>
    <path d="M8 1.5 9.6 6l4.4 1.6L9.6 9.2 8 13.6 6.4 9.2 2 7.6 6.4 6 8 1.5Z" />
  </svg>
)

const Layers: Icon = props => (
  <svg {...base(props)}>
    <path d="m8 2 6 3-6 3-6-3 6-3Z" />
    <path d="m2 8.5 6 3 6-3" />
    <path d="m2 11.5 6 3 6-3" />
  </svg>
)

/** Icon by the name a panel declares. */
export const ICONS: Record<string, Icon> = {
  grid: Grid,
  route: Route,
  orm: Orm,
  cache: Cache,
  outbound: Outbound,
  queue: Queue,
  worker: Worker,
  schedule: Schedule,
  alert: Alert,
  terminal: Terminal,
  realtime: Realtime,
  mail: Mail,
  spark: Spark,
  layers: Layers,
}

/**
 * The icon a panel asked for, or a neutral one.
 *
 * Falling back rather than throwing: a panel added on the Python side before
 * its icon is drawn here should appear with a placeholder, not break the
 * sidebar.
 */
export function iconFor(name: string): Icon {
  return ICONS[name] ?? Grid
}

/** The Sillo mark, as the banner and the sidebar draw it. */
export function Mark(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 16 16" fill="currentColor" aria-hidden="true" {...props}>
      <path d="M8 1.2 15 14H1L8 1.2Z" />
    </svg>
  )
}
