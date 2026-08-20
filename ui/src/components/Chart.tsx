/**
 * The bar chart.
 *
 * Bars are per-minute counts, oldest on the left, and the right-hand quarter is
 * drawn in the brand red — the mockups do this, and it turns out to carry real
 * meaning here: the series is anchored to the current minute, so the coloured
 * bars are always "recently", whatever the traffic did.
 *
 * Scaled to the tallest bar rather than to a fixed ceiling. A dev server serving
 * four requests a minute and one serving four thousand should both produce a
 * readable shape.
 */

import type { Chart as ChartData } from '../types'

/** How much of the chart's right-hand side is drawn as recent. */
const RECENT = 0.25

export function Chart({ chart }: { chart: ChartData }) {
  const tallest = Math.max(...chart.bars, 1)
  const boundary = chart.bars.length * (1 - RECENT)

  return (
    <div className="chart">
      <div className="chart__head">
        <span className="chart__label">{chart.label}</span>
        <span className="chart__note">{chart.note}</span>
      </div>
      <div className="chart__bars">
        {chart.bars.map((value, index) => (
          <div
            key={index}
            className={`chart__bar${index >= boundary ? ' chart__bar--recent' : ''}`}
            // A zero bar still gets a sliver, so a quiet minute reads as a
            // minute that happened rather than as a gap in the chart.
            style={{ height: `${Math.max(2, (value / tallest) * 100)}%` }}
            title={`${value}`}
          />
        ))}
      </div>
    </div>
  )
}
