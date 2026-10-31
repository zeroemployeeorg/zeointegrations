"""Typed draft-only surface; contact operations have a separate client."""

from .models import (
    Account,
    AudienceFilter,
    AudienceUpdate,
    Campaign,
    EmailMessage,
    EmailUpdate,
    MailingList,
    ThemePage,
    UploadedAsset,
    UploadTicket,
)
from .transport import InvalidResponseError, _asset_url, _HttpClient, _id


class LoopsClient(_HttpClient):
    def test_api_key(self) -> Account:
        return self._request("GET", "api-key", Account)

    def create_campaign(self, name: str) -> Campaign:
        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            raise ValueError("campaign name is required and bounded")
        result = self._request("POST", "campaigns", Campaign, body={"name": name})
        if result.status != "Draft":
            raise InvalidResponseError("new campaign is not a draft")
        return result

    def get_campaign(self, campaign_id: str) -> Campaign:
        result = self._request("GET", f"campaigns/{_id(campaign_id)}", Campaign)
        if result.id != campaign_id:
            raise InvalidResponseError("campaign identity mismatch")
        return result

    def get_email_message(self, email_message_id: str) -> EmailMessage:
        result = self._request(
            "GET", f"email-messages/{_id(email_message_id)}", EmailMessage
        )
        if result.id != email_message_id:
            raise InvalidResponseError("email message identity mismatch")
        return result

    def update_email_message(
        self,
        email_message_id: str,
        expected_revision_id: str,
        subject: str,
        preview_text: str,
        from_name: str,
        from_email: str,
        lmx: str,
    ) -> EmailMessage:
        update = EmailUpdate(
            expected_revision_id=expected_revision_id,
            subject=subject,
            preview_text=preview_text,
            from_name=from_name,
            from_email=from_email,
            lmx=lmx,
        )
        result = self._request(
            "POST",
            f"email-messages/{_id(email_message_id)}",
            EmailMessage,
            body=update.model_dump(by_alias=True),
        )
        if result.id != email_message_id:
            raise InvalidResponseError("email message identity mismatch")
        return result

    def update_campaign_audience(
        self,
        campaign_id: str,
        *,
        mailing_list_id: str | None = None,
        audience_segment_id: str | None = None,
        audience_filter: AudienceFilter | None = None,
    ) -> Campaign:
        update = AudienceUpdate(
            mailing_list_id=mailing_list_id,
            audience_segment_id=audience_segment_id,
            audience_filter=audience_filter,
        )
        result = self._request(
            "POST",
            f"campaigns/{_id(campaign_id)}",
            Campaign,
            body={
                key: value
                for key, value in update.model_dump(by_alias=True).items()
                if value is not None
            },
        )
        if result.id != campaign_id:
            raise InvalidResponseError("campaign identity mismatch")
        if result.status != "Draft":
            raise InvalidResponseError("updated campaign is not a draft")
        return result

    def list_themes(self, *, cursor: str | None = None) -> ThemePage:
        return self._request(
            "GET", "themes", ThemePage, params={"cursor": cursor} if cursor else None
        )

    def list_mailing_lists(self) -> list[MailingList]:
        return self._request("GET", "lists", list[MailingList])

    def create_upload(self, content_type: str, content_length: int) -> UploadTicket:
        if content_type not in {"image/jpeg", "image/png", "image/gif", "image/webp"}:
            raise ValueError("unsupported upload content type")
        if type(content_length) is not int or not 0 < content_length <= 4_000_000:
            raise ValueError("upload size must be within 4000000 bytes")
        # Returns the ticket only. Never send an API credential to its signed URL.
        result = self._request(
            "POST",
            "uploads",
            UploadTicket,
            body={
                "contentType": content_type,
                "contentLength": content_length,
            },
        )
        _asset_url(result.presigned_url, asset_id=result.email_asset_id)
        return result

    def complete_upload(self, email_asset_id: str) -> UploadedAsset:
        result = self._request(
            "POST", f"uploads/{_id(email_asset_id)}/complete", UploadedAsset
        )
        _asset_url(result.final_url)
        if result.email_asset_id != email_asset_id:
            raise InvalidResponseError("upload identity mismatch")
        return result
