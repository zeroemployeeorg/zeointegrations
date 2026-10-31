"""Prove aggregate Loops RECORD and namespace identity in a clean offline install."""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from pathlib import Path


def run(argv: list[str]) -> None:
    subprocess.run(argv, check=True, timeout=180)


def main() -> None:
    project = tomllib.loads(Path("pyproject.toml").read_text())["project"]
    wheels = list(Path("dist").glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError("expected exactly one freshly built aggregate wheel")
    wheel = wheels[0].resolve()
    required = {
        "zeo_integrations/loops/__init__.py",
        "zeo_integrations/loops/client.py",
        "zeo_integrations/loops/models.py",
        "zeo_integrations/loops/transport.py",
        "zeo_integrations/loops/connect.py",
        "zeo_integrations/loops/NOTICE",
        "zeo_integrations/loops/py.typed",
    }
    with zipfile.ZipFile(wheel) as archive:
        record_names = [
            n for n in archive.namelist() if n.endswith(".dist-info/RECORD")
        ]
        if len(record_names) != 1 or not required <= set(archive.namelist()):
            raise RuntimeError("aggregate wheel lacks the Loops distribution files")
        record = archive.read(record_names[0]).decode().splitlines()
        if not required <= {line.split(",", 1)[0] for line in record}:
            raise RuntimeError("Loops files absent from aggregate RECORD")
    with tempfile.TemporaryDirectory(
        prefix="loops-installed-", dir=os.environ.get("TMPDIR")
    ) as directory:
        root = Path(directory)
        requirements = root / "requirements.txt"
        run(
            [
                "uv",
                "export",
                "--frozen",
                "--no-dev",
                "--no-hashes",
                "--no-emit-project",
                "--output-file",
                str(requirements),
            ]
        )
        environment = root / "venv"
        run(["uv", "venv", "--python", sys.executable, str(environment)])
        python = environment / "bin/python"
        run(
            [
                "uv",
                "pip",
                "install",
                "--offline",
                "--no-deps",
                "--python",
                str(python),
                "-r",
                str(requirements),
                str(wheel),
            ]
        )
        script = """
import importlib.metadata as metadata
import socket
import sys
from pathlib import Path

def refuse(*args, **kwargs):
    raise AssertionError("network forbidden during installed Loops import")
socket.socket.connect = refuse
socket.socket.connect_ex = refuse
socket.getaddrinfo = refuse
import zeo_integrations.loops as loops
from zeo_integrations.loops.connect import ConnectLoopsMailingListsReader

assert Path(loops.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
distribution = metadata.distribution("zeo-integrations")
assert distribution.version == sys.argv[1]
names = metadata.packages_distributions().get("zeo_integrations", [])
assert names == ["zeo-integrations"], names
assert not distribution.entry_points
assert not any(n == "zeo_core" or n.startswith("zeo_core.") for n in sys.modules)
assert callable(ConnectLoopsMailingListsReader.list_mailing_lists)
print("LOOPS_INSTALLED_RECORD_NAMESPACE_AND_IMPORT_BOUNDARY_PASS")
"""
        run([str(python), "-I", "-c", script, project["version"]])
    print(
        json.dumps(
            {
                "wheel": wheel.name,
                "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
            }
        )
    )


if __name__ == "__main__":
    main()
