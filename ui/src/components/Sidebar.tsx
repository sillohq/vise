/**
 * The sidebar, grouped as the Foreman page groups it.
 *
 * Groups arrive from the server already filtered: a group with no live panels
 * is not sent, and a panel whose watcher is not collecting is not in it. So this
 * renders what it is given without asking whether anything should be hidden —
 * "live only" is a decision made once, on the Python side, where the probes are.
 */

import type { Group } from '../types'
import { Mark, iconFor } from '../icons'

export function Sidebar({
  groups,
  active,
  onSelect,
  title,
}: {
  groups: Group[]
  active: string
  onSelect: (id: string) => void
  title: string
}) {
  return (
    <aside className="sidebar">
      <div className="sidebar__brand">
        <Mark />
        <span>{title}</span>
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
