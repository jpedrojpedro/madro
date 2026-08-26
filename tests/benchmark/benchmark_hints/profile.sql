create materialized view benchmark_hints.profile as
SELECT pr.id,
       pr.username,
       pr.full_name,
       pr.is_private,
       pr.num_medias,
       pr.num_followers,
       pr.num_following,
       pr.biography,
       pr.is_verified,
       CASE
           WHEN pri.id IS NOT NULL THEN 't'::text
           ELSE 'f'::text
           END                AS is_influencer,
       CASE
           WHEN prr.id IS NOT NULL THEN 't'::text
           ELSE 'f'::text
           END                AS is_restaurant,
       prr.primary_category   AS restaurant_primary_category,
       prr.secondary_category AS restaurant_secondary_category,
       pr.biography_lexemes
FROM profile pr
         LEFT JOIN benchmark_hints.profile_restaurant prr ON pr.id = prr.id
         LEFT JOIN benchmark_hints.profile_influencer pri ON pr.id = pri.id;

alter materialized view benchmark_hints.profile owner to dowser;

