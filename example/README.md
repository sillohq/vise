# Sillodraft — the example application

Everything vise can watch, switched on, in one process with nothing to install
and nothing to start.

```bash
cd example
vise serve
```

Open <http://127.0.0.1:8000/__sillo/foreman/>. Then, in another terminal, give
it something to show:

```bash
python traffic.py
```

`traffic.py` walks the application the way a person would but faster and without
stopping — reads, writes, searches, queues exports that fail, opens websockets,
and asks for the route that raises. Ctrl-C stops it.

## What makes each panel live

| Panel | What it is watching here |
| --- | --- |
| Overview, Requests | twelve routes, one deliberately slow |
| Queries | Record over SQLite, with a deliberate N+1 in `/api/v1/search` |
| Cache | a memory cache on the document render path |
| Outgoing | an HTTP client calling this application's own `/api/v1/health` |
| Queues | four job classes on four queues |
| Workers | three in-process workers, one per queue group |
| Schedules | `analytics.rollup` every 30s, `notifications.digest` on cron |
| Exceptions | `/api/v1/insights` raises, and `BuildExport` always fails |
| Logs | every route logs |
| Real-time | a websocket route and two registered events |
| Mail | a suppressed mailer, sent both directly and through the queue |
| Routes, Config | the application itself |

`vise panels` confirms it:

```
  14 live, 0 waiting on what they watch
```

## Things worth clicking

- **A request row.** Its headers both ways, the body it sent, the body that came
  back, and the queries, cache reads, log lines and jobs it caused — each of
  those clickable in turn.
- **`GET /api/v1/search`.** The Queries panel flags it as an N+1 and names the
  statement: one query for the list and one per row, because the route fetches
  each document's workspace separately instead of joining.
- **A failed export.** `BuildExport` raises every time, on purpose. A queue
  dashboard that has only seen jobs succeed has never shown anybody the thing
  they actually open it for.
- **The `exports` queue** in the side rail, which reads *stalled* — accurately,
  since nothing on it has ever finished.

## What it needs

Beyond `sillo-vise` itself:

```bash
pip install 'sillo-framework[record]' httpx wsproto
```

- **tortoise-orm** (`[record]`) for the database, and so the Queries panel exists
- **httpx** for `sillo.http.client`, and so the Outgoing panel exists
- **wsproto** so uvicorn can accept websocket connections at all — without it
  the route is in the table and the handshake never completes

The database is a SQLite file, `sillodraft.db`, created and seeded on first
start. Delete it to start over.

## Two framework traps this example works around

Both cost an afternoon and neither announces itself.

**`Job.queue` does not route.** `Dispatchable.dispatch` reads `_queue_name`;
`queue` is the attribute `Job` documents. Setting only `queue` puts every job on
`default` however many queues you declared, which looks like a working queue
system until you notice one worker doing all the work. The jobs here set both.

**`HTTPClient()` cannot be constructed without a base URL.** sillo 0.2.1 renders
an empty one as `None` on the way to httpx — `base_url=self._config.base_url or
None` — and httpx refuses it. The upstream route passes its own base URL.
