create materialized view benchmark_hints.broxadasinistra_publication as
SELECT pub.id,
       pub.slug,
       pub.url,
       pub.type,
       pub.description,
       pub.profile_id,
       pub.visited_at,
       pub.published_at,
       pub.num_likes,
       pub.num_comments,
       pub.description_lexemes
FROM publication pub
         LEFT JOIN profile pr1 ON pub.profile_id = pr1.id
         LEFT JOIN publication_collab pc ON pub.id = pc.publication_id
         LEFT JOIN profile pr2 ON pc.profile_id = pr2.id
WHERE pr1.username = 'broxadasinistra'::text
   OR pr2.username = 'broxadasinistra'::text;

alter materialized view benchmark_hints.broxadasinistra_publication owner to dowser;

