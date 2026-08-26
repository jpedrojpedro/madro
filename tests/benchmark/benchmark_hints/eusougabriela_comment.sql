create materialized view benchmark_hints.eusougabriela_comment as
WITH filtered_comments AS (SELECT c.id,
                                  c.profile_id,
                                  c.publication_id,
                                  c.annotation,
                                  c.num_likes,
                                  c.visited_at,
                                  c.reply_to,
                                  c.published_at
                           FROM publication pub
                                    LEFT JOIN profile pr1 ON pub.profile_id = pr1.id
                                    LEFT JOIN publication_collab pc ON pub.id = pc.publication_id
                                    LEFT JOIN profile pr2 ON pc.profile_id = pr2.id
                                    LEFT JOIN comment c ON pub.id = c.publication_id
                                    LEFT JOIN profile pr3 ON c.profile_id = pr3.id
                           WHERE pr1.username = 'eusougabriela'::text
                              OR pr2.username = 'eusougabriela'::text)
SELECT id,
       profile_id,
       publication_id,
       annotation,
       num_likes,
       visited_at,
       reply_to,
       published_at,
       to_tsvector('portuguese'::regconfig, COALESCE(misc.emoji_replace(annotation),
                                                     ''::text)) AS annotation_lexemes
FROM filtered_comments fc;

alter materialized view benchmark_hints.eusougabriela_comment owner to dowser;

