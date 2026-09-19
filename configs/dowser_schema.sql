create type edge_type as enum ('follows');

alter type edge_type owner to instaloader;

create type file_extension as enum ('mp4', 'jpg', 'json', 'txt');

alter type file_extension owner to instaloader;

create type media_type as enum ('GraphImage', 'GraphSidecar', 'GraphVideo');

alter type media_type owner to instaloader;

create type profile_category as enum ('Fine Dining Restaurants', 'Casual Dining Restaurants', 'Fast Food and Street Food', 'Cafes and Coffee Shops', 'Bars and Pubs', 'Specialty Foods (Bakeries, Ice Cream Shops)', 'General Retail', 'Fashion and Apparel', 'Electronics and Appliances', 'Books, Music, and Games Stores', 'Furniture and Home Decor', 'Beauty and Cosmetics Stores', 'Sporting Goods and Outdoor Equipment', 'General and Specialty Healthcare', 'Dental and Orthodontic Services', 'Alternative and Holistic Health Services', 'Fitness and Gym Facilities', 'Sports Teams and Clubs', 'Recreational Activities', 'Arts and Crafts', 'Museums and Galleries', 'Music and Performing Arts Venues', 'Cinemas and Entertainment Centers', 'Educational Institutions', 'Training and Coaching Services', 'Professional Services (Legal, Consulting)', 'Financial Services', 'Real Estate Services', 'Construction and Renovation', 'Auto Sales and Services', 'Transport and Logistics', 'Travel Agencies and Tour Operators', 'Accommodation Services', 'Event Planning and Catering', 'Advertising and Marketing', 'IT and Technology Services', 'Media Production and Publishing', 'Corporate Services', 'Industrial and Manufacturing', 'Agricultural and Environmental Services', 'Energy and Utilities', 'Community and Social Services', 'Government and Public Administration', 'Religious and Spiritual Services', 'Animal Care and Pet Services', 'Specialty Consulting (Environmental, Safety)', 'Telecommunications', 'Online and E-commerce Services', 'Emergency and Rescue Services', 'Arts and Literature');

alter type profile_category owner to instaloader;

create type profile_segment as enum ('Network Marketing', 'Gambling', 'Gourmet Products', 'Fitness/Training', 'Religion and Music', 'Art and Craft', 'TV Channel', 'Music Marketing', 'Business/Supplies', 'Food/Bar', 'Alcohol', 'Fitness Equipment', 'Professional Networking', 'Fitness Events', 'Home Renovation', 'Health and Education', 'Fashion/Jewelry', 'Media and Entertainment', 'Nonprofit', 'Legal and Professional', 'Education and Learning', 'Legal/Judiciary', 'Finance/Investing', 'Science - Education', 'Books and Media', 'Marketing Technology', 'Photography Courses', 'Alcohol & Spirits', 'Art/Animation', 'Gaming/Streaming', 'Tourism & Services', 'News', 'Nature', 'Tea', 'Modeling/Influencer', 'Events and Ceremonies', 'Music/Education', 'Political', 'Parenting/Babies', 'Cultural Content', 'Poetry/Author', 'Fashion/Handbags', 'Supplements Store', 'Business/Innovation', 'Fashion/Model', 'Lifestyle/Cooking', 'Diversity and Inclusion', 'Sports/Equestrian', 'Educational Institution', 'Music/Entertainment');

alter type profile_segment owner to instaloader;

create table profile
(
    id                bigint                  not null
        primary key,
    username          text,
    full_name         text,
    is_private        boolean,
    num_medias        bigint,
    num_followers     bigint,
    num_following     bigint,
    biography         text,
    profile_pic_url   text,
    visited_at        timestamp default now() not null,
    inferred_category profile_category,
    inferred_segment  profile_segment,
    is_brand          boolean,
    is_verified       boolean,
    enriched_at       timestamp,
    biography_lexemes tsvector
);

alter table profile
    owner to instaloader;

create index profile_biography_lexemes_idx
    on profile using gin (biography_lexemes);

create index profile_enriched_at_idx
    on profile (enriched_at);

create unique index unq_profile_username
    on profile (username);

create table profile_relationship
(
    origin_profile_id      bigint                  not null
        constraint graph_origin_profile_id_fkey
            references profile,
    edge                   edge_type               not null,
    destination_profile_id bigint                  not null
        constraint graph_destination_profile_id_fkey
            references profile,
    visited_at             timestamp default now() not null,
    constraint graph_pkey
        primary key (origin_profile_id, edge, destination_profile_id)
);

alter table profile_relationship
    owner to instaloader;

