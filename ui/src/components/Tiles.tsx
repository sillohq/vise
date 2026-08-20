/**
 * The stat tiles, four across.
 *
 * A tile draws what it is given and judges nothing. Whether a number is good
 * news was decided on the Python side, by the panel that knows what the number
 * measures — latency falling is good, queue size rising is not, and throughput
 * is the other way round again. Re-deriving that here would be a second opinion
 * that could disagree with the first.
 */

import type { Tile } from '../types'

/** A sparkline over the points the server sent. */
function Spark({ points }: { points: number[] }) {
  if (points.length < 2) {
    return <svg className="tile__spark" viewBox="0 0 72 20" aria-hidden="true" />
  }

  const highest = Math.max(...points, 1)
  const step = 72 / (points.length - 1)

  // Drawn upside down: SVG's y grows downward and a chart's value grows up.
  const path = points
    .map((value, index) => `${(index * step).toFixed(1)},${(18 - (value / highest) * 16).toFixed(1)}`)
    .join(' ')

  return (
    <svg className="tile__spark" viewBox="0 0 72 20" preserveAspectRatio="none" aria-hidden="true">
      <polyline points={path} fill="none" stroke="currentColor" strokeWidth={1.4} />
    </svg>
  )
}

export function Tiles({ tiles }: { tiles: Tile[] }) {
  if (tiles.length === 0) return null

  return (
    <div className="tiles">
      {tiles.map(tile => (
        <div className="tile" key={tile.label}>
          <div className="tile__label" title={tile.label}>
            {tile.label}
          </div>
          <div className="tile__figures">
            <span className="tile__value">{tile.value}</span>
            <span className={`tile__delta tone-${tile.tone}`}>{tile.delta}</span>
          </div>
          <Spark points={tile.spark} />
        </div>
      ))}
    </div>
  )
}
