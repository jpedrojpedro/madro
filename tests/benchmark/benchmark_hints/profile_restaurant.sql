create materialized view benchmark_hints.profile_restaurant as
SELECT id,
       username,
       (array_remove(array_agg(primary_category), NULL::text))[1]   AS primary_category,
       (array_remove(array_agg(secondary_category),
                     NULL::text))[1]                                AS secondary_category
FROM (SELECT p.id,
             p.username,
             NULL::text AS primary_category,
             NULL::text AS secondary_category
      FROM profile p
      WHERE p.biography ~~* '%Alimentação%'::text
         OR p.biography ~~* '%Restaurante%'::text
         OR p.biography ~~* '%Confeitaria%'::text
         OR p.biography ~~* '%Gastronomia%'::text
         OR p.biography ~~* '%Culinária%'::text
         OR p.biography ~~* '%Doces%'::text
         OR p.biography ~~* '%Cafeteria%'::text
         OR p.biography ~~* '%Padaria%'::text
      UNION ALL
      SELECT p.id,
             p.username,
             pa.commercial_subsegment AS primary_category,
             pa.topic_subniche        AS secondary_category
      FROM profile_analysis pa
               LEFT JOIN profile p ON pa.profile_id = p.id
      WHERE pa.commercial_segment = ANY
            (ARRAY ['Alimentação'::text, 'Restaurante'::text, 'Confeitaria'::text, 'Gastronomia'::text, 'Culinária'::text, 'Doces'::text, 'Restaurantes'::text, 'Cafeteria'::text, 'Padaria'::text])) tmp
GROUP BY id, username;

alter materialized view benchmark_hints.profile_restaurant owner to dowser;

