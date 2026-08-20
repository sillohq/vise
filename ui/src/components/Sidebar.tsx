/**
 * The sidebar, grouped as the Foreman page groups it.
 *
 * Groups arrive from the server already filtered: a group with no live panels
 * is not sent, and a panel whose watcher is not collecting is not in it. So this
 * renders what it is given without asking whether anything should be hidden —
 * "live only" is a decision made once, on the Python side, where the probes are.
 *
 * It also carries what the mocked-up browser chrome used to: which application
 * this is, and where. That was a decorative frame around a real tool, and a
 * fake address bar in something you reach through a real one is a strange thing
 * to look at every day.
 */

import type { Group, Meta } from '../types'
import { Mark, iconFor } from '../icons'

export function Sidebar({
  groups,
  active,
  onSelect,
  meta,
}: {
  groups: Group[]
  active: string
  onSelect: (id: string) => void
  meta: Meta
}) {
  return (
    <aside className="sidebar">
      <div className="sidebar__brand">
        <Mark />
        <span>{meta.dashboard.title}</span>
        <span className="sidebar__env">{meta.app.environment}</span>
      </div>

      <div className="sidebar__app">
        <div className="sidebar__appName" title={meta.app.name}>
          {meta.app.name}
        </div>
        <div className="sidebar__appUrl" title={meta.app.target}>
          {meta.app.url}
        </div>
      </div>

      {groups.map(group => (
        <div className="sidebar__group" key={group.name}>
          <div className="sidebar__label">{group.name}</div>
          {group.panels.map(panel => {
            const Icon = iconFor(panel.icon)
            return (
              <button
                key={panel.id}
                type="button"
                className="sidebar__item"
                aria-pressed={panel.id === active}
                onClick={() => onSelect(panel.id)}
                title={panel.summary}
              >
                <Icon />
                <span>{panel.name}</span>
                {panel.badge ? <span className="sidebar__badge">{panel.badge}</span> : null}
              </button>
            )
          })}
        </div>
      ))}
    </aside>
  )
}
