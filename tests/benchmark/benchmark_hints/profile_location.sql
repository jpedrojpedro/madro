create materialized view benchmark_hints.profile_location as
(WITH search_setup AS (SELECT to_tsquery('portuguese'::regconfig, array_to_string(
        tsvector_to_array(to_tsvector('portuguese'::regconfig,
                                      'Balneário Camboriú'::text)),
        ' | '::text)) AS query)
 SELECT p.id,
        p.username,
        'Balneário Camboriú'::text AS location
 FROM profile p,
      search_setup ss
 WHERE p.biography_lexemes @@ ss.query
 UNION
 SELECT p.id,
        p.username,
        pa.city AS location
 FROM profile p
          JOIN profile_analysis pa ON p.id = pa.profile_id
 WHERE pa.city = 'Balneário Camboriú'::text)
UNION ALL
(WITH search_setup AS (SELECT to_tsquery('portuguese'::regconfig, array_to_string(
        tsvector_to_array(to_tsvector('portuguese'::regconfig, 'Rio de Janeiro'::text)),
        ' | '::text)) AS query)
 SELECT p.id,
        p.username,
        'Rio de Janeiro'::text AS location
 FROM profile p,
      search_setup ss
 WHERE p.biography_lexemes @@ ss.query
 UNION
 SELECT p.id,
        p.username,
        pa.city AS location
 FROM profile p
          JOIN profile_analysis pa ON p.id = pa.profile_id
 WHERE pa.city = 'Rio de Janeiro'::text)
UNION ALL
(WITH search_setup AS (SELECT to_tsquery('portuguese'::regconfig, array_to_string(
        tsvector_to_array(to_tsvector('portuguese'::regconfig, 'São Paulo'::text)),
        ' | '::text)) AS query)
 SELECT p.id,
        p.username,
        'São Paulo'::text AS location
 FROM profile p,
      search_setup ss
 WHERE p.biography_lexemes @@ ss.query
 UNION
 SELECT p.id,
        p.username,
        pa.city AS location
 FROM profile p
          JOIN profile_analysis pa ON p.id = pa.profile_id
 WHERE pa.city = 'São Paulo'::text)
UNION ALL
(WITH search_setup AS (SELECT to_tsquery('portuguese'::regconfig, array_to_string(
        tsvector_to_array(to_tsvector('portuguese'::regconfig, 'Florianópolis'::text)),
        ' | '::text)) AS query)
 SELECT p.id,
        p.username,
        'Florianópolis'::text AS location
 FROM profile p,
      search_setup ss
 WHERE p.biography_lexemes @@ ss.query
 UNION
 SELECT p.id,
        p.username,
        pa.city AS location
 FROM profile p
          JOIN profile_analysis pa ON p.id = pa.profile_id
 WHERE pa.city = 'Florianópolis'::text)
UNION ALL
(WITH search_setup AS (SELECT to_tsquery('portuguese'::regconfig, array_to_string(
        tsvector_to_array(to_tsvector('portuguese'::regconfig, 'Curitiba'::text)),
        ' | '::text)) AS query)
 SELECT p.id,
        p.username,
        'Curitiba'::text AS location
 FROM profile p,
      search_setup ss
 WHERE p.biography_lexemes @@ ss.query
 UNION
 SELECT p.id,
        p.username,
        pa.city AS location
 FROM profile p
          JOIN profile_analysis pa ON p.id = pa.profile_id
 WHERE pa.city = 'Curitiba'::text);

alter materialized view benchmark_hints.profile_location owner to dowser;

