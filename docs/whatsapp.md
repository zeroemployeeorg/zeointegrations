# WhatsApp with explicit credentials

The library performs provider operations only. Applications keep credentials in
an execution service, bind recipients to a grant, and retain
MessageSid deduplication and order/outbox state outside the model. Never put
provider credentials, phone numbers, or request bodies in logs.

## WhatsApp

`zeo_integrations.twilio.WhatsAppClient` is synchronous. Construct it with
`TwilioSendCredentials(account_sid, api_key_sid, api_key_secret)` and a dedicated
`httpx.Client` configured with `trust_env=False`, bounded timeouts, and no retry
transport or content logging. The caller owns and closes that client. Only the
fixed Twilio HTTPS Messages endpoint is used, with redirects disabled. Sending
uses a revocable API key, never the account Auth Token. Credential discovery,
file reads, environment fallback, and account provisioning are absent.

- `send_text(from_number, to_number, text)` sends free-form text within the
  customer service window. Both numbers use `whatsapp:+...` E.164 addresses.
- `send_template(from_number, to_number, content_sid, variables)` uses an approved
  `HX...` Content template with numbered string variables. It does not silently
  fall back to free-form text. The caller must verify template approval,
  recipient consent, sandbox membership, and applicable window rules.
- A `MessageReceipt(message_sid, status)` confirms provider acceptance only.
  It does not prove handset delivery, that the customer window was open, or
  completion of an order. A queued message may fail asynchronously, including
  with 63016; the caller must learn final status from a separate authenticated
  message lookup or status callback. This transport does not provide that lookup.
- `TwilioRefused` identifies a definite HTTP refusal. Code 63016 yields the
  closed reason `outside_customer_window`; native error text is discarded.
- `TwilioUnknownOutcome` means a write may have executed, including a timeout,
  HTTP 408/5xx, redirect, or malformed acceptance. Persist UNKNOWN and reconcile
  it. Do not resend, switch transport, or declare failure automatically.

`inbound_list(to_number, date_sent_after, now=..., max_pages=10)` returns validated
`InboundMessage` records, ordered by DateSent and MessageSid. Both timestamps
must be timezone-aware and the window must be at most 24 hours. The server-side
DateSent lower bound is day-granular; local filtering enforces the exact lower
bound. Requests always constrain the sandbox recipient and page size to 50.
Only a validated continuation token is used, keeping the host and filters fixed.
The native lower-bound query key is `DateSent>` with a plain GMT date value;
`DateSent%3E` in continuation URLs is decoded before comparison.
A failed, malformed, or incomplete listing raises `TwilioListError`; callers
must never turn it into an empty successful poll or advance their cursor.
The closed `reason` is `inbound_window_truncated` at the page cap,
`inbound_message_pending` if a matching inbound message is still receiving,
or `inbound_list_failed` for other malformed/failed listings. No partial rows
are returned. A receiving message holds the whole window until it settles;
retain the cursor and retry the read later. Outbound or unexpected-recipient
rows fail the list rather than being silently skipped.

HTTP client timeouts apply per network operation, not to the whole multi-page
call. The caller must impose a total-operation deadline on its execution
worker as well as bounded connect/read timeouts; ten pages do not imply ten
independent time budgets. The library performs no automatic read or send retry.

Retain MessageSid deduplication across polls and restarts. Use overlapping poll
windows to accommodate delayed visibility. The transport cannot prove that all
messages have become visible at the provider, and it never claims exactly-once
business execution. Media counts are metadata; no media is downloaded.

For a future form webhook, `parse_whatsapp_webhook(canonical_url, raw_form,
signature, credentials)` validates the complete URL-encoded form before parsing
its WhatsApp fields. Pass `TwilioWebhookCredentials(account_sid, auth_token)`
only inside the validating service; Auth Tokens are not send credentials. The
URL must be the exact configured external HTTPS URL, including its query.
Never derive it from Host or forwarding headers. Duplicate fields, invalid
encoding, mismatched account/SIDs, and outbound status callbacks are rejected.
JSON webhooks are unsupported. HMAC validation supplies neither a signed
timestamp nor replay protection: deduplicate MessageSid and attach a trusted
ingress timestamp in the receiving application.


Protocol references: [Twilio Messages](https://www.twilio.com/docs/messaging/api/message-resource) and [Twilio signature validation](https://www.twilio.com/docs/usage/security).
