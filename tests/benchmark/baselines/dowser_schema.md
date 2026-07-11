# dowser database — `public` schema

Scraped Instagram-style data (via `instaloader`). Six tables, all in the
`public` schema. PostgreSQL 17 with `pgvector`/`uuid-ossp` extensions
installed, though nothing here uses vector columns — this schema is plain
relational + full-text search only.

## `profile`

One row per Instagram profile/account.

| column              | type      | notes |
|---------------------|-----------|-------|
| `id`                | bigint PK | Instagram's numeric profile id. |
| `username`          | text      | unique (`unq_profile_username`). |
| `full_name`         | text      | |
| `is_private`        | boolean   | |
| `num_medias`        | bigint    | post count. |
| `num_followers`     | bigint    | |
| `num_following`     | bigint    | |
| `biography`         | text      | free-text bio. |
| `profile_pic_url`   | text      | |
| `visited_at`        | timestamp | when this profile was last scraped. |
| `is_brand`          | boolean   | |
| `is_verified`       | boolean   | |
| `enriched_at`       | timestamp | timestamp of a downstream enrichment pass over this profile. |
| `biography_lexemes` | tsvector  | auto-populated by trigger from `biography` (`to_tsvector('pt_en', ...)`) — **use this, not `biography`, for full-text search**; GIN-indexed (`profile_biography_lexemes_idx`). |

Full-text search pattern: `WHERE biography_lexemes @@ to_tsquery('english', ...)`,
ranked with `ts_rank(biography_lexemes, query)`.

## `profile_relationship`

Follow graph edges. Composite PK `(origin_profile_id, edge, destination_profile_id)`.

| column                    | type      | notes |
|---------------------------|-----------|-------|
| `origin_profile_id`       | bigint FK → `profile.id` | the follower. |
| `edge`                    | enum `edge_type` | only value today: `'follows'`. |
| `destination_profile_id`  | bigint FK → `profile.id` | the profile being followed. |
| `visited_at`              | timestamp | |

To find who follows a profile: `origin_profile_id` where `destination_profile_id = <target>`.
To find who a profile follows: `destination_profile_id` where `origin_profile_id = <target>`.

## `publication`

One row per post (image, video, or carousel).

| column                | type      | notes |
|------------------------|-----------|-------|
| `id`                  | bigint PK | |
| `slug`                | text      | |
| `url`                 | text      | |
| `type`                | enum `media_type` | `GraphImage`, `GraphSidecar` (carousel), `GraphVideo`. |
| `description`         | text      | the post caption. |
| `profile_id`          | bigint FK → `profile.id` | the primary author/owner of the post. |
| `visited_at`          | timestamp | scrape time. |
| `published_at`        | timestamp | actual post date — use this for date-range questions, not `visited_at`. |
| `num_likes`           | bigint    | |
| `num_comments`        | bigint    | |
| `description_lexemes` | tsvector | auto-populated by trigger from `description` — **use this, not `description`, for full-text search**; GIN-indexed (`publication_description_lexemes_idx`). |

## `publication_collab`

Many-to-many: profiles tagged as collaborators on a publication (in addition
to its primary `profile_id` author). Composite PK
`(publication_id, profile_id)`, both FKs (→ `publication.id`, → `profile.id`).
When resolving "who is this post about," a publication's owner can be either
its own `profile_id` or a collaborator here — check both.

## `comment`

Comments on publications, with reply threading.

| column           | type      | notes |
|-------------------|-----------|-------|
| `id`             | bigint PK | |
| `profile_id`     | bigint FK → `profile.id` | comment author. |
| `publication_id` | bigint FK → `publication.id` | post being commented on. |
| `annotation`     | text, not null | the comment text. |
| `num_likes`      | bigint    | |
| `visited_at`     | timestamp | |
| `reply_to`       | bigint FK → `comment.id`, nullable | null for top-level comments. |
| `published_at`   | timestamp | |

No `tsvector`/full-text index on `annotation` — filter/sort on it with plain
`ILIKE` or just order by recency/likes if a demand needs comments.

## `raw_file`

Binary media attached to a publication (used for images passed through
MADRO's own OCR/captioning — the raw bytes are not text-searchable at all).

| column           | type      | notes |
|-------------------|-----------|-------|
| `id`             | bigserial PK | |
| `name`           | text      | |
| `extension`      | enum `file_extension` | `mp4`, `jpg`, `json`, `txt`. |
| `position`       | integer   | order within a carousel post. |
| `publication_id` | bigint FK → `publication.id` | |
| `data`           | bytea     | the actual file bytes — never useful for a text answer; don't select this. |
| `external_id`    | text, indexed | |

Unique on `(name, publication_id)`.

## What this schema *cannot* answer

`profile.biography`/`publication.description`/`comment.annotation` are the
only free text stored here. There is no OCR text, no image caption, no
visual description of anything in `raw_file.data` — those only exist
downstream, generated by MADRO's own enrichment pipeline (image captioning +
OCR over the raw bytes), and have no equivalent column here. A question that
can only be answered from what's actually *shown in an image* (e.g. a menu
board, a flyer, on-image text) cannot be resolved by a query against this
schema alone.
