# HTTP collection boundary, not a Broker

This slice supplies the shared request/response library for later bounded
provider batches. It registers no provider, plugin, operation, catalogue,
credential principal, machine manifest or UI. Core contracts remain in Core.

Use an explicit host-owned synchronous HTTPX BaseTransport and an admitted HTTPS
origin. Missing/invalid transports fail with TRANSPORT_REQUIRED before an HTTPX
client can create a default socket transport. The host retains DNS/egress policy,
credential injection, per-call grants, shared connection pacing and durable
idempotency. One instance is per use and closes its supplied transport; a host
lease wrapper is needed if host resources are shared. No ambient credentials,
proxy environment, persistent cookies or automatic retry are introduced.

The generic invocation facade bounds arguments/results/requests, requires an
explicit idempotency key and refuses effects without separate host admission.
The key is propagated, not stored as a durable execution ledger. A timeout or
uncertain effect is not turned into success or automatically retried. The
streaming wrapper bounds response bytes and time, refuses redirects/encoding,
and exposes only selected response headers.

Travelling transport tests retain the provider-independent prefix of the supplied
execution suite. New synthetic-only cases cover missing transport, closed input,
admission, credential refusal and uncertain effects. Amazon, IMAP and provider
vectors were not included and are not claimed qualified. No live provider may be
called by verification. Verification must include the complete repository suite,
supported Python floor, package builds and independent review.

This is a shared library foundation. It does not implement the
selected Loops operation, daily approvals, a service scheduler, Substack fencing,
a new Core ABI, installation, registration or consumer acceptance. Per-file
provenance and the one local constructor correction are recorded in
migrations/http-boundary.json.
