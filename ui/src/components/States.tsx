/**
 * What is shown when there is no panel to show.
 *
 * Three cases, and they are genuinely different. Loading is a moment. An error
 * is something to read. And "no panels at all" is a real state on a live-only
 * dashboard: an application with the recorder off has nothing to watch, and
 * saying so with the list of what was looked for is more useful than an empty
 * sidebar.
 */

import type { MissingPanel } from '../types'

export function Loading() {
  return (
    <div className="state">
      <div>
        <p className="state__body">Reading the recorder…</p>
      </div>
    </div>
  )
}

export function Failed({ error, onRetry }: { error: string; onRetry: () => void }) {
  return (
    <div className="state">
      <div>
        <h1 className="state__title">The dashboard could not reach the server.</h1>
        <p className="state__body">
          {error}
          <br />
          <br />
          If <code>vise serve</code> has restarted, this reconnects by itself in a
          moment.
        </p>
        <br />
        <button type="button" className="chrome__action" onClick={onRetry}>
          Try again
        </button>
      </div>
    </div>
  )
}

export function NoPanels({ missing }: { missing: MissingPanel[] }) {
  return (
    <div className="state">
      <div>
        <h1 className="state__title">Nothing is being watched.</h1>
        <p className="state__body">
          A panel appears when it can observe something real. None of them can,
          which usually means the recorder is off — check <code>[recorder]
          enabled</code> in <code>.vise</code>.
        </p>

        {missing.length > 0 ? (
          <div className="missing">
            <div className="missing__title">What was looked for</div>
            {missing.map(panel => (
              <div className="missing__row" key={panel.id}>
                <span>{panel.id}</span>
                <span>{panel.reason}</span>
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  )
}
