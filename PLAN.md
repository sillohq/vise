# Vise — build plan, and what shipped

> **Status: built.** Every phase below landed. This section records where the
> plan and the finished thing differ, because a plan kept as though nothing was
> learned is a plan nobody trusts twice.
>
> **Changed during the build**
>
> - **Route names are resolved, not read off the scope.** The plan assumed
>   sillo's router put the matched route on the ASGI scope. It does not — it
>   sets `route_params` and dispatches. `introspect.RouteResolver` walks the
>   table once per unique path and caches, with a bound so a 404 flood cannot
>   grow memory.
> - **Three watchers monkeypatch.** Queries, Outgoing and Schedules each wrap a
>   method because the framework offers no hook at the only point where the
>   measurement exists. The plan said "hooks the framework already has"; for
>   these three that was optimistic. Each wrapper is named, reversible and
>   documented in `ARCHITECTURE.md`.
> - **Reload needed a factory.** `--reload` runs the application in a child
>   process that imports it itself, so an instrumented object never reaches it.
>   `server/factory.py` exists entirely for that.
> - **The logging work was larger than "replace the formatter".** uvicorn was
>   the easy half. The framework attaches handlers to its own named loggers and
>   builds more of them lazily, and it reports one unhandled exception from two
>   layers — four full tracebacks for one failure, before any of that was
>   addressed.
> - **`vise bench` shipped as a real command**, not a note. The published
>   numbers are in the README.
> - **Boolean flags are `--reload on|off`.** A console flag always has a value,
>   so a flag defaulting to true would override `.vise` on every run.
>
> **Not built, and why**
>
> - **Request replay and HAR export**, listed on the Foreman page under
>   Requests. Both are write-shaped features against somebody's live
>   application; neither is needed to see what an application is doing.
> - **Queue actions** — retry a job, pause a queue, flush a cache key. The
>   dashboard's only writes act on the recorder itself. A development tool
>   changing application state by accident is a story nobody wants to be in.
> - **EXPLAIN from the interface**, on the Queries panel. Same reason: it runs
>   SQL the person did not write against a live database.

---

# The original plan

**Vise is the development server for Sillo, with Foreman built in.**

One command — `vise serve` — runs the application, replaces uvicorn's logging
with something legible, and mounts the Foreman operations dashboard at
`/__sillo/foreman`. A `.vise` file in the project root configures all of it.

- Distribution: `sillo-vise` · import package: `sillo_vise` · console script: `vise`
- Repository: `sillohq/vise`, sibling of `core/`, `oauth/`, `inertia/`, `start/`
- Built against `sillo-framework` 0.2.1 (`SilloApp`, `sillo.console`, `sillo.work`)

---

## 1. Decisions taken before writing code

| Question | Decision |
| --- | --- |
| Dashboard frontend | **Vite + React**, source in `ui/`, build output committed to `sillo_vise/dashboard/static/`. `pip install` needs no node; changing the UI does. |
| Panel data | **Live only.** A panel is registered only when its collector can actually observe the running app. No sample data ships in the dashboard, ever. |
| Repository | Local git at `vise/`, plus a public `sillohq/vise` on GitHub, pushed as the build proceeds. |
| Config file | `.vise`, TOML syntax. `tomllib` on 3.11+, `tomli` on 3.10. |
| CLI toolkit | `sillo.console` — the framework's own `Console`/`Command`/`Output`, so `vise` and `sillo` look and behave identically. |

## 2. The four constraints the Foreman page ships with

These are specification, not follow-up work. Each is expensive to retrofit.

1. **Redaction happens on capture, never on read.** A watcher that stores a
   `Cookie` header and hides it in the interface is a credential store with a
   filter on top. `redact()` runs inside the recorder's `emit()`, before the
   event reaches the store, and the store has no un-redacted path at all.
2. **Disabled means compiled out.** `recorder.enabled = false` must not leave a
   branch on the hot path. The ASGI middleware is only wrapped around the app
   when the recorder is on; watchers are only attached when their panel is
   live. Off costs one boolean at boot, nothing per request.
3. **The cost is a published number.** `vise bench` reports overhead with the
   recorder on and off, and the README carries both rows.
4. **One recorder, many watchers.** Fourteen panels with fourteen collection
   paths is fourteen storage decisions and fourteen ways to leak. One
   `Recorder`, one `Store`, one watcher per concern.

## 3. Package layout

```
sillo_vise/
  config/      .vise discovery, TOML parse, env overlay, defaults
  recorder/    Event types, ring Store, time series, redact(), Recorder
  watchers/    One per concern; each declares availability and attaches a hook
  panels/      Panel specs + registry; turn store state into tiles/chart/table
  dashboard/   ASGI app: static assets, JSON API, SSE stream, access gate
  server/      uvicorn runner, reload, startup banner
  logs/        The clean formatter, access line, uvicorn log replacement
  cli/         serve, init, doctor, panels, routes, bench, version
ui/            Vite + React source for the dashboard
tests/
```

## 4. The recorder

`Recorder` owns a `Store`. The store is a fixed-size ring per event kind plus a
per-minute time series, so memory is bounded by configuration and not by
traffic. Every event carries `request_id` where one is in scope, which is what
lets the Requests panel show the queries, cache reads, outgoing calls, jobs and
log lines a single request produced.

