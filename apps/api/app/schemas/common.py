from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """Request bodies: unknown fields are rejected (422), strings are stripped."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Page[T](BaseModel):
    items: list[T]
    page: int
    page_size: int
    total: int
