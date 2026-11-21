"""Bounded diagnostics and explicit local maintenance; never import providers."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / ".githooks" / "pre-push"


def command(args: list[str]) -> str | None:
    """Bound probes; callers never print raw subprocess output or env values."""
    try:
        environment = os.environ.copy()
        if args[0] == "git":
            names = subprocess.run(
                ["git", "rev-parse", "--local-env-vars"],
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            ).stdout.splitlines()
            for name in names:
                environment.pop(name, None)
        result = subprocess.run(
            args,
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def hook_path() -> Path | None:
    value = command(["git", "rev-parse", "--git-path", "hooks/pre-push"])
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def matching_hook(path: Path | None) -> bool:
    return bool(
        path
        and not path.is_symlink()
        and path.is_file()
        and os.access(path, os.X_OK)
        and path.read_bytes() == HOOK.read_bytes()
    )


def doctor() -> int:
    failed = False

    def report(ok: bool, name: str, fix: str) -> None:
        nonlocal failed
        print(f"{'PASS' if ok else 'FIX'} {name}: {'ready' if ok else fix}")
        failed |= not ok

    report(
        sys.version_info[:2] >= (3, 12),
        "diagnostic Python",
        "select an existing Python 3.12+ with PYTHON",
    )
    for tool in ("git", "make", "awk", "grep"):
        report(shutil.which(tool) is not None, tool, "ask the toolchain owner")
    uv = command([os.environ.get("UV", "uv"), "--version"])
    match = re.match(r"uv (\d+)\.(\d+)\.(\d+)", uv or "")
    report(
        bool(match and tuple(map(int, match.groups())) >= (0, 12, 2)),
        "uv >=0.12.2",
        "provide admitted uv >=0.12.2; doctor never installs tools",
    )
    environment = Path(os.environ.get("UV_PROJECT_ENVIRONMENT", str(ROOT / ".venv")))
    if not environment.is_absolute():
        environment = ROOT / environment
    venv_python = environment / "bin" / "python"
    version = command([str(venv_python), "--version"])
    report(
        version == "Python " + (ROOT / ".python-version").read_text().strip(),
        "development Python pin (.python-version)",
        "run make setup with the pinned interpreter on an admitted host",
    )
    for package, expected in (("ruff", "0.15.5"), ("hatchling", "1.27.0")):
        installed = command(
            [
                str(venv_python),
                "-IB",
                "-c",
                "import importlib.metadata; "
                f"print(importlib.metadata.version({package!r}))",
            ]
        )
        report(installed == expected, f"{package} =={expected}", "run make setup")
    requirements = command(
        [
            str(venv_python),
            "-IB",
            "-c",
            "import importlib.metadata as m, pathlib, tomllib; "
            "from packaging.requirements import Requirement; "
            "p=tomllib.loads(pathlib.Path('pyproject.toml').read_text()); "
            "roots=p['project']['dependencies']+"
            "sum(p['project']['optional-dependencies'].values(), [])+"
            "p['dependency-groups']['dev']+p['dependency-groups']['release']; "
            "checks=[Requirement(s) for s in roots]; "
            "ok=all(m.version(r.name) in r.specifier for r in checks "
            "if r.marker is None or r.marker.evaluate()); "
            "print('ready' if ok else 'missing')",
        ]
    )
    report(
        requirements == "ready",
        "declared runtime, SDK, development and release requirements",
        "run make setup with the complete approved dependency cache",
    )
    mypy = command([os.environ.get("MYPY", "mypy"), "--version"])
    report(
        bool(re.match(r"mypy 2\.3\.1(?:[ .]|$)", mypy or "")),
        "mypy ==2.3.1",
        "ask the build-toolchain owner for the pinned static checker",
    )
    floor = os.environ.get("FLOOR_PYTHON") or shutil.which("python3.12")
    floor_version = command([floor, "--version"]) if floor else None
    report(
        bool(floor_version and floor_version.startswith("Python 3.12.")),
        "FLOOR_PYTHON (name only; Python 3.12)",
        "set FLOOR_PYTHON to an existing admitted Python 3.12 executable",
    )
    report((ROOT / "uv.lock").is_file(), "frozen lock", "restore committed uv.lock")
    report(
        matching_hook(hook_path()),
        "main-push hook",
        "run make hooks; preserve existing hooks",
    )
    print("PASS provider environment: no credential variables required or inspected")
    print("PASS volumes/services: no mount or live provider service required")
    temporary = Path(os.environ.get("TMPDIR", "/tmp")).resolve()
    report(
        temporary.is_dir()
        and os.access(temporary, os.W_OK)
        and not temporary.is_relative_to(ROOT),
        "TMPDIR (name only)",
        "select a writable existing temporary directory outside the checkout",
    )
    if os.environ.get("DOCTOR_NETWORK") == "1":
        try:
            request = urllib.request.Request("https://pypi.org/simple/", method="HEAD")
            with urllib.request.urlopen(request, timeout=5) as response:
                report(response.status == 200, "public package index", "check network")
        except (OSError, ValueError):
            report(False, "public package index", "check network; details suppressed")
    else:
        print("PASS network: offline; DOCTOR_NETWORK=1 enables public-index HEAD")
    return int(failed)


def hooks() -> int:
    # Never change configuration or take over a shared custom hooks directory.
    if command(["git", "config", "--get", "core.hooksPath"]) is not None:
        print("FIX custom hook path retained; ask its owner to integrate the guard")
        return 1
    target = hook_path()
    if target is None:
        print("FIX hooks: run inside a Git checkout")
        return 1
    if matching_hook(target):
        print("PASS main-push guard already installed")
        return 0
    if target.exists() or target.is_symlink():
        print("FIX existing hook retained; integrate the guard through its owner")
        return 1
    target.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation closes the existence-check race; never replace a hook.
    with target.open("xb") as output:
        output.write(HOOK.read_bytes())
    target.chmod(0o755)
    print("PASS installed main-push guard; no Git configuration changed")
    return 0


def clean() -> int:
    targets = [
        ROOT / name for name in ("dist", "build", ".ruff_cache", ".pytest_cache")
    ]
    if any(
        path.is_symlink() or (path.exists() and not path.is_dir()) for path in targets
    ):
        print("FIX cleanup refused: output path is a symlink or non-directory")
        return 1
    if os.environ.get("CLEAN_CONFIRM") != "1":
        print("Preview only: dist/ build/ .ruff_cache/ .pytest_cache/")
        print("CLEAN_CONFIRM=1 make clean removes only these root directories")
        return 0
    for path in targets:
        if path.exists():
            shutil.rmtree(path)
    print("Removed root outputs; environments and historical releases retained")
    return 0


def main() -> int:
    actions = {"doctor": doctor, "hooks": hooks, "clean": clean}
    if len(sys.argv) != 2 or sys.argv[1] not in actions:
        print("Usage: repo_maintenance.py doctor|hooks|clean")
        return 2
    try:
        return actions[sys.argv[1]]()
    except (OSError, ValueError):
        print("FIX maintenance refused: check file access; no error details printed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
