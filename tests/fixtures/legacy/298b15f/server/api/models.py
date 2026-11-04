"""Strict request schemas for ledger operations and online approvals."""

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictInt, field_validator, model_validator

Money = Annotated[StrictInt, Field(ge=1, le=999999999)]
Version = Annotated[StrictInt, Field(ge=1, le=9223372036854775806)]


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(RequestModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class ChangePassword(RequestModel):
    old_password: SecretStr
    new_password: SecretStr


class Category(RequestModel):
    scope: Literal["shared", "private"]
    name: str = Field(min_length=1, max_length=24)
    restore_deleted: bool = False

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
    rating_reason: str = Field(default="", max_length=500)

    @field_validator("rating_reason", mode="before")
    @classmethod
    def trim_reason(cls, value):
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def paired_review(self):
        if self.rating is None and self.rating_reason != "":
            raise ValueError("a cleared rating cannot have a reason")
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
        if supplied == {"rating_reason"}:
            raise ValueError("rating is required when changing its reason")
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
    reason: str = Field(default="", max_length=500)

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
    rejection_reason: str = Field(default="", max_length=500)

    @field_validator("rejection_reason", mode="before")
    @classmethod
    def trim_rejection_reason(cls, value):
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def reject_reason_only(self):
        if self.decision == "accept" and self.rejection_reason:
            raise ValueError("Only a rejection may include rejection_reason")
        return self

    def hash_payload(self):
        payload = self.model_dump(mode="json")
        # Preserve hashes of decisions sent before rejection reasons existed.
        if not self.rejection_reason:
            payload.pop("rejection_reason")
        return payload


class CategoryChangePreview(RequestModel):
    kind: Literal['rename','delete','reclassify']
    merge: bool = False
    scope: Literal['shared','private']
    source_name: str | None = Field(default=None,min_length=1,max_length=24)
    target_name: str = Field(default='',max_length=24)
    expense_ids: list[UUID] = Field(default_factory=list,max_length=1000)

    @field_validator('source_name','target_name',mode='before')
    @classmethod
    def trim_category_names(cls,value):
        return value.strip() if isinstance(value,str) else value

    @model_validator(mode='after')
    def category_action(self):
        if self.merge and self.kind != 'rename':
            raise ValueError('merge is only supported for rename')
        if self.kind in {'rename','delete'}:
            if not self.source_name or self.expense_ids:
                raise ValueError('source_name required; expense_ids only for reclassify')
            if self.kind == 'rename' and (not self.target_name or self.source_name == self.target_name):
                raise ValueError('rename requires a different nonempty target_name')
            if self.kind == 'delete' and self.target_name:
                raise ValueError('delete target must be uncategorized')
        elif self.source_name is not None or not self.expense_ids or len(set(self.expense_ids)) != len(self.expense_ids):
            raise ValueError('reclassify requires distinct expense_ids and no source_name')
        return self


class CategoryChangeSubmit(RequestModel):
    id: UUID
    preview_id: UUID
    reason: str = Field(default='',max_length=500)

    @field_validator('reason',mode='before')
    @classmethod
    def trim_reason(cls,value):
        return value.strip() if isinstance(value,str) else value


class Register(RequestModel):
    username: str = Field(pattern=r"^[A-Za-z0-9._-]{3,40}$")
    display_name: str = Field(min_length=1, max_length=40)
    password: SecretStr

    @field_validator('display_name', mode='before')
    @classmethod
    def trim_display_name(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator('password')
    @classmethod
    def password_length(cls, value):
        if not 8 <= len(value.get_secret_value()) <= 128:
            raise ValueError('invalid password length')
        return value


class CreateFamily(RequestModel):
    request_id: UUID
    name: str = Field(min_length=1, max_length=40)

    @field_validator('name', mode='before')
    @classmethod
    def trim_name(cls, value):
        return value.strip() if isinstance(value, str) else value


class InviteFamily(RequestModel):
    request_id: UUID
    username: str = Field(pattern=r"^[A-Za-z0-9._-]{3,40}$")


class TransferFamilyOwner(RequestModel):
    user_id: UUID
