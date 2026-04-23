import uuid
import yaml
from django.db import models as db_models
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


class Agent(YamlExportMixin, db_models.Model):
    id = db_models.UUIDField(primary_key=True, editable=False)
    name = db_models.TextField()
    description = db_models.TextField()
    uri = db_models.TextField()
    mcp_schema = db_models.JSONField()
    candidate_topics = ArrayField(base_field=db_models.TextField(), null=True, blank=True)

    class Meta:
        db_table = '"agents_topics"."agent"'
        managed = False


class Topic(YamlExportMixin, db_models.Model):
    id = db_models.UUIDField(primary_key=True, editable=False)
    name = db_models.TextField()
    description = db_models.TextField()

    class Meta:
        db_table = '"agents_topics"."topic"'
        managed = False


class AgentTopic(YamlExportMixin, db_models.Model):
    agent = db_models.ForeignKey(Agent, on_delete=db_models.CASCADE)
    topic = db_models.ForeignKey(Topic, on_delete=db_models.CASCADE)
    assigned_at = db_models.DateTimeField(auto_now_add=True)
    is_active = db_models.BooleanField(default=True)

    class Meta:
        db_table = '"agents_topics"."agent_topic"'
        managed = False
        unique_together = [("agent", "topic")]


class ExecutionStatus(db_models.TextChoices):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class JobExecution(YamlExportMixin, db_models.Model):
    job_id = db_models.UUIDField(editable=False)
    thread = db_models.ForeignKey(Thread, on_delete=db_models.DO_NOTHING)
    demand = db_models.ForeignKey(Message, on_delete=db_models.DO_NOTHING)
    topic = db_models.ForeignKey(Topic, on_delete=db_models.DO_NOTHING)
    agent = db_models.ForeignKey(Agent, on_delete=db_models.DO_NOTHING)
    created_at = db_models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = '"broker"."job_execution"'
        managed = False
        unique_together = [("job_id", "agent")]


class JobStatus(YamlExportMixin, db_models.Model):
    id = db_models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job_id = db_models.UUIDField()
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
    semantic_embedding = EmbeddingField(dimensions=1536, null=True, blank=True)
    created_at = db_models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = '"broker"."job_artifact"'
        managed = False
