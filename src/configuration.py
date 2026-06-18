from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class EntityGroup(StrEnum):
    config = "config"
    customers = "customers"


class Configuration(BaseModel):
    model_config = ConfigDict(extra="ignore")

    api_key: str = Field(alias="#api_key")
    project_id: str | None = None
    entities: list[EntityGroup] = [EntityGroup.config, EntityGroup.customers]

    @field_validator("api_key")
    @classmethod
    def _non_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("RevenueCat API key (#api_key) is required.")
        return v
