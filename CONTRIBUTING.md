# Contributing

One distribution, `zeo-integrations`, depends on published `zeocore>=0.43`;
Core does not import it at import time. Earlier per-provider PyPI and shared
base/ports proposals are superseded, with their evidence preserved.

## Development and layout

On admitted ENG-UK, use the existing Python3.14.3/uv toolchain and run:

```sh
make setup
make verify
```

The root is now the aggregate distribution, with `requires-python >=3.12`,
`src/zeo_integrations/<provider>/` and travelling tests under
`tests/test_integrations/<provider>/`. Keep the shared namespace implicit;
provider-level `py.typed` markers identify typed children. Google remains one
family with its current six entry-point names. SDKs use optional extras copied
from Core's existing requirements; only the selected provider loads its SDK.
`make setup` installs SDK extras for developer qualification, not as mandatory
runtime dependencies. The normal package install does not select all extras.

The foundation initially contains shared packaging only. Do not claim all
providers have moved until their actual batches merge and are installed/tested.
Existing `packages/` and `releases/` remain separately preserved historical and
consumer checkpoints during transition; the older read-only Kit and hardened
HubSpot APIs must not be silently conflated with whole Core-shaped moves.

## Migration, source provenance and credentials

Coordinate each bounded batch with the Core owner. Copy frozen provider
implementations and their tests, with only explicit import/module-reference
relocations. Preserve old/new file paths and Git blobs for every copied file
in the PR body (M2). Test that fresh imports of Core, its CLI, contracts and
loader never import the aggregate (M1). Do not duplicate Core contracts,
registries, boot state or credential custody. Copied fixtures remain unchanged;
additional verification fixtures forbid live provider traffic. Include archive,
installed-distribution, Python-floor and optional-SDK checks in the owning gate.

Core keeps its provider entry-point registrations until its later optional lazy
shim PR; the collection registers none during these moves. Its shim comes only
after actual aggregate PyPI availability. Packages may be installed directly
from a reviewed repository commit beforehand. No runtime Git fetch or fallback
copy. The per-operation Broker schema/admission and actual consumer acceptance
remain separate from availability of library code.

Whole-source movement preserves existing host/credential discovery and
principal-host controls through Core; it does not grant a new execution route,
token store, credential principal or live operation. Never copy keys or secrets
or call live providers during verification. The GitHub App signer remains in
`zeo_core.release.app_tokens`; moving a compatibility forwarder does not move
signer custody. Core runtime groups stay Core under the agreed module map.

## Licensing and review

Preserve source copyright and MIT permission notices on every copied portion.
Record upstream repository, full source commit, paths and local destinations in
each package's NOTICE and the root NOTICE. VeyraX is a service-coverage
reference only: implement fresh code and tests rather than copying or adapting
its source. Do not add attribution for code that was not copied.

This public repository uses exactly `sow: public` in every commit and on line 1
of the PR body. Keep internal work identifiers, role names, session trailers and
message identifiers in private governance records, not public files, commits or
PR metadata. Keep branch topics neutral. The required project prefix remains:
push `zeointegrations/<slug>` branches to the fork
with at most one open PR per contributing seat per repository under the
operator's coordinated batch allocation. Open a new PR only
when the work is ready and the full repository gate has passed on that exact
head, including new draft PRs. Existing drafts stay draft until their gate passes.
Independent review and required signing precede merging-app integration.

### Own-fork backups and required gates

Push daily backups to your own fork's `zeointegrations/wip/<topic>` branch.
Only that own-fork WIP route uses root Ruff lint and format checks instead of
running the full suite for the backup push. Test regularly on the work branch
with the full gate in your own lane, independently of backup pushes. A WIP backup branch is never a PR head; publish an ordinary own-seat branch
after its exact-head full gate passes. Opening or updating
a PR must never start the heavy suite; PR checks remain light. Later scheduler
placement does not turn the PR into the full-gate trigger.
Before descendant Git/check commands, clear Git-local environment variables in
an isolated subshell using `git rev-parse --local-env-vars` and `unset` so a
parent hook's repository context cannot leak into another checkout. Run root
`uv run --frozen --offline --no-sync ruff check .` and
`uv run --frozen --offline --no-sync ruff format --check .` with a combined
wall-clock deadline below 180 seconds. Missing tools/cache, a timeout or a failed
check stops the backup push; never substitute a full suite or bypass a hook.
Prepare the approved tools and frozen cache separately before the quick check.
`make hooks` installs the tracked lightweight main-push guard without replacing
an existing hook or changing Git configuration. `make doctor` reports its
installation status. The guard runs no tests or builds: the required maintainer
checks above still apply, and no automatic WIP qualification is claimed.

Every other push requires full own-repository `make setup` and `make verify` on
the exact candidate head in the maintainer's own lane (ENG-UK by default).
Before any final approval, required signature or merge, record that full gate's
HEAD, clean DIRTY status, host, EXIT and complete log SHA256 in the PR body.
The requirement also applies before a new PR opens and when an existing draft
becomes ready; rerun on any changed candidate head before updating its PR. A backup, quick check or draft is not qualification, release or
consumer acceptance. Preserve disabled upstream pushes and normal independent
review, required signing and merging-app integration.

Record baseline failures with the base commit, failing tests and original gate
evidence in the PR body, and name the repair owner and date. The maintainer
repairs main in its own PR. Never waive, skip or relabel a failure as passing,
and never use `--no-verify` or another actor or route to bypass a hook/refusal.
This text-only policy amendment adds no hook, checker, tool or configuration.

Full qualification runs locally and independently in review until pomar CI on
ENG-UK posts its signed gate. Public Actions is one three-minute dependency-free
PR smoke job, not the full gate. Required
checks align with the smoke/review gate and later `zeo-ci/gate`; no removed full-CI
job may remain required. See docs/ci-toolchain.md for exact inputs and limits.
The per-provider HubSpot PyPI workflow/click list is superseded and must not
be dispatched. Public Actions perform checks only; the retained artifact-check workflow has
no PyPI publisher job. Publication uses separately reviewed Linux-built candidate
artifacts and no-rebuild promotion through the normal signature and merger path.
Do not cut a release, alter publisher trust or change account/environment settings
as part of source qualification. Workflow changes use the normal reviewed merger
with its required Workflows permission; no hand-merge workaround. See
[the publication boundary](docs/pypi-bootstrap.md). Source moves and repository
installs do not wait for PyPI. All builds/tests/gates
remain on ENG-UK with existing tooling and the20GB seat cap; missing tools go to
the gate infrastructure owner, never a Mat fallback. This foundation performs no settings
write, runner registration, pending-publisher creation or upload.
