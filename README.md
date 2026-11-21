# Zeo integrations

`zeo-integrations` is one Python distribution containing service integrations
under `zeo_integrations.<family>`. It requires Python 3.12+ and
`zeocore>=0.43.0,<1`, which supplies shared contracts and runtime support.
Core can run without this collection; this collection needs Core. Importing
Core does not eagerly import the collection.

Install from the repository root at a full reviewed commit while aggregate
publication is being established. Replace `COMMIT` with the full SHA; choose
only the optional SDK extras your application needs:

```sh
pip install 'zeo-integrations[google] @ git+https://github.com/zeroemployeeorg/zeointegrations.git@COMMIT'
```

There is one aggregate distribution, not a separate PyPI project per provider.
Installing source is not evidence of a published release or live provider
acceptance. Existing Core entry-point names remain there during migration;
the collection registers no duplicate plugins. The historical `packages/`
and `releases/` trees preserve earlier APIs and release evidence, not additional
future distribution targets.

## Development

Start with `make help` for the first-time guide and `make help-all` for every
entry point. `make doctor` checks prerequisites without changing anything;
`make hooks` installs the lightweight main-push guard. See
[development instructions](docs/development.md) for setup, the complete
`make verify` gate, tool pins and build-host requirements.

## Current families

These statuses describe implemented source coverage, not deployed credentials,
provider enrollment, or end-to-end acceptance. **Ready** means the named bounded
surface has an implementation and offline tests; **partial** means broader
provider behavior is deliberately outside that surface. No row promises the
provider's entire API.

| Family | Status | Implemented surface and boundary |
|---|---|---|
| [bluesky](src/zeo_integrations/bluesky) | Partial | Client, service, authentication/configuration, rich-text facets; requires the selected SDK extra and host credential policy. |
| [briefing_sources](src/zeo_integrations/briefing_sources) | Ready | Bounded public-source HTTP fetching, host controls, response limits, caching and rate limits. |
| [ffmpeg](src/zeo_integrations/ffmpeg) | Ready | Local media processing service and models; optional FFmpeg dependency and an available native toolchain are required. |
| [github](src/zeo_integrations/github) | Partial | Repository, issue, pull-request and other curated operations; App signing remains in Core. |
| [google](src/zeo_integrations/google) | Partial | Gmail, Drive, Calendar, Docs, Sheets, Slides and YouTube; shared auth/configuration and optional Google SDKs. Coverage and credential setup differ by service. |
| [hubspot](src/zeo_integrations/hubspot) | Partial | Typed marketing email, campaigns, subscriptions and sequence workflows; injected HTTP transport with explicit send controls. |
| [jupytext](src/zeo_integrations/jupytext) | Ready | Notebook/script conversion, batch conversion and semantic parity receipts; optional Jupytext SDK. |
| [kit](src/zeo_integrations/kit) | Partial | Typed subscriber, broadcast and sequence operations with explicit audience/send controls. |
| [loops](src/zeo_integrations/loops) | Partial | Typed API client, upload/newsletter operations and webhook verification; operation admission and delivery remain host responsibilities. |
| [mastodon](src/zeo_integrations/mastodon) | Partial | Instance discovery, media upload and status publication through an admitted transport. |
| [notion](src/zeo_integrations/notion) | Partial | Pages, blocks, search, databases and upsert support; optional Notion SDK and host-controlled token initialization. |
| [publishing](src/zeo_integrations/publishing) | Partial | Typed content/media composition and curated Facebook, Instagram, Threads, LinkedIn and X publication workflows; requires provider-specific transport and admission. |
| [revolut](src/zeo_integrations/revolut) | Partial | Hosted and local read/custody lifecycle surfaces; no general money-transfer API is promised. |
| [supabase](src/zeo_integrations/supabase) | Partial | Database, auth, storage, functions and realtime interfaces; optional Supabase SDK. |
| [telegram](src/zeo_integrations/telegram) | Partial | Existing credentialless hosted read/ack/send API plus [direct async Bot API transport](docs/telegram-direct.md) for polling, text sends and callback answers with an injected token. |
| [twilio](src/zeo_integrations/twilio) | Partial | Injected WhatsApp send/status transport and webhook signature/parsing primitives; no phone enrollment or application controller. |

The [provider coverage inventory](docs/inventory/provider-coverage.md) lists
planned services, auth inputs, dependency observations, current counterparts
and the implementation order. Planned coverage is not implemented coverage.

Credentials and execution policy belong to the host. Selecting a Python API
or installing an extra does not grant provider permission or consent. Some
legacy migrated services still use file/configuration-based authentication;
inspect the selected service's constructor before choosing it for a host that
requires in-memory credential injection. Sheets also offers an additive [in-memory credentials constructor](docs/google-sheets-memory.md).
Direct Telegram reads no credential files or environment variables and never
automatically retries sends.

## Remaining Core migration

Core still contains provider implementations or compatibility surfaces for
Google, GitHub, HubSpot, Kit, Notion, Revolut, Bluesky and briefing sources,
plus FFmpeg and Jupytext local capabilities. Other integrations still needing
an explicit ownership disposition include Gemini, LLM providers and Pandoc.
Shared contracts, registries, boot/loader code, runtime environments, database,
agent, notebook and model-operation infrastructure require separate ownership
review; a folder named `integrations` alone does not make it provider code.
Provider moves must preserve consumers and provenance before duplicate Core
implementations are removed. See the [source inventory](docs/inventory/README.md)
for the earlier migration baseline; it is historical evidence, not a current
claim that every listed source has moved.

## Development

```sh
make setup
make verify
```

The full gate includes lint, formatting, source/installed-distribution tests,
provider tests, Python 3.12 floor checks and packaging. Public Actions is a
short smoke check. See [CONTRIBUTING.md](CONTRIBUTING.md) for gate placement,
exact-commit qualification, review and migration rules, and [NOTICE](NOTICE)
for attribution of migrated source.
