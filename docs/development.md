# Development entry points

Start with `make help`; `make help-all` lists every target and its purpose.
This repository is a library, with no standalone server or provider credentials
required for development. `make run` reports the installed distribution version;
it does not call a provider.

## First checkout

1. `make doctor` checks prerequisites and prints PASS or an actionable FIX.
2. `make hooks` installs the lightweight guard against pushes to `main`.
3. On an admitted build host, `make setup` installs the frozen development extras
   and the existing locked `release` group (the build backend).
4. `make test` runs root offline fixture tests.
5. `make verify` runs the complete qualification gate, serially even under `-j`.

`make doctor` is read-only: it does not sync dependencies, acquire tools, import
providers, create caches, install hooks or change configuration. It checks Git,
Make, awk, grep, uv >=0.12.2, the development Python pin in `.python-version`,
locked Ruff 0.15.5 and Hatchling 1.27.0, and the separately admitted mypy 2.3.1.
`PYTHON` selects the diagnostic interpreter (3.12+); `UV` and `MYPY` can name
existing tool executables. Doctor checks `.venv/bin/python`, which setup creates,
or the environment named by `UV_PROJECT_ENVIRONMENT`. It checks declared direct
runtime, optional SDK, development and release requirement versions without
importing provider code.
The full gate also needs an existing Python 3.12 interpreter selected by
`FLOOR_PYTHON` or `python3.12` on PATH. `TMPDIR` must be writable and outside the
checkout. Diagnostics mention environment variable names only, never values.
No mounted volume, live service, provider credential or secret is needed.

Network access is not needed for a cached frozen gate. Setup may need the public
package index if the approved cache is incomplete. For an optional bounded,
credentialless index reachability check use `DOCTOR_NETWORK=1 make doctor`.
A missing prerequisite returns a nonzero status and a fix; doctor never repairs
it automatically. Tool acquisition belongs to the admitted toolchain owner.

## Gate scope

`verify` retains every earlier gate step: root Ruff lint and formatting, root
unittest fixtures, installed-distribution checks, travelling provider tests,
Python-floor qualification, offline wheel and source builds, wheel validation,
all three historical package gates and whole committed-tree whitespace checks.
It adds strict static checking of `tools/repo_maintenance.py` with mypy 2.3.1.
This scoped check does not claim static coverage of all migrated provider code;
provider contracts retain their existing full runtime and floor checks.
No missing-tool or baseline failure is skipped. Setup, tests, types, builds and
gates run only on an admitted build host, never the authoring machine.

Each component is also a documented target for focused development. `make format`
changes formatting; `make format-check` only checks. `make release` runs the full
gate and leaves archives in `dist/`; it performs no tag, upload or deployment.
Publication follows its separately approved release workflow.

## Local hooks and cleanup

The tracked `.githooks/pre-push` rejects every remote `refs/heads/main` update,
including deletions and explicit refspecs, without running a build or test.
`make hooks` installs it in the checkout's Git hook directory using exclusive
creation. It is idempotent for the exact installed guard. An existing different
hook, a symlink or a configured custom hooks path is preserved and reported as
a fix for that hook's owner; no automatic replacement or configuration change.
Doctor reports whether the exact executable guard is installed. Server-side
protection and the normal reviewed merger remain required.

`make clean` previews only disposable root `dist/`, `build/`, `.ruff_cache/` and
`.pytest_cache/`. Explicit `CLEAN_CONFIRM=1 make clean` removes those directories;
it refuses symlinks and non-directory targets. It does not remove environments,
source, stashes, worktrees, branches, historical packages or `releases/`.
Do not confirm cleanup while another job uses these outputs.
