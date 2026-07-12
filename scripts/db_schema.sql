create extension if not exists "uuid-ossp";
create extension if not exists vector;

create schema broker;
create schema agents_topics;
create schema flow_control;

alter database postgres set timezone to 'utc';

create or replace function public.custom_uuid_generate_v5(ns uuid, val text)
returns uuid
language sql
immutable
as $$
    select uuid_generate_v5(ns, val);
$$;


create or replace function public.get_namespace()
returns uuid AS $$
    select '2234acab-b9c4-4e3a-8d5c-e435f17f4f7c'::uuid;
$$ language sql immutable;

create type agents_topics.agent_modality as enum ('text', 'image');

create table agents_topics.agent(
    id uuid primary key generated always as (
        public.custom_uuid_generate_v5(
            public.get_namespace(),
            name || '|' || uri
        )
    ) stored,
    name text not null,
    description text not null,
    uri text not null,
    mcp_schema jsonb not null,
    candidate_topics text[] null,
    modality agents_topics.agent_modality not null default 'text',
    identity jsonb not null default '[]'::jsonb
);

create table agents_topics.topic(
    id uuid primary key generated always as (
        public.custom_uuid_generate_v5(
            public.get_namespace(),
            name
        )
    ) stored,
    name text not null check (
        length(trim(name)) > 0 and length(name) <= 50
    ),
    description text not null
);

create table agents_topics.agent_topic (
    agent_id uuid references agents_topics.agent(id) on delete cascade,
    topic_id uuid references agents_topics.topic(id) on delete cascade,
    assigned_at timestamptz default (now() at time zone 'utc'),
    is_active boolean default true,
    primary key (agent_id, topic_id)
);

create type flow_control.message_role as enum ('user', 'assistant', 'system');

create table flow_control.thread (
    id uuid primary key default gen_random_uuid(),
    created_at timestamptz default (now() at time zone 'utc'),
    updated_at timestamptz default (now() at time zone 'utc')
);

create table flow_control.message (
    id uuid primary key default gen_random_uuid(),
    thread_id uuid not null references flow_control.thread(id) on delete no action,
    role flow_control.message_role not null,
    content text not null,
    sequence_number serial,
    model_name text,
    token_count integer,
    created_at timestamptz default (now() at time zone 'utc')
);

create table broker.job_execution (
    job_id uuid generated always as (
        public.custom_uuid_generate_v5(
            public.get_namespace(),
            thread_id::text || '|' || demand_id::text || '|' || topic_id::text
        )
    ) stored,
    thread_id uuid not null references flow_control.thread(id),
    demand_id uuid not null references flow_control.message(id),
    topic_id uuid not null references agents_topics.topic(id),
    agent_id uuid not null references agents_topics.agent(id),
    created_at timestamptz default (now() at time zone 'utc'),
    primary key (job_id, agent_id)
);

create type broker.execution_status as enum (
    'pending', 'processing', 'completed', 'failed'
);

create table broker.job_status (
    id uuid primary key default gen_random_uuid(),
    job_id uuid not null,
    agent_id uuid not null,
    status broker.execution_status not null,
    finished_at timestamptz default (now() at time zone 'utc'),
    constraint fk_status_to_execution
        foreign key (job_id, agent_id)
        references broker.job_execution(job_id, agent_id)
        on delete no action
);

create table broker.job_artifact (
    job_status_id uuid primary key references broker.job_status(id) on delete cascade,
    canonical_text text not null,
    confidence_score numeric(5,4) check (confidence_score between 0 and 1),
    provenance_details jsonb,
    lexical_vector tsvector,
    created_at timestamptz default (now() at time zone 'utc')
);

create table broker.job_artifact_document (
    id uuid primary key default gen_random_uuid(),
    job_artifact_id uuid not null references broker.job_artifact(job_status_id) on delete cascade,
    chunk_index integer not null,
    chunk_text text not null,
    embedding vector(768) not null,
    created_at timestamptz default (now() at time zone 'utc'),
    unique (job_artifact_id, chunk_index)
);

create index idx_artifact_document_embedding
    on broker.job_artifact_document using hnsw (embedding vector_cosine_ops);
create index idx_artifact_confidence
    on broker.job_artifact (confidence_score desc);
