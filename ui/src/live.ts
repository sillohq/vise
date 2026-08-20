/**
 * The live feed.
 *
 * An `EventSource` per open panel. The server sends *rendered panels*, not raw
 * events, so this hook does no computation — it holds the latest snapshot and
 * hands it to React.
 *
 * Two things it has to get right, both about a development server:
 *
 * **It must survive a restart.** `vise serve --reload` kills the process on
 * every save. `EventSource` reconnects by itself, which is most of why it was
 * chosen over a websocket, and the interface shows a disconnected state in the
 * meantime rather than freezing on stale numbers.
 *
 * **It must not leak a connection per panel switch.** The source is closed in
 * the effect's cleanup, which React runs on every dependency change — so
 * clicking through fourteen panels opens and closes fourteen connections rather
 * than opening fourteen.
 */

import { useEffect, useRef, useState } from 'react'

import { fetchPanel, openStream } from './api'
import type { Rendered } from './types'

export interface Live {
  /** The latest snapshot, or null before the first arrives. */
  rendered: Rendered | null
  /** Whether the stream is currently connected. */
  connected: boolean
  /** True when the server said this panel no longer exists. */
  gone: boolean
}

export function useLivePanel(id: string | null, enabled: boolean): Live {
  const [rendered, setRendered] = useState<Rendered | null>(null)
  const [connected, setConnected] = useState(false)
  const [gone, setGone] = useState(false)

  // Held in a ref so the first paint does not wait for the stream's first
  // interval — the fetch below fills it in immediately, and the stream takes
  // over from there.
  const current = useRef<string | null>(null)

  useEffect(() => {
    if (!id) return

    let cancelled = false
    current.current = id
    setGone(false)

    // Snapshot first. The stream sends its first frame one interval in, and a
    // panel that showed nothing for two seconds after every click would feel
    // broken.
    fetchPanel(id)
      .then(panel => {
        if (!cancelled && current.current === id) setRendered(panel)
      })
      .catch(() => {
        // The stream will report the same problem more usefully.
      })

    if (!enabled) {
      setConnected(false)
      return () => {
        cancelled = true
      }
    }

    const source = openStream(id)

    source.addEventListener('open', () => setConnected(true))

    source.addEventListener('panel', event => {
      const payload = JSON.parse((event as MessageEvent).data) as Rendered & {
        panel: string
      }
      if (payload.panel === current.current) {
        setRendered(payload)
        setConnected(true)
      }
    })

    source.addEventListener('gone', () => setGone(true))

    source.onerror = () => {
      // EventSource retries on its own. Reporting the gap is all there is to
      // do, and closing here would prevent the retry.
      setConnected(false)
    }

    return () => {
      cancelled = true
      source.close()
    }
  }, [id, enabled])

  return { rendered, connected, gone }
}
