# Aggregate Loops mailing-list reader

The first account proof is a READ of the selected account's mailing lists when
its Connect connection exists. No subscriber/contact read is included in that
proof. This library does not create a connection, register a Broker operation,
select an account or grant a credential.

The EXISTING operation is `loops.newsletter.list_mailing_lists`, with closed
`NoArguments` and result `{mailing_lists: [{id, name, is_public}]}`. Connect owns
that outer envelope; this client returns its list projection. Preserve the same
operation ID, ID pattern `[A-Za-z0-9_-]{1,128}`, order and maximum 200 rows. Do
not register a second operation for this replacement client.

The collection includes the original typed Loops library in the ONE
`zeo-integrations` wheel. Historical standalone packages remain unchanged.
`zeo_integrations.loops.connect.ConnectLoopsMailingListsReader` is a separate,
narrow result facade. It exposes list metadata and close; it has no contact,
campaign, send or scheduling method. Its only provider call is the typed
`GET https://app.loops.so/api/v1/lists` request with no query or body; its result
projection contains only `id`, `name`, `is_public`. This is the pinned client
request contract, not real-account proof that the endpoint currently works.

Connect supplies a fresh per-use `LoopsClient` INSIDE custody and the shared
per-connection pacer as the required `wait` callable. Constructing the facade
makes no request. Call `list_mailing_lists()` once and close in `finally`.
The callback must share pacing with any other calls on that connection. No
`initialize`, token/environment discovery, retry or automatic extra account check
is added by this adapter. Provider exceptions retain the typed library's
sanitized refusal/unknown distinction. No private response is a log message.

The full typed library's other methods are source availability only; this slice
registers or authorizes none of them. Connect must preserve a single closed
mailing-list READ operation, authenticated connection/account binding,
response bounds, custody and its current authority rules. Operation identifiers
and live connection references are the host owner's to supply, not invented here.
The request takes no subscriber search/filter/pagination argument.

## Acceptance stages

1. Full exact-head `make setup` and `make verify` in the supported verification environment. Offline fakes
   prove one GET, shared pacing, list-only projection/no contact or write surface,
   strict malformed-response refusal and no retry. No live provider in verification.
2. The gate builds a wheel, verifies its Loops RECORD entries and makes a clean
   offline install from the locked runtime requirements. Namespace metadata must
   name exactly one aggregate distribution, with no duplicate entry points; the
   Loops import must resolve inside that installed environment and not import Core.
3. Independent review and normal merge/publication. Record
   the ACTUAL aggregate release and hashes, never predict a post version.
4. For ADOPTING THIS REPLACEMENT, Connect's separate compatible Core/server gate
   (including its compatibility and credential-isolation conditions) keeps the existing operation/schema.
   No duplicate ID or new registry permission is implied by the client swap.
5. The FIRST account proof can instead use that already-declared operation and
   its existing client now, once the actual selected connection, application owner
   grant and Broker allowlist are confirmed. It does not require this package or
   the Core bump. Current deployed authority is not inferred from catalogue code
   or an old connection report. No provider call is part of verification.

No account proof, connection existence, deployed server upgrade or live operation
is claimed by this source slice. Other integration batches do not wait for the
server upgrade.