Event kinds: `request`, `query`, `cache`, `outgoing`, `job`, `schedule`,
`exception`, `log`, `mail`, `ws`, `event`.

Redaction covers headers (`authorization`, `cookie`, `set-cookie`, `x-api-key`,
anything in `recorder.redact`), query-string values on named keys, and SQL
bind parameters when `recorder.redact_bindings` is on.

## 5. Panels and their availability probes

Live only: a panel that cannot observe anything is not registered, and does not
appear in the sidebar.

| Group | Panel | Available when |
| --- | --- | --- |
| Monitor | Overview | always (derived from the recorder) |
| Monitor | Requests | always (recorder middleware) |
| Monitor | Queries | `app.state["record"]` is set up |
| Monitor | Cache | a `BaseCache` is reachable on the app |
| Monitor | Outgoing | `sillo.http.client` is importable and instrumented |
| Work | Queues | a queue backend answers `ping()` / `queue_stats()` |
| Work | Workers | a worker pool or `WorkerStats` is reachable |
| Work | Schedules | `app.state["scheduler"]` is set up |
| Diagnose | Exceptions | always (recorder) |
| Diagnose | Logs | always (log watcher) |
| Diagnose | Real-time | the app registers websocket routes |
| Diagnose | Mail | mail is configured |
| Tools | Routes | always |
| Tools | Config | always |

Probes re-run on an interval, so a panel appears when Redis comes up rather
than requiring a restart.

## 6. `.vise`

```toml
[app]
target = "app.main:app"          # falls back to sillo's own discovery

[server]
host = "127.0.0.1"
port = 8000
reload = true
watch = ["app", "config"]

[dashboard]
enabled = true
path = "/__sillo/foreman"
access = "local"                 # local | token | off

[recorder]
enabled = true
buffer = 2000
window_minutes = 60
slow_request_ms = 500
slow_query_ms = 100
redact = ["authorization", "cookie", "set-cookie", "x-api-key"]

[logs]
style = "vise"                   # vise | plain | json
level = "info"
access = true

[panels]
disable = []
```

Precedence: defaults < `.vise` < `VISE_*` environment < command-line flags.

## 7. Logging

uvicorn's own logging is switched off entirely — `log_config=None`,
`access_log=False` — and replaced. Access lines are emitted by the recorder
middleware, which is the only thing that knows the duration and byte count
anyway.

```
  ▲ vise 0.1.0                        sillo 0.2.1 · python 3.12.13

  ➜  Local      http://127.0.0.1:8000
  ➜  Foreman    http://127.0.0.1:8000/__sillo/foreman
  ➜  App        app.main:app · reload on · 11 panels live

  09:14:22  GET   /api/v1/documents           200    38ms   12.4 kB
  09:14:22  POST  /api/v1/documents           201   204ms      840 B
  09:14:23  GET   /api/v1/insights            503   2.10s       84 B  slow
```

Colour comes from `sillo.console.style.Palette`, so it downgrades correctly in
a pipe, in CI and on a terminal without true colour.

## 8. Dashboard HTTP surface

Mounted under `dashboard.path`, gated by `dashboard.access`.

- `GET  /` — the built index
- `GET  /assets/*` — hashed build output, immutable
- `GET  /api/meta` — app name, versions, groups, live panels
- `GET  /api/panel/{id}` — tiles, chart, table, aside for one panel
- `GET  /api/request/{id}` — one request and everything it caused
- `GET  /api/stream` — SSE: panel snapshots and a heartbeat
- `POST /api/action/{panel}/{action}` — retry a job, pause a queue, forget a
  cache key, resolve an exception; only where the underlying hook is real

## 9. UI

`ui/` is a Vite + React app that reproduces `AppMock.tsx` exactly — the same
theme tokens (`#fc0345` primary, `#050505` bg, the surface/border/muted scale),
the same sidebar, breadcrumb, stat tiles with sparklines, bar chart, table with
`toneFor()` pills and side rail. The difference is that the data arrives from
`/api` over SSE instead of from `products.ts`.

Build with `bun run build`; output is committed so the wheel is self-contained.

## 10. Phases, and what each one commits

1. **Repo** — plan, licence, pyproject, CI, gitignore
2. **Config** — TOML shim, schema, loader, env overlay, precedence tests
3. **Recorder** — events, redaction, ring store, time series
4. **Logs** — formatter, access line, uvicorn replacement, banner
5. **Watchers** — requests, then one per concern with its availability probe
6. **Panels** — registry, then the fourteen panel builders
7. **Dashboard** — ASGI app, API, SSE, access gate, asset serving
8. **UI** — theme, shell, sidebar, tiles, chart, table, aside, live wiring
9. **CLI** — serve, init, doctor, panels, routes, bench, version
10. **Docs and release** — README, CHANGELOG, benchmark rows, tag

## 11. Testing

- `TestClient` against a real `SilloApp` with the dashboard mounted
- Redaction proved by asserting the store never holds the secret, not that the
  API hides it
- Availability proved by building apps with and without each subsystem
- Overhead measured, with the recorder on and off, and published
