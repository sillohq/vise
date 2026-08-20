# vise

**The Sillo development server, with Foreman built in.**

```bash
pip install sillo-vise
vise serve
```

One command runs the project's application, replaces uvicorn's logging with
something a person can read at a glance, and mounts the Foreman operations
dashboard beside it.

```
  ▲ vise 0.1.0                        sillo 0.2.1 · python 3.12.13

  ➜  Local      http://127.0.0.1:8000
  ➜  Foreman    http://127.0.0.1:8000/__sillo/foreman
  ➜  App        app.main:app
  ➜  Panels     9 live · 5 waiting on what they watch

  reload on · recorder on · .vise

  09:14:22  GET    /api/v1/documents             200     38ms   12.4 kB
  09:14:22  POST   /api/v1/documents             201    204ms      840 B
  09:14:23  GET    /api/v1/insights              503    2.10s       84 B  slow
```

---

## What it shows

Fourteen panels, in four groups, over hooks the framework already has.

| Group | Panels |
| --- | --- |
| Monitor | Overview, Requests, Queries, Cache, Outgoing |
| Work | Queues, Workers, Schedules |
| Diagnose | Exceptions, Logs, Real-time, Mail |
| Tools | Routes, Config |

**A panel appears only when it can observe something.** An application with no
Redis has no Queues panel — not a Queues panel showing zeroes, and certainly not
one showing sample data. `vise panels` says which are live and why the rest are
not:

```
  panel       group     state
  overview    Monitor   live
  queries     Monitor   no database — sillo.record is not set up
  queues      Work      no queue backend — sillo.work is not set up
  logs        Diagnose  live
  routes      Tools     live
```

## Why the logging is different

uvicorn writes this:

```
INFO:     127.0.0.1:54118 - "GET /api/v1/documents HTTP/1.1" 200 OK
```

Everything on that line is true and almost none of it is what somebody watching
a development server wants. The level is always INFO, the protocol is always
HTTP/1.1, the reason phrase restates the code, and the two facts that matter —
how long it took and how much came back — are not there at all, because
uvicorn's access logger runs inside its protocol implementation where neither is
known.

Vise writes the access line **from the recorder's own measurement** instead. One
thing measures a request and the log reads it, so the log and the dashboard can
never disagree, and turning the recorder off turns the access log off with it.

uvicorn's logging is switched off rather than reconfigured — `log_config=None`,
`access_log=False`, and its loggers silenced explicitly. Passing a custom
`log_config` would leave uvicorn owning the configuration.

Three styles: `vise` (aligned and coloured), `plain` (aligned, no colour, for a
file or CI) and `json` (one object per line for a collector).

## Configuration

A `.vise` file in the project root. `vise init` writes one with every setting
present, commented out, at its default.

```toml
[app]
target = "app.main:app"

[server]
host = "127.0.0.1"
port = 8000
reload = true

[dashboard]
path = "/__sillo/foreman"
access = "local"          # local | token | open

[recorder]
buffer = 2000
slow_request_ms = 500
redact = ["x-tenant-key"]

[logs]
style = "vise"
level = "info"
```

Precedence, lowest to highest: built-in defaults, `.vise`, `VISE_*` environment
variables, command-line flags. Only flags actually typed override the file.

## Commands

| Command | What it does |
| --- | --- |
| `vise serve` | Run the application with Foreman alongside it |
| `vise init` | Write a starter `.vise` |
| `vise doctor` | Report what vise can observe here, and what it cannot |
| `vise panels` | List the panels, and why any are missing |
| `vise routes` | The route table, with what guards each route |
| `vise version` | What is installed, and what each optional piece would add |

`vise doctor` exits non-zero when any declared panel is missing, so it is usable
in a check without parsing its output.

---

## The four rules it ships with

These are from the Foreman specification, and each is expensive to retrofit and
cheap to design in.

**1. Redaction happens on capture, never on read.** A watcher that stores a
`Cookie` header and hides it in the interface is a credential store with a filter
on top. The redactor runs inside the recorder's `emit()`, before an event reaches
the store, and the store has no un-redacted path at all. The tests prove this by
asserting the secret is not *in the store* — never that the API hides it.

**2. Disabled means compiled out.** `recorder.enabled = false` does not leave a
branch on the hot path. Nothing is constructed, no middleware is wrapped around
the application, and no watcher is attached. Off costs one boolean at boot.

**3. The cost is a published number.** See [Overhead](#overhead).

**4. One recorder, many watchers.** Fourteen panels with fourteen collection
paths would be fourteen storage decisions and fourteen ways to leak. There is one
`Recorder`, one bounded `Store`, and one watcher per concern.

## How it is put together

```
sillo_vise/
  config/      .vise discovery, TOML parse, environment overlay
  recorder/    Event types, ring store, time series, redaction
  watchers/    One per concern; each proves it can observe before it attaches
  panels/      Fourteen panel builders over the store
  dashboard/   Raw ASGI middleware: assets, JSON API, SSE stream, access gate
  server/      uvicorn runner, reload factory, installation
  logs/        Banner, aligned formatter, access line
  cli/         serve, init, doctor, panels, routes, version
```

Two ordering decisions carry most of the weight, both because sillo builds its
middleware chain inside-out — the *last* registered runs *first*:

- The **request recorder** is registered first, so it runs innermost, closest to
  the application. What it measures is the application's own time.
- The **dashboard** is registered last, so it runs outermost. Its own requests
  are answered before the recorder is reached, which is why the dashboard cannot
  appear in its own charts.

The dashboard is a middleware rather than a mounted router on purpose: a mounted
router in sillo claims its whole prefix subtree and can shadow routes registered
later during startup. An observability tool must not do that to the thing it is
observing.

### Memory is bounded by configuration, not by traffic

A ring per event kind capped at `recorder.buffer`, plus a per-minute time series
capped at `recorder.window_minutes`. Percentiles come from a fixed reservoir per
bucket, so they are *estimates* — said plainly here and in the interface, because
a p95 presented as exact when it is sampled is worse than one presented as
sampled.

### Access

`access = "local"` is the default and admits only loopback peers, read from the
transport's peer address and never from `X-Forwarded-For`. A header is a claim
made by whoever is talking to you, and this is the check standing between a
stranger and every request the application has served.

`token` compares a shared secret in constant time. `open` has a real use behind
somebody else's authentication and is spelled out rather than reachable by
accident. A refusal never says which mode refused.

## Overhead

Measured with `vise bench`, on the same machine, same application, same
request.

| | p50 | p95 | overhead |
| --- | --- | --- | --- |
| sillo alone | — | — | — |
| vise, recorder off | — | — | — |
| vise, recorder on | — | — | — |

> Numbers are filled in by the benchmark; the row for "recorder off" exists to
> keep rule 2 honest.

## Development

```bash
uv sync --extra dev
uv run pytest

cd ui && bun install && bun run build   # rebuild the dashboard interface
```

The interface is a Vite + React app under `ui/`, built into
`sillo_vise/dashboard/static/` and committed, so `pip install` needs no node.
Changing the interface needs bun; using vise does not.

## Licence

BSD-3-Clause.