create index idx_profile_rel_destination
    on profile_relationship (destination_profile_id);

create index idx_profile_rel_origin
    on profile_relationship (origin_profile_id);

create table publication
(
    id                  bigint                  not null
        primary key,
    slug                text,
    url                 text,
    type                media_type,
    description         text,
    profile_id          bigint                  not null
        references profile,
    visited_at          timestamp default now() not null,
    published_at        timestamp,
    num_likes           bigint,
    num_comments        bigint,
    description_lexemes tsvector
);

alter table publication
    owner to instaloader;

create table comment
(
    id             bigint                  not null
        primary key,
    profile_id     bigint                  not null
        references profile,
    publication_id bigint                  not null
        references publication,
    annotation     text                    not null,
    num_likes      bigint,
    visited_at     timestamp default now() not null,
    reply_to       bigint
        references comment,
    published_at   timestamp
);

alter table comment
    owner to instaloader;

create index publication_description_lexemes_idx
    on publication using gin (description_lexemes);

create table publication_collab
(
    publication_id bigint not null
        references publication,
    profile_id     bigint not null
        references profile,
    primary key (publication_id, profile_id)
);

alter table publication_collab
    owner to instaloader;

create table raw_file
(
    id             bigserial
        primary key,
    name           text,
    extension      file_extension,
    position       integer,
    publication_id bigint not null
        references publication,
    data           bytea,
    external_id    text,
    constraint unq_raw_file_name_publication
        unique (name, publication_id)
);

alter table raw_file
    owner to instaloader;

create index raw_file_external_id_idx
    on raw_file (external_id);

create function uuid_nil() returns uuid
    immutable
    strict
    parallel safe
    language c
as
$$
begin
-- missing source code
end;
$$;

alter function uuid_nil() owner to dowser;

create function uuid_ns_dns() returns uuid
    immutable
    strict
    parallel safe
    language c
as
$$
begin
-- missing source code
end;
$$;

alter function uuid_ns_dns() owner to dowser;

create function uuid_ns_url() returns uuid
    immutable
    strict
    parallel safe
    language c
as
$$
begin
-- missing source code
end;
$$;

alter function uuid_ns_url() owner to dowser;

create function uuid_ns_oid() returns uuid
    immutable
    strict
    parallel safe
    language c
as
$$
begin
-- missing source code
end;
$$;

alter function uuid_ns_oid() owner to dowser;

create function uuid_ns_x500() returns uuid
    immutable
    strict
    parallel safe
    language c
as
$$
begin
-- missing source code
end;
$$;

alter function uuid_ns_x500() owner to dowser;

create function uuid_generate_v1() returns uuid
    strict
    parallel safe
    language c
as
$$
begin
-- missing source code
end;
$$;

alter function uuid_generate_v1() owner to dowser;

create function uuid_generate_v1mc() returns uuid
    strict
    parallel safe
    language c
as
$$
begin
-- missing source code
end;
$$;

alter function uuid_generate_v1mc() owner to dowser;

create function uuid_generate_v3(namespace uuid, name text) returns uuid
    immutable
    strict
    parallel safe
    language c
as
$$
begin
-- missing source code
end;
$$;

alter function uuid_generate_v3(uuid, text) owner to dowser;

create function uuid_generate_v4() returns uuid
    strict
    parallel safe
    language c
as
$$
begin
-- missing source code
end;
$$;

alter function uuid_generate_v4() owner to dowser;

create function uuid_generate_v5(namespace uuid, name text) returns uuid
    immutable
    strict
    parallel safe
    language c
as
$$
begin
-- missing source code
end;
$$;

alter function uuid_generate_v5(uuid, text) owner to dowser;

create function profile_biography_lexemes_update() returns trigger
    language plpgsql
as
$$
BEGIN
  NEW.biography_lexemes :=
    to_tsvector('pt_en', misc.emoji_replace(COALESCE(NEW.biography, '')));
  RETURN NEW;
END;
$$;

alter function profile_biography_lexemes_update() owner to instaloader;

create function publication_description_lexemes_update() returns trigger
    language plpgsql
as
$$
BEGIN
  NEW.description_lexemes :=
    to_tsvector('pt_en', misc.emoji_replace(COALESCE(NEW.description, '')));
  RETURN NEW;
END;
$$;

alter function publication_description_lexemes_update() owner to instaloader;

create function set_external_id() returns trigger
    language plpgsql
as
$$
BEGIN
  NEW.external_id := uuid_generate_v5('d05ef06a-2313-4a9f-9541-42e913db1dec', NEW.id::text);
  RETURN NEW;
END;
$$;

alter function set_external_id() owner to instaloader;
