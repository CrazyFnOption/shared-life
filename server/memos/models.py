"""Wire validation for memo-specific versioned mutations."""
from datetime import date, datetime
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Operation(Model):
    op_id: UUID
    entity_type: Literal['category', 'memo', 'focus', 'category_order', 'deletion_request']
    entity_id: UUID
    kind: str = Field(min_length=1, max_length=40)
    base_version: int = Field(ge=0, le=9223372036854775806, strict=True)
    data: dict


class Attachment(Model):
    id: UUID
    kind: Literal['photo', 'file']
    name: str = Field(min_length=1, max_length=255)
    mime_type: str = Field(min_length=1, max_length=150, pattern=r'^[a-zA-Z0-9!#$&^_.+-]+/[a-zA-Z0-9!#$&^_.+-]+$')
    size_bytes: int = Field(ge=0, le=20*1024*1024, strict=True)
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')

    @field_validator('name', mode='before')
    @classmethod
    def filename(cls, value):
        if isinstance(value,str):
            value=value.strip()
            if any(ord(c)<32 for c in value):
                raise ValueError('Invalid filename')
        return value


class MemoData(Model):
    category_id: UUID | None
    kind: Literal['memo','schedule'] = 'memo'
    schedule: 'Schedule | None' = None
    reminders: list['Reminder'] = Field(default_factory=list,max_length=20)
    recurrence: 'Recurrence | None' = None
    title: str = Field(min_length=1,max_length=200)
    body_markdown: str = ''
    attachments: list[Attachment] = Field(default_factory=list,max_length=100)

    @field_validator('title', mode='before')
    @classmethod
    def trim_title(cls,value):
        return value.strip() if isinstance(value,str) else value

    @field_validator('body_markdown')
    @classmethod
    def body_limit(cls,value):
        if len(value.encode('utf-8'))>200*1024:
            raise ValueError('Body exceeds 200 KiB')
        return value


class CategoryData(Model):
    name: str = Field(min_length=1,max_length=40)

    @field_validator('name',mode='before')
    @classmethod
    def trim_name(cls,value):
        return value.strip() if isinstance(value,str) else value


class CompleteData(Model):
    completed: bool = Field(strict=True)


class Schedule(Model):
    start_date: date
    end_date: date | None = None
    start_time: str | None = Field(default=None,pattern=r'^(?:[01][0-9]|2[0-3]):(?:00|30)$')
    end_time: str | None = Field(default=None,pattern=r'^(?:[01][0-9]|2[0-3]):(?:00|30)$')

    @model_validator(mode='after')
    def interval(self):
        if not 1901<=self.start_date.year<=2099 or self.end_date and not 1901<=self.end_date.year<=2099:raise ValueError('Supported schedule years are 1901..2099')
        if self.start_time is None and self.end_time is not None:raise ValueError('All-day cannot have an end time')
        if self.start_time is not None and self.end_date is not None and self.end_time is None:raise ValueError('Timed end needs a time')
        if self.end_time is not None and self.end_date is None:self.end_date=self.start_date
        if self.end_date and (self.end_date<self.start_date or self.end_time and (self.end_date,self.end_time)<=(self.start_date,self.start_time)):raise ValueError('Invalid end')
        return self


class Reminder(Model):
    id: UUID
    mode: Literal['absolute','relative'] = 'absolute'
    at: datetime | None = None
    offset_minutes: int = Field(default=0,ge=0,le=525600,strict=True)
    recipient_ids: list[UUID] = Field(default_factory=list,max_length=100)
    all_members: bool = Field(default=False,strict=True)

    @model_validator(mode='after')
    def rule(self):
        if self.mode=='absolute' and self.at is None or self.mode=='relative' and self.at is not None:raise ValueError('Invalid reminder time')
        if len(set(self.recipient_ids))!=len(self.recipient_ids):raise ValueError('Duplicate recipient')
        return self


class Recurrence(Model):
    frequency: Literal['daily','weekly','monthly','yearly']
    interval: int = Field(default=1,ge=1,le=99,strict=True)
    calendar: Literal['solar','lunar'] = 'solar'
    start_date: date
    until: date | None = None
    lunar_month: int | None = Field(default=None,ge=1,le=12,strict=True)
    lunar_day: int | None = Field(default=None,ge=1,le=30,strict=True)
    lunar_leap: bool = False

    @model_validator(mode='after')
    def valid(self):
        if self.until and self.until<self.start_date:raise ValueError('Invalid recurrence end')
        if self.calendar=='lunar' and (self.frequency!='yearly' or self.lunar_month is None or self.lunar_day is None):raise ValueError('Lunar requires yearly month and day')
        if not 1901<=self.start_date.year<=2099 or self.until and self.until.year>2099:raise ValueError('Supported recurrence years are 1901..2099')
        return self


class ArchiveData(Model):
    archived: bool = Field(strict=True)


class Empty(Model):
    pass


class CategoryDelete(Model):
    target_category_id: UUID | None = None


class FocusTarget(Model):
    kind: Literal['memo','plan_task']
    id: UUID


class FocusData(Model):
    targets: list[FocusTarget] = Field(max_length=1000)


class CategoryOrderData(Model):
    category_ids: list[UUID] = Field(max_length=1000)


class DeletionData(Model):
    memo_id: UUID
    target_version: int = Field(ge=1,strict=True)
    reason: str = Field(default='',max_length=2000)


class DecideData(Model):
    accepted: bool = Field(strict=True)


MemoData.model_rebuild()
