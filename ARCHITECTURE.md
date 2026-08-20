# How vise is put together

Notes for somebody changing it. The README says what vise does; this says why it
is shaped the way it is, and which of those shapes are load-bearing.

---

## The one-sentence version

One recorder, many watchers, fourteen panels reading one store, served by a raw
ASGI middleware wrapped around an application vise does not own.

```
             ┌─────────── vise serve ───────────┐
             │                                   │
  request ──▶│  Dashboard  (outermost)           │
             │      │  answers under its prefix  │
             │      ▼                            │
             │  RequestRecorder  (innermost)     │──▶ application
             │      │  opens the request scope   │
             └──────┼────────────────────────────┘
                    │
        watchers ───┴──▶  Recorder ──▶ Store ──▶ Panels ──▶ /api
        (queries, cache,   redacts     bounded    render
         outgoing, queue,  on capture  rings +
         schedule, log,                series
         exception, mail,
         realtime)
```

## The decisions that are load-bearing

### Middleware order

sillo builds its chain inside-out: **the last registered runs first**. So
`install()` registers the request recorder *first*, putting it innermost, next
to the application — what it measures is the application's own time. The
dashboard is registered *last*, putting it outermost, so its own requests are
answered before the recorder is ever reached and it cannot appear in its own
charts.

Getting this backwards produces a dashboard that records itself and an
application whose requests are missing from their own log. Both look almost
right.

### The dashboard is middleware, not a mounted router

A mounted router in sillo claims its whole prefix subtree and can shadow routes
registered later during startup — the admin panel registers its own during
startup. An observability tool must not do that to the application it is
observing. As middleware it does one comparison and delegates.

### Redaction is on capture

`Recorder.emit()` redacts and then stores. There is no second pass on read, and
that is the design rather than an omission: two places that could redact are two
places that could forget, and the second one existing makes the first look
optional.

This is why the redaction tests read `recorder.store` and never the HTTP API. A
test that checks the interface hides a credential passes just as happily when
the credential is sitting in memory behind a filter.

### Correlation goes through a context variable

A query watcher inside the ORM has no argument telling it which HTTP request
caused the statement. A `ContextVar` does, asyncio copies the context into child
tasks, and the alternative was threading a request id through every framework
hook — which would mean changing the framework. Watchers are supposed to be
watchers.

The store's correlation index is keyed **independently of the request ring**,
because the request event is stored last: its duration is not known until it
finishes, so the queries it caused arrive before it does. Indexing against
requests already in the ring correlates nothing at all. That was the first
version, and the smoke test caught it.

### Live only

A panel names the watcher it needs; `PanelRegistry.is_live` asks whether that
watcher is collecting. A panel that is not live is not in the sidebar, not
reachable by URL and **not built** — so a project without Redis cannot produce a
traceback from the Queues panel, because there is nothing to traceback from.

Probes must not raise. `Watcher.safe_probe` turns any exception into an
unavailability, and the exception becomes the reason the panel is missing, which
is both honest and the fastest way to find out that a probe is wrong.

### Reload hands over a factory

`--reload` runs the application in a child process that imports it itself, so an
object instrumented in the parent never reaches it. With reload on, uvicorn is
given `sillo_vise.server.factory:create` instead: the child calls it, and it
imports, instruments and returns. Reattachment is structural rather than
something to remember. The target and configuration travel in the environment,
which is the one channel a re-executed process inherits.

## Where the monkeypatching is, and why

Three watchers replace methods. Each is confined, reversible, and installed only
because the alternative is no panel at all.

| Watcher | What it wraps | Why not a hook |
| --- | --- | --- |
| Queries | `execute_query`, `execute_query_dict`, `execute_insert` on the classes of live Tortoise connections | Tortoise's `db_client` log line carries the query and no duration, because it is written *before* the statement runs |
| Outgoing | `HTTPClient._send` | Client middleware is per instance and a project builds clients wherever it likes. `_send` is private and is the only seam that returns the response — so the only one that can see a status code |
| Schedules | `SchedulerManager._execute` | The manager knows a job's next fire, not how its last run went |

Each wrapper marks itself `__vise_wrapped__` so a second attach — after a reload
— recognises a method it already wrapped rather than wrapping the wrapper.

The Queries watcher patches from `client.__dict__` rather than through
`getattr`, so a subclass that does not override a method does not get the base's
wrapped onto it as well, which would record one statement twice.

## Things that look like bugs and are not

- **`Series.rate()` divides by elapsed time, not by bucket count.** Dividing by
  buckets makes every reading dip at the top of a minute; dropping the current
  bucket makes a server that has just served its first request report zero for
  up to sixty seconds.
- **`Series.buckets()` is anchored to now, not to the last observation.** A
  series that went quiet five minutes ago has to render as five empty minutes on
  the right; anchoring to the newest bucket would draw old traffic at the live
  edge and make a stalled queue look busy.
- **A tile's baseline is the *preceding* window, not a wider one.** A wider
  window contains the recent half and is dominated by whichever is worse, so a
  latency that had just tripled read as no change at all.
- **A tone says whether a number is good, not which way it moved.** Latency
  falling is green; queue size rising is amber; throughput is the other way
  round. `trend_tile` takes `higher_is_better` as a required argument rather
  than guessing.
- **`_is_hashed` looks at the last eight characters, not everything after the
  last hyphen.** Vite's hashes are base64url and can contain a hyphen —
  `index-BLYE-R0z.js` split on its last one leaves `R0z`.
- **The empty path remainder is not defaulted to `/`.** The bare prefix has to
  redirect to the trailing slash, because the built index references its assets
  relatively; defaulting made that branch unreachable.

## The interface

`ui/` is a Vite + React app built into `sillo_vise/dashboard/static` and
committed, which is what lets `pip install sillo-vise` need no node.

It is a **renderer**. Which panels exist, what each shows and what every number
means were decided on the Python side. Two implementations of "what is the p95"
would eventually disagree, and the one in the browser would be the one nobody
tested. The server sends rendered panels over SSE; the browser draws them.

The CI job `interface` rebuilds and fails when the committed output differs from
the source, because a committed build is only useful while it matches.

## Testing

Everything that can be driven through a real `SilloApp` and the framework's own
`TestClient` is. The bugs worth catching are the ones where sillo does not do
what vise assumed — a route table empty until startup finishes, a scope key that
is not there, middleware ordering — and a mock ASGI application would agree with
every assumption and prove none of them.

Availability is proved by building applications *with and without* each
subsystem, which is the only way to check that a probe answers the question the
panel asks rather than a slightly different one.
