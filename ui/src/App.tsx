/**
 * Foreman.
 *
 * The whole interface is a renderer. Which panels exist, what each one shows and
 * what every number means were all decided on the Python side; this holds which
 * panel is open, keeps a live feed pointed at it, and draws.
 *
 * That division is deliberate. Two implementations of "what is the p95" would
 * eventually disagree, and the one in the browser would be the one nobody
 * tested.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'

import { fetchMeta, performAction } from './api'
import { Chrome } from './components/Chrome'
import { Panel } from './components/Panel'
import { Sidebar } from './components/Sidebar'
import { Failed, Loading, NoPanels } from './components/States'
import { useLivePanel } from './live'
import type { Meta, PanelSummary } from './types'

/** How long to wait before retrying after the server goes away. */
const RETRY_MS = 2000

export function App() {
  const [meta, setMeta] = useState<Meta | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [active, setActive] = useState<string | null>(null)
  const [recording, setRecording] = useState(true)

  const load = useCallback(() => {
    setError(null)
    fetchMeta()
      .then(next => {
        setMeta(next)
        setRecording(next.recorder.enabled)
        // Only when nothing is open, so a refresh of the sidebar does not
        // knock the reader back to the first panel.
        setActive(current => current ?? next.initial ?? null)
      })
      .catch((problem: Error) => setError(problem.message))
  }, [])

  useEffect(load, [load])

  // `vise serve --reload` restarts the process on every save, so the first
  // fetch after a save fails. Retrying quietly is the difference between a
  // dashboard that survives development and one that has to be reloaded by
  // hand every time a file is touched.
  useEffect(() => {
    if (!error) return
    const timer = window.setTimeout(load, RETRY_MS)
    return () => window.clearTimeout(timer)
  }, [error, load])

  const panels = useMemo<PanelSummary[]>(
    () => (meta ? meta.groups.flatMap(group => group.panels) : []),
    [meta],
  )

  const panel = useMemo(
    () => panels.find(candidate => candidate.id === active) ?? panels[0] ?? null,
    [panels, active],
  )

  const live = useLivePanel(panel?.id ?? null, recording)

  // A panel can stop existing while it is open — a queue backend goes away, and
  // the Queues panel goes with it. Moving to the first remaining panel is
  // better than leaving the reader looking at something that is no longer there.
  useEffect(() => {
    if (live.gone) {
      setActive(null)
      load()
    }
  }, [live.gone, load])

  // The title says which application this is, because two dashboards in two
  // tabs are otherwise identical.
  useEffect(() => {
    if (meta) document.title = `${meta.dashboard.title} — ${meta.app.name}`
  }, [meta])

  const toggle = useCallback(() => {
    const next = !recording
    setRecording(next)
    performAction(next ? 'resume' : 'pause').catch(() => setRecording(!next))
  }, [recording])

  if (error && !meta) return <Failed error={error} onRetry={load} />
  if (!meta) return <Loading />

  return (
    <div className="shell">
      <Chrome
        meta={meta}
        recording={recording}
        connected={live.connected}
        onToggle={toggle}
      />

      {panel === null ? (
        <NoPanels missing={meta.missing} />
      ) : (
        <div className="body">
          <Sidebar
            groups={meta.groups}
            active={panel.id}
            onSelect={setActive}
            title={meta.dashboard.title}
          />
          <Panel panel={panel} rendered={live.rendered} live={recording && live.connected} />
        </div>
      )}
    </div>
  )
}
