import json
import uuid
import yaml
from django.db import models as db_models, connection
from django.contrib.postgres.fields import ArrayField
from madro.utils.fields import TsVectorField, EmbeddingField


class YamlExportMixin:
    def to_yaml(self, *args: str) -> str:
        fields = args or [
            f.name
            for f in self._meta.get_fields()
            if hasattr(f, "attname") or not f.is_relation
        ]
        data = {field: getattr(self, field) for field in fields}
        return yaml.dump(data, default_flow_style=False, allow_unicode=True)


class GeneratedPKMixin:
    """Mixin for models whose PK is a PostgreSQL GENERATED ALWAYS AS STORED column.

    Subclasses must implement `_insert_generated_sql()` returning (sql, params)
    and `_set_pk(row)` to assign the returned PK value.
    """

    def _insert_generated_sql(self) -> tuple[str, list]:
        raise NotImplementedError

    def _set_pk(self, row: tuple) -> None:
        raise NotImplementedError

    def save(self, *args, **kwargs):
        if self._state.adding:
            sql, params = self._insert_generated_sql()
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                self._set_pk(cursor.fetchone())
            self._state.adding = False
        else:
            super().save(*args, **kwargs)

    async def asave(self, *args, **kwargs):
        if self._state.adding:
            from madro.db import async_cursor
            sql, params = self._insert_generated_sql()
            async with async_cursor() as cur:
                await cur.execute(sql, params)
                self._set_pk(await cur.fetchone())
            self._state.adding = False
        else:
            await super().asave(*args, **kwargs)


class MessageRole(db_models.TextChoices):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class Thread(YamlExportMixin, db_models.Model):
    id = db_models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = db_models.DateTimeField(auto_now_add=True)
    updated_at = db_models.DateTimeField(auto_now=True)

    class Meta:
        db_table = '"flow_control"."thread"'
        managed = False


class Message(YamlExportMixin, db_models.Model):
    id = db_models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    thread = db_models.ForeignKey(Thread, on_delete=db_models.DO_NOTHING, related_name="messages")
    role = db_models.TextField(choices=MessageRole.choices)
    content = db_models.TextField()
    sequence_number = db_models.IntegerField()
    model_name = db_models.TextField(null=True, blank=True)
    token_count = db_models.IntegerField(null=True, blank=True)
    created_at = db_models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = '"flow_control"."message"'
        managed = False


class Agent(GeneratedPKMixin, YamlExportMixin, db_models.Model):
    id = db_models.UUIDField(primary_key=True, editable=False)
    name = db_models.TextField()
    description = db_models.TextField()
    uri = db_models.TextField()
    mcp_schema = db_models.JSONField()
    candidate_topics = ArrayField(base_field=db_models.TextField(), null=True, blank=True)

    def _insert_generated_sql(self):
        return (
            "INSERT INTO agents_topics.agent (name, description, uri, mcp_schema, candidate_topics) VALUES (%s, %s, %s, %s, %s) RETURNING id",
            [self.name, self.description, self.uri, json.dumps(self.mcp_schema), self.candidate_topics],
        )

    def _set_pk(self, row): self.id = row[0]

    class Meta:
        db_table = '"agents_topics"."agent"'
        managed = False


class Topic(GeneratedPKMixin, YamlExportMixin, db_models.Model):
    id = db_models.UUIDField(primary_key=True, editable=False)
    name = db_models.TextField()
    description = db_models.TextField()

    def _insert_generated_sql(self):
        return (
            "INSERT INTO agents_topics.topic (name, description) VALUES (%s, %s) RETURNING id",
            [self.name, self.description],
        )

    def _set_pk(self, row): self.id = row[0]

    class Meta:
        db_table = '"agents_topics"."topic"'
        managed = False


class AgentTopic(YamlExportMixin, db_models.Model):
    agent = db_models.ForeignKey(Agent, on_delete=db_models.CASCADE, primary_key=True)
    topic = db_models.ForeignKey(Topic, on_delete=db_models.CASCADE)
    assigned_at = db_models.DateTimeField(auto_now_add=True)
    is_active = db_models.BooleanField(default=True)

    class Meta:
        db_table = '"agents_topics"."agent_topic"'
        managed = False


class ExecutionStatus(db_models.TextChoices):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class JobExecution(GeneratedPKMixin, YamlExportMixin, db_models.Model):
    job_id = db_models.UUIDField(primary_key=True, editable=False)
    thread = db_models.ForeignKey(Thread, on_delete=db_models.DO_NOTHING)
    demand = db_models.ForeignKey(Message, on_delete=db_models.DO_NOTHING)
    topic = db_models.ForeignKey(Topic, on_delete=db_models.DO_NOTHING)
    agent = db_models.ForeignKey(Agent, on_delete=db_models.DO_NOTHING)
    created_at = db_models.DateTimeField(auto_now_add=True)

    def _insert_generated_sql(self):
        return (
            "INSERT INTO broker.job_execution (thread_id, demand_id, topic_id, agent_id) VALUES (%s, %s, %s, %s) RETURNING job_id",
            [self.thread_id, self.demand_id, self.topic_id, self.agent_id],
        )

    def _set_pk(self, row): self.job_id = row[0]

    class Meta:
        db_table = '"broker"."job_execution"'
        managed = False


class JobStatus(YamlExportMixin, db_models.Model):
    id = db_models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = db_models.ForeignKey(JobExecution, on_delete=db_models.DO_NOTHING, db_column="job_id", to_field="job_id")
    agent = db_models.ForeignKey(Agent, on_delete=db_models.DO_NOTHING, db_column="agent_id")
    status = db_models.TextField(choices=ExecutionStatus.choices)
    finished_at = db_models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = '"broker"."job_status"'
        managed = False


class JobArtifact(YamlExportMixin, db_models.Model):
    job_status = db_models.OneToOneField(JobStatus, on_delete=db_models.CASCADE, primary_key=True)
    canonical_text = db_models.TextField()
    confidence_score = db_models.DecimalField(max_digits=5, decimal_places=4, null=True, blank=True)
    provenance_details = db_models.JSONField(null=True, blank=True)
    lexical_vector = TsVectorField(null=True, blank=True)
    semantic_embedding = EmbeddingField(dimensions=768, null=True, blank=True)
    created_at = db_models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = '"broker"."job_artifact"'
        managed = False
