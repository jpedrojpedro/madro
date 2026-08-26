create materialized view benchmark_hints.profile_influencer as
SELECT id,
       username
FROM profile
WHERE num_followers >= 50000;

alter materialized view benchmark_hints.profile_influencer owner to dowser;

