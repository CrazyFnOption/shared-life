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
    overrides:list['ProposalOverride']=Field(default_factory=list,max_length=100)

class OverrideFields(Model):
    title:str|None=Field(default=None,min_length=1,max_length=200)
    body_markdown:str|None=Field(default=None,max_length=10000)
    target_date:date|None=None
    open_questions_markdown:str|None=Field(default=None,max_length=10000)
    stage_key:UUID|None=None
    position:int|None=Field(default=None,ge=0,le=1000000,strict=True)
    assignee_ids:list[UUID]|None=Field(default=None,max_length=20)
    due_date:date|None=None

class ProposalOverride(Model):
    item_id:UUID
    changes:OverrideFields

class DraftItem(Model):
    id:UUID
    kind:Literal['plan','stage','task']
    title:str=Field(min_length=1,max_length=200)
    body_markdown:str=Field(default='',max_length=10000)
    stage_key:UUID|None=None
    assignee_ids:list[UUID]=Field(default_factory=list,max_length=20)
    due_date:date|None=None
    operation:Literal['create','update','delete']='create'
    target_id:UUID|None=None
    base_version:int|None=Field(default=None,ge=1,strict=True)
    changed_fields:list[str]=Field(default_factory=list,max_length=8)
    position:int|None=Field(default=None,ge=0,le=1000000,strict=True)
    target_date:date|None=None
    open_questions_markdown:str|None=Field(default=None,max_length=10000)

class PlanningOutput(Model):
    message:str=Field(min_length=1,max_length=12000)
    items:list[DraftItem]=Field(default_factory=list,max_length=100)
