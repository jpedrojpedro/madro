# `dowser` database — `benchmark_hints` schema

Materialized views inside the `dowser` Postgres instance, pre-calculating
topic/category/account filtering that `public` alone can't answer cheaply.
DDL lives alongside this file, one `.sql` per view. Read-only — never
written to by the benchmark; queried only by Ground Truth generation, and
only for the specific view names listed in a question's `hint`.

## `benchmark_hints.profile`

Every column from `public.profile` (see `dowser_schema.md`), plus
pre-calculated topic/geo flags flattened onto the same row:

| column                          | type     | notes |
|----------------------------------|----------|-------|
| `is_influencer`                  | text     | `'t'` or `'f'` — **not boolean**, compare with `= 't'`/`= 'f'`. `'t'` when `num_followers >= 50000`. |
| `is_restaurant`                  | text     | `'t'` or `'f'` — **not boolean**, compare with `= 't'`/`= 'f'`. `'t'` when the biography matches a food/restaurant keyword or `profile_analysis` tags the account as a food/restaurant commercial segment. |
| `restaurant_primary_category`    | text     | from `profile_analysis.commercial_subsegment`; `NULL` when `is_restaurant = 'f'` or no analysis row exists. |
| `restaurant_secondary_category`  | text     | from `profile_analysis.topic_subniche`; `NULL` under the same conditions as above. |

Full-text search: still `biography_lexemes @@ to_tsquery(...)`, same as
`public.profile`.

City is **not** a column on this view — see `benchmark_hints.profile_location`
below and `JOIN` to it for any location-scoped question.

## `benchmark_hints.profile_location`

A profile's matched cities, one row per `(id, city)` match — not a 1:1
extension of `profile`. A profile can have zero rows here (no city matched),
one, or several (e.g. a chain present in more than one city): treat this as
a normal many-to-many relation and `JOIN` to it, rather than assuming a
single scalar location per profile.

| column     | type | notes |
|------------|------|-------|
| `id`       | int  | `profile.id` — join key, not unique on this table. |
| `username` | text | `profile.username`, denormalized for convenience. |
| `location` | text | one of a fixed set of known cities (`Balneário Camboriú`, `Rio de Janeiro`, `São Paulo`, `Florianópolis`, `Curitiba`), matched from the biography or a separate `profile_analysis` table. |

To filter `benchmark_hints.profile` by city:
`... FROM benchmark_hints.profile p JOIN benchmark_hints.profile_location pl ON p.id = pl.id WHERE pl.location = '<city>'`.
Don't `LEFT JOIN` this onto `profile` and then filter/aggregate over the
combined row set for anything that isn't itself city-scoped — a multi-city
profile's non-location columns would be double-counted.

## `benchmark_hints.<account>_publication`

One materialized view per tracked account (`haight_clothing`,
`fluminensefc`, `broxadasinistra`, `eusougabriela`) — same shape as
`public.publication`, pre-filtered to that account's own posts plus any post
where it's a `publication_collab` collaborator. Column set and full-text
search pattern (`description_lexemes @@ to_tsquery(...)`) are identical to
`public.publication`.

## `benchmark_hints.<account>_comment`

One materialized view per tracked account (same four as above) — comments on
that account's publications (via the same primary-author-or-collaborator
rule), with one addition over `public.comment`:

| column               | type     | notes |
|-----------------------|----------|-------|
| `annotation_lexemes`  | tsvector | `to_tsvector('portuguese', emoji_replace(annotation))` — **`public.comment` has no full-text index on `annotation` at all; this view does.** Search with `annotation_lexemes @@ to_tsquery('portuguese', ...)`, ranked with `ts_rank(annotation_lexemes, query)`. |

All other columns (`id`, `profile_id`, `publication_id`, `annotation`,
`num_likes`, `visited_at`, `reply_to`, `published_at`) match `public.comment`.
