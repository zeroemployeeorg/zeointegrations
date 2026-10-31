"""Bounded projections of Loops OpenAPI 1.22.6; no custody or executor ABI."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator


def _camel(name: str) -> str:
    first, *rest = name.split("_")
    return first + "".join(part.title() for part in rest)


class Result(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        alias_generator=_camel,
        populate_by_name=True,
        allow_inf_nan=False,
    )


class Request(Result):
    model_config = ConfigDict(extra="forbid")


class Account(Result):
    success: Literal[True]
    team_name: str = Field(min_length=1, max_length=1024)


class Campaign(Result):
    id: str
    url: str
    name: str
    status: Literal["Draft", "Scheduled", "Sending", "Sent"]
    created_at: str
    updated_at: str
    email_message_id: str | None
    # Present on creation, omitted by the get/update response schema.
    email_message_content_revision_id: str | None = None
    campaign_group_id: str | None
    mailing_list_id: str | None
    audience_segment_id: str | None


class MessageWarning(Result):
    rule: str
    severity: Literal["warning"]
    message: str
    path: str | None = None


class EmailMessage(Result):
    id: str
    campaign_id: str | None = None
    transactional_id: str | None = None
    subject: str
    preview_text: str
    from_name: str
    from_email: str
    reply_to_email: str
    email_format: Literal["styled", "plain"]
    lmx: str
    content_revision_id: str | None
    updated_at: str
    warnings: list[MessageWarning] = Field(default_factory=list)


class EmailUpdate(Request):
    expected_revision_id: str = Field(
        min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$"
    )
    subject: str = Field(max_length=998)
    preview_text: str = Field(max_length=998)
    from_name: str = Field(max_length=998)
    from_email: str = Field(min_length=1, max_length=256)
    lmx: str = Field(min_length=1, max_length=60000)

    @model_validator(mode="after")
    def sender_is_local_part(self):
        if "@" in self.from_email or any(c.isspace() for c in self.from_email):
            raise ValueError("from_email must be the sender local-part")
        if len(self.lmx.encode("utf-8")) > 60000:
            raise ValueError("LMX exceeds 60000 UTF-8 bytes")
        return self


class DateRange(Request):
    from_: str = Field(alias="from")
    to: str


class PropertyCondition(Request):
    type: Literal["property"] = "property"
    key: str = Field(min_length=1, max_length=256)
    operator: Literal[
        "any",
        "contains",
        "notContains",
        "equals",
        "notEquals",
        "greaterThan",
        "lessThan",
        "isTrue",
        "isFalse",
        "empty",
        "notEmpty",
        "dateEmpty",
        "dateNotEmpty",
        "after",
        "before",
        "between",
    ]
    value: str | int | float | DateRange | None = None

    @model_serializer(mode="wrap")
    def omit_absent_value(self, handler):
        result = handler(self)
        if self.value is None:
            result.pop("value", None)
        return result

    @model_validator(mode="after")
    def value_matches_operator(self):
        no_value = {
            "any",
            "isTrue",
            "isFalse",
            "empty",
            "notEmpty",
            "dateEmpty",
            "dateNotEmpty",
        }
        if self.operator in no_value and self.value is not None:
            raise ValueError("operator takes no value")
        if self.operator not in no_value and self.value is None:
            raise ValueError("operator needs a value")
        if (self.operator == "between") != isinstance(self.value, DateRange):
            raise ValueError("between requires a date range")
        return self


class OptInCondition(Request):
    type: Literal["optIn"] = "optIn"
    status: Literal["accepted", "pending", "rejected"] | None


class ActivityCondition(Request):
    type: Literal["activity"] = "activity"
    action: Literal["sent", "opened", "clicked"]
    negate: bool
    target: Literal["campaign", "workflow", "workflowEmail"]
    id: str = Field(min_length=1, max_length=256)


Condition = Annotated[
    PropertyCondition | OptInCondition | ActivityCondition, Field(discriminator="type")
]


class AudienceFilter(Request):
    match: Literal["all", "any"]
    conditions: list[Condition] = Field(min_length=1, max_length=20)


class AudienceUpdate(Request):
    mailing_list_id: str | None = Field(default=None, min_length=1, max_length=256)
    audience_segment_id: str | None = Field(default=None, min_length=1, max_length=256)
    audience_filter: AudienceFilter | None = None

    @model_validator(mode="after")
    def has_target(self):
        if not any(
            value is not None
            for value in (
                self.mailing_list_id,
                self.audience_segment_id,
                self.audience_filter,
            )
        ):
            raise ValueError("an audience target is required")
        return self


class Pagination(Result):
    total_results: int
    returned_results: int
    per_page: int
    total_pages: int
    next_cursor: str | None
    next_page: str | None


class Theme(Result):
    id: str
    name: str
    styles: dict[str, str | int | float]
    is_default: bool
    created_at: str
    updated_at: str


class ThemePage(Result):
    pagination: Pagination
    data: list[Theme]


class MailingList(Result):
    id: str
    name: str
    description: str | None
    is_public: bool


class UploadTicket(Result):
    email_asset_id: str
    presigned_url: str = Field(repr=False)


class UploadedAsset(Result):
    email_asset_id: str
    final_url: str


class ContactUpdate(Request):
    email: str = Field(min_length=3, max_length=320)
    first_name: str | None = Field(default=None, max_length=1024)
    last_name: str | None = Field(default=None, max_length=1024)
    source: str | None = Field(default=None, max_length=1024)
    user_group: str | None = Field(default=None, max_length=1024)
    user_id: str | None = Field(default=None, max_length=256)
    subscribed: bool | None = None
    mailing_lists: dict[str, bool] | None = None
    # Serialized as top-level custom contact properties, not this envelope key.
    properties: dict[str, str | int | float | bool] = Field(default_factory=dict)

    @model_validator(mode="after")
    def custom_properties_do_not_override_fields(self):
        reserved = {_camel(name) for name in type(self).model_fields}
        reserved |= set(type(self).model_fields) | {"scheduling"}
        if any(not key or key in reserved for key in self.properties):
            raise ValueError("custom property conflicts with a contact field")
        return self


class ContactSaved(Result):
    success: Literal[True]
    id: str


class Contact(Result):
    id: str
    email: str
    subscribed: bool
    first_name: str | None = None
    last_name: str | None = None
    source: str | None = None
    user_group: str | None = None
    user_id: str | None = None
    mailing_lists: dict[str, bool] = Field(default_factory=dict)
    opt_in_status: Literal["accepted", "pending", "rejected"] | None = None


class ContactIdentity(Result):
    id: str
    email: str
    user_id: str | None


class RemovalQuota(Result):
    limit: int
    remaining: int


class Suppression(Result):
    contact: ContactIdentity
    is_suppressed: bool
    removal_quota: RemovalQuota
