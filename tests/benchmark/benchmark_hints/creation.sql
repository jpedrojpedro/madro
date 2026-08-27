-- create schema benchmark_hints;
-- drop materialized view benchmark_hints.profile_location;
CREATE MATERIALIZED VIEW benchmark_hints.profile_location as (
    (
        WITH search_setup AS (
          SELECT to_tsquery('portuguese',
              array_to_string(
                  tsvector_to_array(
                      to_tsvector('portuguese', 'Balneário Camboriú')
                  ),
                  ' | '
              )
          ) AS query
        )
        SELECT p.id, p.username, 'Balneário Camboriú' as location
        FROM public.profile p, search_setup ss
        WHERE biography_lexemes @@ query
        UNION
        SELECT p.id, p.username, pa.city as location
        FROM public.profile p
        INNER JOIN public.profile_analysis pa on p.id = pa.profile_id
        WHERE pa.city = 'Balneário Camboriú'
    )
    UNION ALL
    (
        WITH search_setup AS (
          SELECT to_tsquery('portuguese',
              array_to_string(
                  tsvector_to_array(
                      to_tsvector('portuguese', 'Rio de Janeiro')
                  ),
                  ' | '
              )
          ) AS query
        )
        SELECT p.id, p.username, 'Rio de Janeiro' as location
        FROM public.profile p, search_setup ss
        WHERE biography_lexemes @@ query
        UNION
        SELECT p.id, p.username, pa.city as location
        FROM public.profile p
        INNER JOIN public.profile_analysis pa on p.id = pa.profile_id
        WHERE pa.city = 'Rio de Janeiro'
    )
    UNION ALL
    (
        WITH search_setup AS (
          SELECT to_tsquery('portuguese',
              array_to_string(
                  tsvector_to_array(
                      to_tsvector('portuguese', 'São Paulo')
                  ),
                  ' | '
              )
          ) AS query
        )
        SELECT p.id, p.username, 'São Paulo' as location
        FROM public.profile p, search_setup ss
        WHERE biography_lexemes @@ query
        UNION
        SELECT p.id, p.username, pa.city as location
        FROM public.profile p
        INNER JOIN public.profile_analysis pa on p.id = pa.profile_id
        WHERE pa.city = 'São Paulo'
    )
    UNION ALL
    (
        WITH search_setup AS (
          SELECT to_tsquery('portuguese',
              array_to_string(
                  tsvector_to_array(
                      to_tsvector('portuguese', 'Florianópolis')
                  ),
                  ' | '
              )
          ) AS query
        )
        SELECT p.id, p.username, 'Florianópolis' as location
        FROM public.profile p, search_setup ss
        WHERE biography_lexemes @@ query
        UNION
        SELECT p.id, p.username, pa.city as location
        FROM public.profile p
        INNER JOIN public.profile_analysis pa on p.id = pa.profile_id
        WHERE pa.city = 'Florianópolis'
    )
    UNION ALL
    (
        WITH search_setup AS (
          SELECT to_tsquery('portuguese',
              array_to_string(
                  tsvector_to_array(
                      to_tsvector('portuguese', 'Curitiba')
                  ),
                  ' | '
              )
          ) AS query
        )
        SELECT p.id, p.username, 'Curitiba' as location
        FROM public.profile p, search_setup ss
        WHERE biography_lexemes @@ query
        UNION
        SELECT p.id, p.username, pa.city as location
        FROM public.profile p
        INNER JOIN public.profile_analysis pa on p.id = pa.profile_id
        WHERE pa.city = 'Curitiba'
    )
)
;

