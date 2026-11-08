# Interactive saving and navigation

The site previously opened a PostgreSQL connection and checked four tables on
every database operation. Saving an edit then requested missing data separately;
switching editor tabs fetched it again. Bulk export repeated both requests for
each order before starting PDF generation.

Connections now use a bounded PostgreSQL pool (up to four, minimum zero, idle
expiry 60 seconds), with schema initialization once per database in the process.
Connections are checked before reuse and transactions commit or roll back before
returning to the pool. No periodic queries keep Neon awake. Settings still come
from the database rather than a process cache, so external changes remain visible.
The pool dependency must be installed from `requirements.txt` when deploying.

State and save responses include missing-data status. The browser coalesces edits,
serializes writes, and batches multiple orders in one save. It applies a response
only to the matching current draft, retains failed saves, flushes pending edits
before reload/export, and warns before leaving with unsaved changes. Tab switches
use the status already loaded. A normal page refresh fetches current server data.

## Reproducible browser comparison

`CHROMIUM_PATH=/usr/bin/chromium python tests/browser_performance.py`

The same test can target an older checkout with `PERF_ROOT` and
`PERF_BASELINE=1`. The comparison used baseline commit `7ee7001`, 30 drafts,
Chromium, an isolated SQLite database, and an artificial 100 ms delay on each API
request. Export job responses were stubbed to exclude PDF generation entirely.

| Interaction | Before | After |
| --- | ---: | ---: |
| Open editor and switch tabs six times: API requests | 7 | 0 |
| Same navigation sequence, including browser actions | 1.29 s | 0.38 s |
| Edit then blur: save + missing-data requests | 4 | 1 |
| Save 30 changed drafts before export: requests | 60 | 1 |
| Batch export preparation, excluding PDF generation | 6.77 s | 0.36 s |

These are controlled local measurements, not measured production response times.
The test also holds a save in flight while making a newer edit, checks the latest
value survives, and verifies recovery after a failed save. Real PostgreSQL tests
cover connection reuse, transaction rollback, external settings updates, and
reconnection after an idle backend is terminated. PDF generation and Render's
initial cold start are not accelerated or benchmarked by these numbers.
