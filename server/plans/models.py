"""Strict wire models for the manual wishes/plans module."""
from datetime import date
from typing import Literal
from uuid import UUID
from pydantic import Field, field_validator
from server.memos.models import Model, Attachment

class Operation(Model):
    op_id: UUID
    entity_type: Literal['wish','plan','stage','task','deletion_request']
    entity_id: UUID
    kind: str = Field(min_length=1,max_length=40)
    base_version: int = Field(ge=0,le=9223372036854775806,strict=True)
    data: dict

class Content(Model):
    title: str = Field(min_length=1,max_length=200)
    body_markdown: str = ''
    attachments: list[Attachment] = Field(default_factory=list,max_length=100)
    @field_validator('title',mode='before')
    @classmethod
    def title_trim(cls,v): return v.strip() if isinstance(v,str) else v
    @field_validator('body_markdown')
    @classmethod
    def body_limit(cls,v):
        if len(v.encode())>200*1024: raise ValueError('Body exceeds 200 KiB')
        return v

class RecordData(Content):
    wish_kind: Literal['生活愿景','未来旅行','想要的东西',''] = ''
    target_date: date | None = None

class FromWishData(RecordData):
    source_wish_id: UUID

class StageData(Model):
    plan_id: UUID
    title: str = Field(min_length=1,max_length=200)
    position: int = Field(ge=0,le=1000000,strict=True)
    @field_validator('title',mode='before')
    @classmethod
    def trim(cls,v):return v.strip() if isinstance(v,str) else v

class TaskData(Content):
    plan_id: UUID
    stage_id: UUID
    assignee_ids: list[UUID] = Field(default_factory=list,max_length=100)
    due_date: date | None = None
    position: int = Field(ge=0,le=1000000,strict=True)

class RealizedData(Model): realized: bool = Field(strict=True)
class ArchiveData(Model): archived: bool = Field(strict=True)
class CompleteData(Model): completed: bool = Field(strict=True)
class EmptyData(Model): pass
class DeletionData(Model):
    target_type: Literal['wish','plan','task']
    target_id: UUID
    target_version: int = Field(ge=1,strict=True)
    reason: str = Field(default='',max_length=2000)
class DecideData(Model): accepted: bool = Field(strict=True)

def validate_data(operation):
    kind,entity=operation.kind,operation.entity_type
    model = (RecordData if entity in {'wish','plan'} and kind in {'create','update'} else
             FromWishData if entity=='plan' and kind=='from_wish' else
             StageData if entity=='stage' and kind in {'create','update'} else
             TaskData if entity=='task' and kind in {'create','update'} else
             RealizedData if entity=='wish' and kind=='realized' else
             ArchiveData if entity in {'wish','plan'} and kind=='archive' else
             CompleteData if entity=='task' and kind=='complete' else
             EmptyData if entity in {'wish','plan','stage','task'} and kind=='delete' else
             DeletionData if entity=='deletion_request' and kind=='create' else
             DecideData if entity=='deletion_request' and kind=='decide' else None)
    if model is None: raise ValueError('Unsupported plan operation')
    return model.model_validate(operation.data).model_dump(mode='json')