-- drop materialized view benchmark_hints.profile_restaurant;
CREATE MATERIALIZED VIEW benchmark_hints.profile_restaurant as (
    SELECT tmp.id,
           tmp.username,
           (array_remove(array_agg(tmp.primary_category), NULL))[1] as primary_category,
           (array_remove(array_agg(tmp.secondary_category), NULL))[1] as secondary_category
    FROM (
        SELECT p.id, p.username, null as primary_category, null as secondary_category
        FROM public.profile p
        WHERE p.biography ILIKE '%Alimentação%'
           OR p.biography ILIKE '%Restaurante%'
           OR p.biography ILIKE '%Confeitaria%'
           OR p.biography ILIKE '%Gastronomia%'
           OR p.biography ILIKE '%Culinária%'
           OR p.biography ILIKE '%Doces%'
           OR p.biography ILIKE '%Cafeteria%'
           OR p.biography ILIKE '%Padaria%'
        UNION ALL
        SELECT p.id, p.username, pa.commercial_subsegment as primary_category, pa.topic_subniche as secondary_category
        FROM public.profile_analysis pa
        LEFT JOIN public.profile p on pa.profile_id = p.id
        WHERE pa.commercial_segment = ANY(ARRAY[
            'Alimentação',
            'Restaurante',
            'Confeitaria',
            'Gastronomia',
            'Culinária',
            'Doces',
            'Restaurantes',
            'Cafeteria',
            'Padaria'
        ])
    ) tmp
    GROUP BY 1, 2
)
;

CREATE MATERIALIZED VIEW benchmark_hints.profile_influencer as (
    select id, username
    from public.profile
    where num_followers >= 50000
)
;

-- drop materialized view benchmark_hints.profile;
CREATE MATERIALIZED VIEW benchmark_hints.profile as (
select
    pr.id,
    pr.username,
    pr.full_name,
    pr.is_private,
    pr.num_medias,
    pr.num_followers,
    pr.num_following,
    pr.biography,
    pr.is_verified,
    case when pri.id is not null then 't' else 'f' end as is_influencer,
    case when prr.id is not null then 't' else 'f' end as is_restaurant,
    prr.primary_category as restaurant_primary_category,
    prr.secondary_category as restaurant_secondary_category,
    pr.biography_lexemes
from public.profile pr
left join benchmark_hints.profile_restaurant prr on pr.id = prr.id
left join benchmark_hints.profile_influencer pri on pr.id = pri.id
)
;

CREATE MATERIALIZED VIEW benchmark_hints.fluminensefc_publication as (
select pub.*
from public.publication pub
left join public.profile pr1 on pub.profile_id = pr1.id
left join public.publication_collab pc on pub.id = pc.publication_id
left join public.profile pr2 on pc.profile_id = pr2.id
where pr1.username = ANY(ARRAY['fluminensefc', 'fluminensefcfem', 'sociodoflu', 'timefluminense'])
   or pr2.username = ANY(ARRAY['fluminensefc', 'fluminensefcfem', 'sociodoflu', 'timefluminense'])
)
;

-- drop materialized view benchmark_hints.fluminensefc_comment;
CREATE MATERIALIZED VIEW benchmark_hints.fluminensefc_comment as (
    WITH filtered_comments AS (
        SELECT c.*
        FROM public.publication pub
        LEFT JOIN public.profile pr1 ON pub.profile_id = pr1.id
        LEFT JOIN public.publication_collab pc ON pub.id = pc.publication_id
        LEFT JOIN public.profile pr2 ON pc.profile_id = pr2.id
        LEFT JOIN public.comment c ON pub.id = c.publication_id
        LEFT JOIN public.profile pr3 ON c.profile_id = pr3.id
        WHERE pr1.username = ANY(ARRAY['fluminensefc', 'fluminensefcfem', 'sociodoflu', 'timefluminense'])
           OR pr2.username = ANY(ARRAY['fluminensefc', 'fluminensefcfem', 'sociodoflu', 'timefluminense'])
    )
    SELECT
        fc.*,
        to_tsvector('pt_en', COALESCE(misc.emoji_replace(fc.annotation), '')) AS annotation_lexemes
    FROM filtered_comments fc
)
;

