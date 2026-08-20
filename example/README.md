# The example application

```bash
cd example
vise serve
```

Then open <http://127.0.0.1:8000/__sillo/foreman/>.

Four routes, chosen to make different panels do something:

| Route | What it exercises |
| --- | --- |
| `GET /` | Requests, Logs |
| `GET /api/v1/documents/{id}` | Cache — a miss, then a hit on the second call |
| `POST /api/v1/invites` | Mail — suppressed, and listed rather than discarded |
| `GET /api/v1/insights` | Exceptions — one route that always fails |

There is no database, no queue and no scheduler, so the Queries, Queues, Workers
and Schedules panels are **absent**. That is the behaviour worth seeing:

```bash
vise panels
```

Add a database or a Redis queue to this application and the matching panels
appear — on the next probe, without a restart.
