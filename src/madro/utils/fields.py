from django.db import models


class TsVectorField(models.Field):
    def db_type(self, connection):
        return "tsvector"


class EmbeddingField(models.Field):
    def __init__(self, dimensions: int = 1536, **kwargs):
        self.dimensions = dimensions
        super().__init__(**kwargs)

    def db_type(self, connection):
        return f"vector({self.dimensions})"

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        kwargs["dimensions"] = self.dimensions
        return name, path, args, kwargs
