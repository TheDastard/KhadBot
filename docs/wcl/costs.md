# WarcraftLogs API: measured costs

## Measured cost (2026-10-11)

Recorded with `main.py ingest` (Buffs filtered to the 17 cooldown-registry IDs; enemy Casts unfiltered because
no encounter file existed yet) and `scripts/wcl/spike_wcl_cost.py` (no filters).

| Report | Boss pulls | Points | Wall time | Raw pages | Events | Parquet |
|---|---|---|---|---|---|---|
| `trMgApZCVYkv3jdq` | 25 | 348 | 2 min 40 s | 642 MB | 1.53 M | 20 MB |
| `H7z1fqDXjRTFnbtg` | 16 | 242 | 1 min 50 s | 438 MB | 1.05 M | 13 MB |
| `QkVa6GHRK8yfrTbw` | 21 | 347 | about 3 min | 730 MB | 1.83 M | 22 MB |

- About **14 points per pull** with the default filters, so a full night is a tenth of the hourly budget.
- A request costs about 2 points, plus roughly 1 point per extra page. Unfiltered Buffs on a 526 s pull
  (`trMgApZCVYkv3jdq:26`) took 9 pages, 82.7k events and 10 points; Healing on the same pull took 9 pages,
  89.7k events, 10 points and 37 MB. A one-page request of a small spec costs about 1 point instead: 48 cost 49
  (2026-10-20, `probes.md`, "Cross-fight and heal-only results").
- No data type was truncated in any pull.
- Local processing (parse, normalize, store) of the whole 25-pull night from cached pages: **18.4 s** on the dev
  machine, under the 30 s budget. Peak traced memory was about 400 MB, bounded by the largest pull because
  pages are concatenated per pull.
- Parsing one long pull's pages: stdlib `json` 1.58 s, `orjson` 0.56 s. Normalizing it: 2.3 s.
- Digest per pull (no encounter file yet): 0.1 to 0.7 s and about 850 to 1,250 tokens.

## Raw page compression (2026-10-20)

How well zstd compresses the raw pages that hosted mode stores in object storage (`CompressedBlobStore`, `raw/`
only). Measured on a copy of `raw/H7z1fqDXjRTFnbtg` (the 16-pull night above): 453 objects, 439.3 MB, one page per
object as WCL returned it, compressed and decompressed one object at a time in memory with `zstandard` 0.25.0
(single thread, Python 3.14, Windows dev machine). Times are process CPU time, because other jobs were sharing the
machine; read them as relative, not as a benchmark.

| zstd level | Stored | Ratio | Compress | Decompress |
|---|---|---|---|---|
| 1 | 29.2 MB | 15.1x | 375 MB/s | 562 MB/s |
| 3 (zstd default) | 30.6 MB | 14.4x | 253 MB/s | 469 MB/s |
| 6 | 25.5 MB | 17.2x | 68 MB/s | 686 MB/s |
| 9 (KhadBot default) | 22.3 MB | 19.7x | 58 MB/s | 740 MB/s |

- A first run (wall clock, with the machine busy) also timed level 19: 18.0 MB (24.4x), but 1 MB/s to compress, far
  too slow for an ingest.
- By data type at level 3: Healing 328.5 MB to 21.7 MB (15.1x), DamageTaken 73.7 to 5.6 (13.1x), Casts 32.3 to
  2.9 (11.3x); the small types compress 4x to 17x. `report.json` goes from 0.2 MB to 0.03 MB (4.7x).
- Page sizes: median 1.7 kB (the many small types), 90th percentile 4.3 MB, largest 4.7 MB. The largest page takes
  about 16 ms to compress at level 3 and 16 ms to decompress.
- A night of raw pages that is about 440 MB costs about 22 MB at the default level, so storage for raw pages is
  about 5% of the uncompressed size. Compressing the whole night at level 9 takes about 8 s of CPU against a WCL
  fetch of about 2 minutes; reading it all back takes under 1 s.

## Live watch polls

A live report watch (`jobs/watch.py`) sends the report metadata query (`GET_REPORT_META`) every
`KHADBOT_WATCH_POLL_INTERVAL_S` seconds (180 by default), so a 6-hour night is at most 120 polls.

- **Not measured yet.** The budget reserves 1 point per poll (`KHADBOT_WATCH_POLL_POINTS`). The only documented figures
  are that a request costs at least 1 point, a `rateLimitData` read costs 1 and a one-page request of a small events
  spec costs about 1 ("Measured cost" above). The metadata query returns `masterData` actors and abilities, which are
  large for a raid, so it may cost more than 1; a watch's polls are then 120 times that.
- Estimate: 1 to 3 points a poll, so 120 to 360 points over the longest watch (3% to 10% of an hour's 3,600), against
  about 180 points for the ingest of a 25-pull night.
- To measure it (this spends WCL points, about 20: it prints the estimate and waits for a typed `yes`), on a public
  report:

      uv run python scripts/wcl/measure_watch_poll.py --code <report code> --polls 3

  It reads `rateLimitData` twice to find a read's own cost, then before and after each metadata query. Record the report
  code, the date and the per-poll cost here, then set `KHADBOT_WATCH_POLL_POINTS` if it differs. It uses the app token;
  on the current limits a user's token spends the same pool. On the new limits the metadata query is user-eligible
  report data, so a user-token poll would spend the user's own points first.
