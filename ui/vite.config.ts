import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

/**
 * The interface is served by the Python package out of
 * `sillo_vise/dashboard/static`, so the build writes there directly and the
 * output is committed. That is what lets `pip install sillo-vise` need no node.
 *
 * `base` is relative rather than absolute because the dashboard's mount point is
 * configurable — `[dashboard] path` defaults to `/__sillo/foreman` and a project
 * can move it. An absolute base would bake one path into the bundle and break
 * every other.
 */
export default defineConfig({
  plugins: [react()],
  base: './',
  build: {
    outDir: '../sillo_vise/dashboard/static',
    emptyOutDir: true,
    // One JS file and one CSS file. A dashboard is not big enough for code
    // splitting to pay for itself, and a single hashed pair is simpler for the
    // Python side to serve and cache.
    rollupOptions: {
      output: {
        manualChunks: undefined,
      },
    },
  },
})
