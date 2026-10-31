"""Narrow Loops mailing-list projection for a custody-bound Connect operation.

Copyright 2026 Zero Employee Organizations. SPDX-License-Identifier: MIT
Registration, account binding, credential injection and authority are host-owned.
"""

import re
from collections.abc import Callable

from .client import LoopsClient
from .transport import InvalidResponseError


class ConnectLoopsMailingListsReader:
    """List metadata only; no contacts, campaigns, send or scheduling facade.

    The host supplies one per-use client and the per-connection shared pacer.
    This adapter registers no operation and discovers no credentials.
    """

    def __init__(self, client: LoopsClient, *, wait: Callable[[], None]):
        self._client, self._wait = client, wait

    def close(self) -> None:
        self._client.close()

    def list_mailing_lists(self) -> list[dict]:
        self._wait()
        items = self._client.list_mailing_lists()
        if len(items) > 200 or any(
            re.fullmatch(r"[A-Za-z0-9_-]{1,128}", item.id) is None for item in items
        ):
            raise InvalidResponseError("mailing-list projection refused")
        return [item.model_dump(include={"id", "name", "is_public"}) for item in items]
