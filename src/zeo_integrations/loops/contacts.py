"""Separately reviewed contact surface. Updates are provider upserts."""

from .models import Contact, ContactSaved, ContactUpdate, Suppression
from .transport import InvalidResponseError, _HttpClient


class LoopsContactsClient(_HttpClient):
    def _save_contact(self, operation: str, contact: ContactUpdate) -> ContactSaved:
        if not isinstance(contact, ContactUpdate):
            raise TypeError("contact must be a ContactUpdate")
        body = contact.model_dump(
            by_alias=True, exclude_none=True, exclude={"properties"}
        )
        body.update(contact.properties)
        return self._request(
            "POST" if operation == "create" else "PUT",
            f"contacts/{operation}",
            ContactSaved,
            body=body,
        )

    def create_contact(self, contact: ContactUpdate) -> ContactSaved:
        return self._save_contact("create", contact)

    def update_contact(self, contact: ContactUpdate) -> ContactSaved:
        """Provider upsert; caller owns consent and any subscribed=True decision."""
        return self._save_contact("update", contact)

    def find_contact(self, email: str) -> list[Contact]:
        identity = ContactUpdate(email=email)
        result = self._request(
            "GET", "contacts/find", list[Contact], params={"email": identity.email}
        )
        if any(contact.email != email for contact in result):
            raise InvalidResponseError("contact identity mismatch")
        return result

    def check_suppression(self, email: str) -> Suppression:
        identity = ContactUpdate(email=email)
        result = self._request(
            "GET", "contacts/suppression", Suppression, params={"email": identity.email}
        )
        if result.contact.email != email:
            raise InvalidResponseError("contact identity mismatch")
        return result
