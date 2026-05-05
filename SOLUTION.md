# Stage 4B — Solution

## 1. Query Performance Optimization

### Approach

Three optimizations applied to the read path:

1. **Database indexes** on filtered columns (`gender`, `age`, `country_id`, `age_group`, `created_at`) plus a composite index on `(gender, country_id, age)` for the most common combined filter pattern.
2. **Redis caching** with deterministic keys built from normalized query parameters. TTL of 5 minutes balances freshness with cache hit rate.
3. **Connection pool tuning** (`pool_size=20`, `max_overflow=10`, `pool_pre_ping=True`, `pool_recycle=3600`) to handle bursts of concurrent requests without per-request connection overhead.

### Trade-offs

- Indexes slow down writes slightly. Acceptable for this workload — reads dominate, and write volume is low even during batch ingestion.
- Cache may serve data up to 5 minutes stale. Acceptable for an analytics API where data ingestion is not real-time.
- Cache invalidation on writes is broad — every successful CSV upload clears all cached query results. Fine-grained invalidation would be more efficient but adds complexity that isn't justified at this scale.

### Measurements

Server-side timing was instrumented with print statements at each phase of the request lifecycle. The measurements below come from that instrumentation, not from external clients (which include network and serialization overhead unrelated to the optimizations).

Test query: `GET /api/profiles?limit=5` against the production database (Railway PostgreSQL).

| Phase | Cache miss | Cache hit |
|---|---|---|
| Param normalization | 0ms | 0ms |
| Cache lookup | 19ms | 1ms |
| Database query (indexed) | 11ms | — |
| Cache write | 4ms | — |
| **Total** | **~43ms** | **~6ms** |

A cache hit returns roughly 7x faster than a cache miss. Database query time itself is dominated by network latency to Railway Postgres (the indexed query is fast — most of the 11ms is round-trip cost).

**Note on pre-index baseline:** A direct "before indexes" measurement was not captured during this stage since indexes were applied first, before any timing instrumentation. Index presence and use were verified by listing them via `pg_indexes` and confirming filter columns are covered. The performance ceiling without indexes — full table scans on tens of millions of rows — would be orders of magnitude slower than the 11ms observed with indexes in place.

---

## 2. Query Normalization

### Approach

A `normalize_filters` function converts any filter dictionary into a canonical form before it's used as a cache key or passed to the database query builder:

- Whitelisted keys only (silently drops unknown parameters)
- Empty and `None` values stripped
- String values lowercased where case is irrelevant (`gender`, `age_group`, `sort_by`, `order`)
- Country codes uppercased to ISO standard form (`country_id`)
- Numeric strings cast to numbers (`min_age`, `max_age`, `page`, `limit`, probability values)
- Keys sorted alphabetically before serialization

The cache key is the MD5 hash of `json.dumps(normalized, sort_keys=True)`.

### Why this works

Two requests expressing the same query intent in different surface forms produce the same normalized dictionary, the same JSON serialization, and therefore the same cache key.

Verified with this assertion:

```python
a = {"gender": "FEMALE", "country_id": "ng", "min_age": "25"}
b = {"min_age": 25, "country_id": "NG", "gender": "female"}
assert get_cache_key(a) == get_cache_key(b)  # passes
```

Confirmed end-to-end by running `?gender=female&country_id=NG` followed by `?gender=FEMALE&country_id=ng` — the second request hit the cache (`Cache lookup: 1ms, hit: True`).

The approach is fully deterministic. No AI or fuzzy matching involved.

### Trade-offs

- Strict whitelist means adding a new filter parameter requires updating both the route handler and the normalizer. This is intentional — explicit beats implicit when the input shape affects cache correctness.
- Synonym handling (e.g., "women" → "female") happens in the existing rule-based query parser, not in the normalizer. The normalizer assumes its input is already in canonical filter-dict form.

---

## 3. CSV Ingestion

### Approach

The `POST /api/profiles/upload` endpoint streams uploaded CSV files and processes them in chunks. The implementation uses `io.TextIOWrapper` around the raw file handle, so rows are parsed lazily through `csv.DictReader` as the loop iterates — the file is not loaded into memory all at once. The chunking benefit is in the database commits — each chunk of 1000 rows is its own transaction, so partial failures retain already-inserted data without a global rollback.

- Processes rows in chunks of 1000
- Validates each row independently before inclusion in the chunk
- Bulk inserts each valid chunk via `bulk_insert_mappings`
- Commits per chunk, so partial failures retain successfully-inserted data
- Tracks skip reasons in counters returned in the final response
- Invalidates the query cache once at the end of the upload (not per row)

### Validation rules

A row is skipped if:

- Required fields are missing or empty (`name`, `gender`, `age`, `country_id`)
- Age is not a positive integer in the range 0–150
- Gender is not in `{"male", "female"}`
- The name already exists in the database OR appeared earlier in the same upload
- The row is malformed (missing required columns)

A bad row never fails the entire upload — the loop continues, the counter increments, and the response summary reports what was skipped and why.

### Verified behavior

Tested with two fixtures:

**Happy path** (3 valid rows): all 3 inserted, 0 skipped.

**Mixed validity** (8 rows: 3 valid, 2 invalid age, 1 invalid gender, 1 missing name, 1 duplicate name within upload):

```json
{
  "status": "success",
  "total_rows": 8,
  "inserted": 3,
  "skipped": 5,
  "reasons": {
    "duplicate_name": 1,
    "invalid_age": 2,
    "invalid_gender": 1,
    "missing_fields": 1,
    "malformed_row": 0
  }
}
```

**Idempotency** (re-uploading the happy-path file): all 3 rows skipped as duplicates, 0 inserted. Confirms the duplicate-name rule works against existing database state.

**Cache invalidation:** After uploading new female profiles, querying `?gender=female` returned the updated total reflecting the new rows — confirming the cache was cleared on upload.

### Concurrency

- Connection pool tuning (`pool_size=20`, `max_overflow=10`) allows multiple uploads to use distinct connections without exhausting the pool
- Short transactions (commit per 1000-row chunk) prevent any single upload from holding a long-running transaction that would block read queries
- Cache invalidation runs once after all chunks are committed, not per row

### Failure handling

- Single bad row → counter increments, loop continues
- Bulk insert error → fallback path inserts the chunk row-by-row, saving as many as possible
- Partial mid-upload failure → already-committed chunks persist; no global rollback
- Final response always includes the full counter summary, even on partial completion

### Trade-offs and known limitations

- Existing names are loaded into memory once at the start of an upload. Works fine for the current dataset size. For datasets in the 100M+ range, a per-chunk database query or a Bloom filter would be needed.
- Cache invalidation clears all query keys, not just affected ones. Could be made smarter but the added complexity isn't justified at this scale.
- No background task for very large files. The current implementation is synchronous — the HTTP response waits for processing to complete. For files in the multi-million-row range, a background queue (Celery, RQ, or FastAPI's `BackgroundTasks`) would be needed.
- No upload progress tracking. Out of scope for this stage.
