# Changelog

## 0.1.0 — unreleased

The first release. `vise serve` runs a Sillo application, replaces uvicorn's
logging, and mounts the Foreman operations dashboard beside it.

### The dashboard

- **Fourteen panels**, in four groups. Monitor: Overview, Requests, Queries,
  Cache, Outgoing. Work: Queues, Workers, Schedules. Diagnose: Exceptions, Logs,
  Real-time, Mail. Tools: Routes, Config.
- **Live only.** A panel is registered when its watcher can observe the running
  application and not otherwise. An application with no Redis has no Queues
  panel — not one showing zeroes, and not one showing sample data. `vise panels`
  says which are live and why the rest are not.
- **Panels reappear without a restart.** Availability is re-probed on an
  interval, so a panel arrives when its backend comes up.
- Served as raw ASGI middleware rather than a mounted router: a mounted router
  in sillo claims its whole prefix subtree and can shadow routes registered
  later during startup, which an observability tool must not do to the thing it
  is observing.
- Live updates over server-sent events, which reconnect by themselves — the
  behaviour a development server that restarts on every save needs.
- Loopback-only by default. Also `token`, compared in constant time, and `open`
  for use behind somebody else's authentication.

### Logging

- uvicorn's logging is switched off rather than reconfigured — `log_config=None`,
  `access_log=False`, and its loggers silenced explicitly.
- One access line per request, **written from the recorder's own measurement**,
  which is why it can carry the duration and the response size that uvicorn's
  access logger never sees.
- The framework's own loggers are consolidated onto the root, and
  `sillo.logging.create_logger` is disarmed for the run. Without both, an
  unhandled exception printed four full tracebacks.
- Identical failures reported by two layers collapse to one, keyed on the
  exception rather than on the string, since the two layers print different
  frames.
- Three styles: `vise` (aligned, coloured), `plain` (aligned, no colour) and
  `json` (one object per line).

### The recorder

- One recorder, one bounded store, one watcher per concern. Memory is bounded by
  configuration rather than by traffic.
- **Redaction happens on capture.** Headers, query-string values, URL userinfo,
  free-text credentials, and SQL bindings behind a flag. The store has no
  un-redacted path, and the tests assert the secret is not *in the store* rather
  than that the API hides it.
- Every event carries the request that caused it, so "the queries, cache reads,
  outgoing calls, jobs and log lines one request produced" is a filter.
- **Disabled means compiled out.** `[recorder] enabled = false` constructs
  nothing and wraps nothing; `vise bench` measures the difference at +0.1µs.

### Command line

`serve`, `init`, `doctor`, `panels`, `routes`, `bench`, `version` — built on
`sillo.console`, so `vise` and `sillo` look like the same tool.

- `vise doctor` exits non-zero when any declared panel is missing, so it is
  usable in a check without parsing its output.
- `vise init` refuses to overwrite an existing `.vise`.
- A flag left untyped never overrides the file. The boolean settings are spelled
  `--reload on|off` because a console flag always has a value, and a flag
  defaulting to true would discard a project's configuration on every run.

### Known limitations

- **Workers out of process are only partly visible.** When the worker pool runs
  elsewhere, only what the shared queue backend reports can be shown, and the
  Workers panel says so rather than reporting a worker count of zero.
- **Percentiles are estimates.** They come from a fixed reservoir per minute, so
  memory stays flat under load. The interface says so; a p95 presented as exact
  when it is sampled is worse than one presented as sampled.
- **Three watchers monkeypatch.** Queries wraps Tortoise's connection classes,
  Outgoing wraps `HTTPClient._send`, and Schedules wraps the scheduler's execute
  step, because none of the three offers a hook. Each is confined to named
  methods and each is reversible. When the private name Outgoing depends on
  changes, its probe stops finding it and the panel disappears with a reason,
  which is the failure mode to want.
- **A queue middleware cannot be removed.** sillo's backends have no interface
  for it, so the per-job hook stays registered until the process ends.
