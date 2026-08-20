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

import { fetchDetail, fetchMeta, mountPath, performAction } from './api'
import { Detail } from './components/Detail'
import { Panel } from './components/Panel'
import { Sidebar } from './components/Sidebar'
import { Failed, Loading, NoPanels } from './components/States'
import { useLivePanel } from './live'
import type { Detail as DetailData, Meta, PanelSummary } from './types'

/** How long to wait before retrying after the server goes away. */
const RETRY_MS = 2000

/**
 * The panel named in the current URL, if any.
 *
 * The dashboard serves its index for any unknown path under the mount, so
 * `/__sillo/foreman/queries` reaches the interface and this is what turns it
 * into an open panel. That is what makes a link to a panel a real link.
 */
function panelFromUrl(): string | null {
  const rest = window.location.pathname
    .slice(mountPath().length)
    .replace(/^\/+|\/+$/g, '')

  return rest || null
}

/** What the drawer is showing, if anything. */
interface Opened {
  kind: string
  id: string
}

export function App() {
  const [meta, setMeta] = useState<Meta | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [active, setActive] = useState<string | null>(panelFromUrl)
  const [recording, setRecording] = useState(true)

  const [opened, setOpened] = useState<Opened | null>(null)
  const [detail, setDetail] = useState<DetailData | null>(null)
  const [detailError, setDetailError] = useState('')

  const load = useCallback(() => {
    setError(null)
    fetchMeta()
      .then(next => {
        setMeta(next)
        setRecording(next.recorder.enabled)
        // Only when nothing is open, so refreshing the sidebar does not knock
        // the reader back to the first panel.
        setActive(current => current ?? next.initial ?? null)
      })
      .catch((problem: Error) => setError(problem.message))
  }, [])

  useEffect(load, [load])

  // `vise serve --reload` restarts the process on every save, so the first
  // fetch after a save fails. Retrying quietly is the difference between a
  // dashboard that survives development and one that has to be reloaded by hand
  // every time a file is touched.
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

  // The live feed pauses while a drawer is open. A table redrawing underneath
  // somebody reading one of its rows is disorienting, and the row they clicked
  // may not be in the next snapshot at all.
  const live = useLivePanel(panel?.id ?? null, recording && opened === null)

  // Keep the address bar on the open panel, so it can be linked and so the back
  // button walks back through the panels somebody actually looked at.
  useEffect(() => {
    if (!panel) return

    const target = `${mountPath()}/${panel.id}`
    if (window.location.pathname !== target) {
      window.history.pushState({ panel: panel.id }, '', target)
    }
  }, [panel])

  useEffect(() => {
    const onPop = () => setActive(panelFromUrl())
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  // A panel can stop existing while it is open — a queue backend goes away, and
  // the Queues panel goes with it. Moving to the first remaining panel is better
  // than leaving the reader looking at something that is no longer there.
  useEffect(() => {
    if (live.gone) {
      setActive(null)
      load()
    }
  }, [live.gone, load])

  // The title says which application this is, because two dashboards in two tabs
  // are otherwise identical.
  useEffect(() => {
    if (meta) document.title = `${meta.dashboard.title} — ${meta.app.name}`
  }, [meta])

  useEffect(() => {
    if (!opened) {
      setDetail(null)
      setDetailError('')
      return
    }

    let cancelled = false
    setDetail(null)
    setDetailError('')

    fetchDetail(opened.kind, opened.id)
      .then(found => {
        if (!cancelled) setDetail(found)
      })
      .catch((problem: Error) => {
        if (!cancelled) setDetailError(problem.message)
      })

    return () => {
      cancelled = true
    }
  }, [opened])

  const open = useCallback((kind: string, id: string) => setOpened({ kind, id }), [])
  const close = useCallback(() => setOpened(null), [])

  const toggle = useCallback(() => {
    const next = !recording
    setRecording(next)
    performAction(next ? 'resume' : 'pause').catch(() => setRecording(!next))
  }, [recording])

  if (error && !meta) return <Failed error={error} onRetry={load} />
  if (!meta) return <Loading />
  if (panel === null) return <NoPanels missing={meta.missing} />

  return (
    <div className="shell">
      <Sidebar groups={meta.groups} active={panel.id} onSelect={setActive} meta={meta} />

      <Panel
        panel={panel}
        rendered={live.rendered}
        live={recording && live.connected}
        recording={recording}
        onToggle={toggle}
        onOpen={open}
      />

      {opened ? (
        <Detail
          detail={detail}
          loading={detail === null && !detailError}
          error={detailError}
          onClose={close}
          onOpen={open}
        />
      ) : null}
    </div>
  )
}
