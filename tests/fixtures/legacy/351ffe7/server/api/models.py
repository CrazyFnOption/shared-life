"""Strict request schemas for ledger operations and online approvals."""

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

Money = Annotated[StrictInt, Field(ge=1, le=999999999)]
Version = Annotated[StrictInt, Field(ge=1, le=9223372036854775806)]


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(RequestModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class Category(RequestModel):
    scope: Literal["shared", "private"]
    name: str = Field(min_length=1, max_length=24)

    @field_validator("name", mode="before")
    @classmethod
    def trim_name(cls, value):
        return value.strip() if isinstance(value, str) else value


class AmountTime(RequestModel):
    amount_cents: Money
    occurred_at: datetime

    @field_validator("occurred_at", mode="before")
    @classmethod
    def iso_time(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("occurred_at must be an ISO8601 string")
        return value

    @field_validator("occurred_at")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone")
        try:
            return value.astimezone(UTC)
        except OverflowError as error:
            raise ValueError("occurred_at is outside supported dates") from error


class ExpenseFields(AmountTime):
    purpose: str = Field(min_length=1, max_length=500)
    location: str = Field(max_length=200)
    category: str = Field(default="", max_length=24)

    @field_validator("category", mode="before")
    @classmethod
    def trim_category(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("purpose")
    @classmethod
    def nonblank_purpose(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("purpose must not be blank")
        return value


class Review(RequestModel):
    rating: Annotated[StrictInt, Field(ge=1, le=5)] | None
    rating_reason: str = Field(max_length=500)

    @field_validator("rating_reason", mode="before")
    @classmethod
    def trim_reason(cls, value):
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def paired_review(self):
        if (self.rating is None) != (self.rating_reason == ""):
            raise ValueError("rating and a nonblank reason must be saved together")
        return self


class EditableExpense(ExpenseFields):
    rating: Annotated[StrictInt, Field(ge=1, le=5)] | None = None
    rating_reason: str = Field(default="", max_length=500)

    @field_validator("rating_reason", mode="before")
    @classmethod
    def trim_reason(cls, value):
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def optional_review(self):
        supplied = {"rating", "rating_reason"} & self.model_fields_set
        if supplied and supplied != {"rating", "rating_reason"}:
            raise ValueError("rating and rating_reason must both be provided")
        if supplied:
            Review(rating=self.rating, rating_reason=self.rating_reason)
        return self


class NewExpense(EditableExpense):
    scope: Literal["shared", "private"]


class RefundData(AmountTime):
    refund_id: UUID
    reason: str = Field(max_length=500)


class RefundTarget(RequestModel):
    refund_id: UUID


class ScopeData(RequestModel):
    scope: Literal["shared", "private"]


class EmptyData(RequestModel):
    pass


class OperationBase(RequestModel):
    operation_id: UUID
    expense_id: UUID
    base_version: Version


class CreateOperation(OperationBase):
    kind: Literal["create"]
    base_version: Annotated[StrictInt, Field(ge=0, le=0)]
    data: NewExpense


class UpdateOperation(OperationBase):
    kind: Literal["update"]
    data: EditableExpense


class RefundOperation(OperationBase):
    kind: Literal["refund_create", "refund_update"]
    data: RefundData


class RefundVoidOperation(OperationBase):
    kind: Literal["refund_void"]
    data: RefundTarget


class ReviewOperation(OperationBase):
    kind: Literal["review"]
    data: Review


class ScopeOperation(OperationBase):
    kind: Literal["scope"]
    data: ScopeData


class VoidOperation(OperationBase):
    kind: Literal["void"]
    data: EmptyData


Operation = Annotated[
    CreateOperation | UpdateOperation | RefundOperation | RefundVoidOperation |
    ReviewOperation | ScopeOperation | VoidOperation, Field(discriminator="kind")
]


class ChangeRequestBase(RequestModel):
    id: UUID
    expense_id: UUID
    base_version: Version
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason", mode="before")
    @classmethod
    def trim_reason(cls, value):
        return value.strip() if isinstance(value, str) else value


class UpdateRequest(ChangeRequestBase):
    kind: Literal["update"]
    data: ExpenseFields


class RefundRequest(ChangeRequestBase):
    kind: Literal["refund_create", "refund_update"]
    data: RefundData


class RefundVoidRequest(ChangeRequestBase):
    kind: Literal["refund_void"]
    data: RefundTarget


class VoidRequest(ChangeRequestBase):
    kind: Literal["void"]
    data: EmptyData


ChangeRequest = Annotated[UpdateRequest | RefundRequest | RefundVoidRequest | VoidRequest, Field(discriminator="kind")]


class Decision(RequestModel):
    decision_id: UUID
    decision: Literal["accept", "reject"]
