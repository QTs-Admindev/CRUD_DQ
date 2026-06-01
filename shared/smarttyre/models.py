from pydantic import BaseModel
from typing import Any


class SmartTyreListResponse(BaseModel):
    records: list[dict[str, Any]] = []
    total: int = 0
    pageNum: int = 1
    pageSize: int = 10
