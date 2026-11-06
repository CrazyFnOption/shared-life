from datetime import date
from typing import Literal
from uuid import UUID
from pydantic import Field
from server.memos.models import Model

class Message(Model):
    op_id:UUID
    content:str=Field(min_length=1,max_length=8000)
    attachment_ids:list[UUID]=Field(default_factory=list,max_length=5)
    allow_web:bool=Field(default=True,strict=True)
    base_version:int=Field(ge=1,strict=True)

class Adopt(Model):
    op_id:UUID
    draft_id:UUID
    item_ids:list[UUID]=Field(min_length=1,max_length=100)
    base_version:int=Field(ge=1,strict=True)
    target_plan_id:UUID|None=None

class DraftItem(Model):
    id:UUID
    kind:Literal['stage','task']
    title:str=Field(min_length=1,max_length=200)
    body_markdown:str=Field(default='',max_length=10000)
    stage_key:UUID|None=None
    assignee_ids:list[UUID]=Field(default_factory=list,max_length=20)
    due_date:date|None=None

class PlanningOutput(Model):
    message:str=Field(min_length=1,max_length=12000)
    items:list[DraftItem]=Field(default_factory=list,max_length=100)
