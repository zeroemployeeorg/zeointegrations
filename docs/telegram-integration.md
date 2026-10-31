# Shared Telegram clients

This source increment implements bounded receive/ACK through TelegramClient
and send/inline/callback-answer through TelegramSender, using the existing
credentialless hosted transport. Synthetic verification covers the client
contract and package compatibility. It is not a bot receiver, running poll loop,
installed Broker route, live grant, application ingress or notification acceptance.

The injected Core HostedConnectionClient owns pairing and the authenticated
transport; Connect alone owns bot credentials and provider URLs. Core's pinned
0.12.0 request has no expect field: the client supplies only connection_id,
operation_id, arguments and idempotency_key. It never manufactures tenant,
origin, HTTP method, header, offset or token. Connector revision comes from
the bound connection. The READ response's Broker-asserted bot_id identifies the
bot; update sender IDs do not. No Core contract or dependency is changed.

The hosted contract requires receipt.condition, null when no Broker-specific
condition applies. The closed replay token is transient_replay. Unknown tokens,
code mismatches and missing required condition fail closed. An absent key is
not treated as null. receipt.replayed is a boolean.
Error messages and provider_detail never drive control. Generic kernel refusal
gains no invented not_permitted label; null is not a grant or successful effect.
No unknown top-level member is used: the Broker-pinned Core model rejects it.

READ arguments contain max1..100 only. The Broker owns getMe/webhook checks,
lease, pending/confirmed provider offsets and redacted observation history.
The client accepts only bounded message/callback batches and immutable local
copies. Unknown kinds or schema drift prevent ACK; no events are silently
discarded to move the queue. Empty batch is distinct from unavailable.
The Broker supplies a nonnegative reset epoch in READ. It is carried unchanged
to the verifier; only Broker advances it on a recorded reset. A lease fence
never changes business identity. Numeric IDs do not define delivery order:
the client preserves Broker batch order and rejects duplicate IDs within it.
Broker records unsupported old queued types as dropped without raw content;
an issued cursor may therefore cover a batch with no delivered updates.
The trusted verifier must still confirm previous deliveries/all consumers
before ACK; Broker must prove its drops before advancing that boundary.
Unsupported raw types leaking through to this client remain a schema refusal,
not a client-authorized drop.

No automatic READ, ACK, backoff or write retry is performed.
A client instance is synchronous; its owning dispatcher serializes calls.
It is not a thread-safe shared receiver or lease service.

ACK accepts only the actual batch issued to this client and only after the
injected TRUSTED DurablePrefixVerifier confirms all configured consumers
durably handled the entire delivered prefix, including prior deliveries.
That verifier is an adapter to the consumer's real authenticated durable
store, not a boolean from Telegram or App input. Stable (bot_id,update_id)
dedup on (bot_id, epoch, update_id), changed-payload conflicts within an
epoch and consumer routing belong to that store.
The verifier receives the immutable CURRENT batch with its payloads, so a stored
ID alone cannot be mistaken for a disposition of changed redelivered content.
The library cannot independently prove a caller supplied a trustworthy adapter.
Only the opaque issued cursor is echoed. There is no integer provider offset.
After an ambiguous invocation, the original batch/key must reconcile unless
the Broker explicitly opens the linked NEW-prefix recovery below. The client
retains only volatile pending context, not a durable outbox. The owning
consumer store preserves original intents and records returned execution
links; neither a READ nor a new client object proves the old outcome.

Deadlines are absolute monotonic caller deadlines, checked before and after
invoke. These checks CANNOT interrupt a blocking hosted transport. Its owning
adapter must enforce the original deadline and response-size limit itself;
there is no invented timeout request field. The local1MiB response ceiling
is client policy, not a provider limit or pre-download allocation guarantee.
No installed transport timing qualification is claimed.

