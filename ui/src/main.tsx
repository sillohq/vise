/**
 * The entry point.
 *
 * `StrictMode` is on. It double-invokes effects in development, which is exactly
 * the pressure the live feed needs to be under: an `EventSource` that is opened
 * twice and closed once is a leak, and StrictMode surfaces it on the first
 * render rather than after an afternoon of clicking between panels.
 */

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import { App } from './App'
import './theme.css'
import './app.css'

const mount = document.getElementById('foreman')

if (mount) {
  createRoot(mount).render(
    <StrictMode>
      <App />
    </StrictMode>,
  )
}
