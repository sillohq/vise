/**
 * One panel's body: tiles, chart, table, rail, in that order.
 *
 * The order is the mockups' and it is not arbitrary. Tiles are the summary
 * somebody reads first; the chart is the same information over time; the table
 * is the detail behind it; the rail is context beside the detail. Reading down
 * the page goes from "what is happening" to "what exactly happened".
 */

import type { PanelSummary, Rendered } from '../types'
import { Chart } from './Chart'
import { Rail, Table } from './Table'
import { Tiles } from './Tiles'

export function Panel({
  panel,
  rendered,
  live,
  recording,
  onToggle,
  onOpen,
}: {
  panel: PanelSummary
  rendered: Rendered | null
  live: boolean
  recording: boolean
  onToggle: () => void
  /** Called with the kind and id of a row somebody clicked. */
  onOpen: (kind: string, id: string) => void
}) {
  return (
    <main className="panel">
      <div className="panel__head">
        <div>
          <div className="panel__crumb">{panel.crumb}</div>
          <h1 className="panel__title">{panel.name}</h1>
          <p className="panel__summary">{panel.summary}</p>
        </div>

        <div className="panel__status">
          <span className="panel__chip">
            <span className={`panel__pulse${live ? '' : ' panel__pulse--off'}`} />
            {live ? 'Live' : 'Paused'}
          </span>
          <span className="panel__chip">Last 1h</span>
          <button
            type="button"
            className="panel__chip panel__chip--action"
            onClick={onToggle}
            title={recording ? 'Stop collecting' : 'Start collecting again'}
          >
            {recording ? 'Pause' : 'Resume'}
          </button>
        </div>
      </div>

      {rendered === null ? (
        <div className="panel__note">Reading…</div>
      ) : (
        <>
          {rendered.note ? <div className="panel__note">{rendered.note}</div> : null}

          {rendered.toolbar.length > 0 ? (
            <div className="toolbar">
              {rendered.toolbar.map(chip => (
                <span className="toolbar__chip" key={chip}>
                  {chip}
                </span>
              ))}
            </div>
          ) : null}

          <Tiles tiles={rendered.tiles} />

          {rendered.chart ? <Chart chart={rendered.chart} /> : null}

          {rendered.table ? (
            <div className={`split${rendered.aside ? ' split--railed' : ''}`}>
              <Table
                table={rendered.table}
                empty={emptyMessage(panel)}
                onOpen={
                  rendered.kind
                    ? id => onOpen(rendered.kind, id)
                    : undefined
                }
              />
              {rendered.aside ? <Rail aside={rendered.aside} /> : null}
            </div>
          ) : null}
        </>
      )}
    </main>
  )
}

/**
 * What an empty table says.
 *
 * Panel-specific, because "nothing yet" means something different on each one:
 * on Requests it means nobody has visited, on Exceptions it means nothing has
 * broken, and only one of those is worth a sentence of reassurance.
 */
function emptyMessage(panel: PanelSummary): string {
  switch (panel.id) {
    case 'requests':
      return 'No requests yet. Open the application and this fills in.'
    case 'exceptions':
      return 'Nothing has raised since vise started.'
    case 'queries':
      return 'No queries yet.'
    case 'cache':
      return 'No cache operations yet.'
    case 'outgoing':
      return 'The application has not called anybody else yet.'
    case 'queues':
      return 'No jobs have run since vise started.'
    case 'mail':
      return 'No mail has been sent since vise started.'
    case 'logs':
      return 'Nothing has been logged at the configured level.'
    default:
      return 'Nothing to show yet.'
  }
}