[Telegram's primary API](https://core.telegram.org/bots/api#getupdates) explains
why a higher getUpdates offset acknowledges earlier updates. READ must use only
the Broker's last successfully confirmed durable ACK offset, never pending
intent. Negative offsets can forget queued updates. ACK is a separate guarded
effect; incidental updates in its answer are discarded by Broker, not passed
to this client as processed events. Retention age is a lower-bound/proxy only;
lost_to_retention means possible loss without a fabricated count.

Verification uses synthetic transports and covers the supported Python floor,
installed package boundaries, builds and nested packages. Actual send, receive
and inline consumer use requires separate integration and account evidence.
Synthetic success does not establish live delivery or an authorised grant.

## New provider-page constraints and unresolved Broker guard

The provider's API has several constraints:
a random update ID can follow a quiet week, allowed_updates is sticky when
omitted, and error_code is not a stable discriminator. Broker owns provider
offsets, fixed allowed_updates on READ and ACK, reset/drop receipts and
HTTP/ok/parameters.retry_after classification. This client does none of that
provider traffic and infers no concurrent-poller409 behavior.

The five-day stale READ policy alone does not guard an OLD ACK cursor. After a
quiet week an old high cursor could confirm new lower IDs through the effect.
The Broker must guard old/stale ACK before provider dispatch, including
ambiguous resend, and do not refresh freshness from an ACK without checking
the reset boundary. Same-key identity alone is not safe reconciliation after
a provider ID reset. No client reset/retry or old-effect success is invented.
Broker guard and reset recovery remain unbuilt/require owner qualification.

## Explicit linked stale recovery

cursor_stale and recovery_required are Hosted FAILED_SAFE with
REQUEST_REFUSED and typed receipt.condition. receipt.pending_ack contains
state unresolved_stale and the original execution_id, not a new Core state.
A fresh READ may return receipt.recovery {open:true,supersedes:E1}. Neither
metadata object is evidence that E1 succeeded or failed.

Only acknowledge_recovery on the actual issued fresh batch starts an explicit
NEW prefix operation under caller-supplied NEW keyK2 and the Broker-asserted
supersedes_execution_id=E1. It still requires the current durable-prefix
verifier. It does not reuse K1 with another body, generate a key, loop, or
bypass Broker open-intent/freshness/epoch checks. Ordinary acknowledge rejects
an unlinked batch when recovery is required. Unknown/malformed/mismatched
metadata, caller-forged batches, reused known keys and wrong intent references
fail closed.

A lost E2 reply retains C2/K2 AND the same supersedes member for same-key resend;
it does not create K3. Original parent context stays until E2 is confirmed. Once the Broker has
declared an attempt unresolved_stale, local resend of that old cursor/key is
blocked; only a fresh Broker-granted linked prefix can advance.
The acknowledgement returns E2 plus superseded_execution_ids as LINKS ONLY;
it never resolves or overwrites E1. The local chain is bounded at16 prior
links; exceeding that requires owning recovery, not an automatic context purge.

After restart, authenticated Broker recovery metadata can supply the E1
reference for a newly issued batch without reconstructing E1's body or outcome.
The actual durable consumer still owns all original intent/key/history and
must establish the full handled prefix; this library is not a crash-safe
outbox or a grant from caller-provided identifiers. A fresh key is allowed
only for this explicitly linked new operation; ordinary unknown writes retain
their original invocation identity. No live recovery is established by synthetic
verification.


## Send, inline buttons and neutral callback answers

Sending and callback answers require the existing per-call browser approval.
There is no standing-send grant, and a receive ACK-only grant does not authorise
sending or answering a callback. The Broker must implement and register these
operations and apply current authority at each dispatch; this source does not
install or activate them. Existing hosted request and response shapes are used;
no dependency or package configuration change is included.

TelegramSender is separate from the receiver so a send-only consumer need not
poll or fabricate a durable receive verifier. It requires expected_bot_id from
TRUSTED configuration or the authenticated connection listing's external_identity,
resolved by the owning adapter before construction. It never obtains the expected
pin from the response it checks. Exact listing propagation still needs Broker
qualification; there is no new HTTP/metadata fetch or expect field in this client.

Example variables below come from the owning authenticated consumer and its
durable intent store; neither this snippet nor a button grants execution:

    sender = TelegramSender(
        bound_hosted_client,
        connection_id=trusted_connection_id,
        expected_bot_id=trusted_bot_id,
    )
    result = sender.send_message(
        allowed_numeric_chat_id,
        notice_text,
        inline_keyboard=((TelegramInlineButton("Open", opaque_action_ref),),),
        idempotency_key=durably_recorded_original_key,
        deadline=original_monotonic_deadline,
    )

Fixed operations are telegram.message.send and telegram.callback.answer.
send_message builds only chat_id/text and optional ordered inline_keyboard.
answer_callback builds callback_query_id, optional text and show_alert.
No caller method/URL/headers/parse mode/entities, files, pay buttons, web apps,
business identity or provider fallback exists. Broker owns the operator allowed
chat list and refuses a changed/revoked recipient even after approval.

Message text is 1..4096 UTF-8 bytes, answer text 0..200 UTF-8 bytes, with normal
line breaks/tabs allowed and invalid Unicode/control content refused. These are
agreed conservative client byte policies, stricter than vendor character limits.
Labels are 1..128 UTF-8 bytes, callback data 1..64 bytes, and the keyboard is
a nonempty tuple of at most8 nonempty tuple rows of at most8 typed buttons.
None omits the keyboard; an empty/unknown shape is refused, never truncated.
Numeric chat IDs include negative groups; booleans, zero, usernames and values
outside signed64-bit are refused. Callback IDs are nonempty and locally capped
at256 UTF-8 bytes as a bounded client policy, not a claimed vendor limit.

A confirmed send result must have exactly bot_id/chat_id/message_id, with the
trusted bot and intended chat matching, a positive integer message ID and the
original execution identity when already known. A callback result must have
exactly bot_id/answered:true. Private text, button refs and chat/message IDs do
not appear in result/model repr or exception text. A malformed or mismatched
post-dispatch result is UNKNOWN, never a safe basis to send again under a new key.

TelegramSendCondition is a separate closed enum; receive conditions
are not widened. Each nonnull condition must match its declared hosted status
and Core code. request_changed is not the internal REQUEST_DIGEST_MISMATCH.
connection_not_usable permits the two declared revoked/repair codes.
identity_mismatch is AMBIGUOUS/RESULT_AMBIGUOUS; callback_in_flight and other
safe refusals remain failed_safe/REQUEST_REFUSED. rate_limited carries an
integer delay without an automatic sleep or resend. Generic kernel refusal or
approval may lack a Broker condition; it never creates a grant. Confirmed
success still requires explicit null condition. Human messages are not control.

Approval-required retains the exact operation/body/key; the caller completes
the existing browser approval and explicitly requests that original invocation
again. The error's approval_url carries only the existing hosted approval
data for the owning UI; it is omitted from str/repr. The authenticated owning
adapter/UI must validate its expected Broker approval origin before presenting
or opening it. This library never opens that URL or retries automatically.
After a transport error, timeout after invoke, malformed success or AMBIGUOUS
outcome, the serial sender retains only the original intent. Changed body/key,
another send or a callback answer cannot replace it. Explicit same-key lookup
may recover a stored confirmed reply or remain ambiguous; it must not redispatch
an unknown send in the Broker. An expired/refused replay cannot erase the earlier
unknown, and a known execution ID cannot change during reconciliation.

This is volatile per-object containment, NOT a durable outbox, provider
idempotency or an exactly-once guarantee. Before dispatch the owning consumer
persists intent/key/body digest and current authority; after restart it restores
the original intent and unresolved state from its own store. Do not create a new
object/key to treat an unknown as safe. A deliberately separate new message
after a human decision belongs to that durable consumer authority, not an
automatic retry/clear operation supplied by this class. Retention durations
remain proposed owner policy; this library claims no infinite dedup window.

Callback answers are neutral UX acknowledgements, not update-prefix ACKs,
business commits, approval or proof of a human reading a notice. Broker binds
a callback to the authenticated READ device and an issued/claimed record;
same-device apps do not thereby gain isolation. Consumer business handling still
checks expected bot/chat/sender, current task/notice revision, durable replay
and payload conflict rules. Button data is an opaque reference, never an
arbitrary command. Broker's durable pre-socket claim, unknown/tombstone behavior,
private storage and actual duplicate-provider prevention require its own source,
PostgreSQL/fake/native/custody/consumer qualification.

Synthetic tests exercise the client without live provider traffic. Actual Broker
implementation and installed authorised consumers need separate qualification.
A synthetic success is not Telegram delivery, operator acknowledgement or a
real grant.
