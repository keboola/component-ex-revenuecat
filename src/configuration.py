from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator


class EntityGroup(StrEnum):
    config = "config"
    customers = "customers"


class LoadType(StrEnum):
    full_load = "full_load"
    incremental_load = "incremental_load"


class Configuration(BaseModel):
    model_config = ConfigDict(extra="ignore")

    api_key: str = Field(alias="#api_key")
    project_id: str | None = None
    entities: list[EntityGroup] = [EntityGroup.config, EntityGroup.customers]
    load_type: LoadType = LoadType.full_load

    @computed_field
    @property
    def incremental(self) -> bool:
        return self.load_type == LoadType.incremental_load

    @field_validator("api_key")
    @classmethod
    def _non_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("RevenueCat API key (#api_key) is required.")
        return v