CREATE MATERIALIZED VIEW benchmark_hints.broxadasinistra_publication as (
select pub.*
from public.publication pub
left join public.profile pr1 on pub.profile_id = pr1.id
left join public.publication_collab pc on pub.id = pc.publication_id
left join public.profile pr2 on pc.profile_id = pr2.id
where pr1.username = 'broxadasinistra'
   or pr2.username = 'broxadasinistra'
)
;

-- drop materialized view benchmark_hints.broxadasinistra_comment;
CREATE MATERIALIZED VIEW benchmark_hints.broxadasinistra_comment as (
    WITH filtered_comments AS (
        SELECT c.*
        FROM public.publication pub
        LEFT JOIN public.profile pr1 ON pub.profile_id = pr1.id
        LEFT JOIN public.publication_collab pc ON pub.id = pc.publication_id
        LEFT JOIN public.profile pr2 ON pc.profile_id = pr2.id
        LEFT JOIN public.comment c ON pub.id = c.publication_id
        LEFT JOIN public.profile pr3 ON c.profile_id = pr3.id
        where pr1.username = 'broxadasinistra'
           or pr2.username = 'broxadasinistra'
    )
    SELECT
        fc.*,
        to_tsvector('pt_en', COALESCE(misc.emoji_replace(fc.annotation), '')) AS annotation_lexemes
    FROM filtered_comments fc
)
;

CREATE MATERIALIZED VIEW benchmark_hints.eusougabriela_publication as (
select pub.*
from public.publication pub
left join public.profile pr1 on pub.profile_id = pr1.id
left join public.publication_collab pc on pub.id = pc.publication_id
left join public.profile pr2 on pc.profile_id = pr2.id
where pr1.username = 'eusougabriela'
   or pr2.username = 'eusougabriela'
)
;

-- drop materialized view benchmark_hints.eusougabriela_comment;
CREATE MATERIALIZED VIEW benchmark_hints.eusougabriela_comment as (
    WITH filtered_comments AS (
        SELECT c.*
        FROM public.publication pub
        LEFT JOIN public.profile pr1 ON pub.profile_id = pr1.id
        LEFT JOIN public.publication_collab pc ON pub.id = pc.publication_id
        LEFT JOIN public.profile pr2 ON pc.profile_id = pr2.id
        LEFT JOIN public.comment c ON pub.id = c.publication_id
        LEFT JOIN public.profile pr3 ON c.profile_id = pr3.id
        where pr1.username = 'eusougabriela'
           or pr2.username = 'eusougabriela'
    )
    SELECT
        fc.*,
        to_tsvector('pt_en', COALESCE(misc.emoji_replace(fc.annotation), '')) AS annotation_lexemes
    FROM filtered_comments fc
)
;

CREATE MATERIALIZED VIEW benchmark_hints.haight_clothing_publication as (
select pub.*
from public.publication pub
left join public.profile pr1 on pub.profile_id = pr1.id
left join public.publication_collab pc on pub.id = pc.publication_id
left join public.profile pr2 on pc.profile_id = pr2.id
where pr1.username = 'haight_clothing'
   or pr2.username = 'haight_clothing'
)
;

-- drop materialized view benchmark_hints.haight_clothing_comment;
CREATE MATERIALIZED VIEW benchmark_hints.haight_clothing_comment as (
    WITH filtered_comments AS (
        SELECT c.*
        FROM public.publication pub
        LEFT JOIN public.profile pr1 ON pub.profile_id = pr1.id
        LEFT JOIN public.publication_collab pc ON pub.id = pc.publication_id
        LEFT JOIN public.profile pr2 ON pc.profile_id = pr2.id
        LEFT JOIN public.comment c ON pub.id = c.publication_id
        LEFT JOIN public.profile pr3 ON c.profile_id = pr3.id
        where pr1.username = 'haight_clothing'
           or pr2.username = 'haight_clothing'
    )
    SELECT
        fc.*,
        to_tsvector('pt_en', COALESCE(misc.emoji_replace(fc.annotation), '')) AS annotation_lexemes
    FROM filtered_comments fc
)
;