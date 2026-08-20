/**
 * The bar across the top: where this is, and whether it is still collecting.
 *
 * The pause control is the only write the interface offers, and it acts on the
 * recorder rather than on the application. A dashboard that could retry a job or
 * flush a cache would be an interesting product and a different one; a
 * development tool changing production state by accident is a story nobody
 * wants to be in.
 */

import type { Meta } from '../types'

export function Chrome({
  meta,
  recording,
  onToggle,
  connected,
}: {
  meta: Meta
  recording: boolean
  onToggle: () => void
  connected: boolean
}) {
  return (
    <div className="chrome">
      <div className="chrome__dots">
        <span className="chrome__dot" />
        <span className="chrome__dot" />
        <span className="chrome__dot" />
      </div>

      <div className="chrome__url">
        <span
          className={`chrome__live${recording && connected ? '' : ' chrome__live--paused'}`}
          title={
            !recording
              ? 'The recorder is paused'
              : connected
                ? 'Live'
                : 'Reconnecting to the live feed'
          }
        />
        <span>
          {meta.app.url}
          {meta.dashboard.path}
        </span>
      </div>

      <button
        type="button"
        className="chrome__action"
        onClick={onToggle}
        title={recording ? 'Stop collecting' : 'Start collecting again'}
      >
        {recording ? 'Pause' : 'Resume'}
      </button>

      <span className="chrome__env">{meta.app.environment}</span>
    </div>
  )
}
