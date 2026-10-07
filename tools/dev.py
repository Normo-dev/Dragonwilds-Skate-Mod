"""Portable source-only build/test entrypoint. Never installs or publishes."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
COMPONENTS = ROOT / "components"
BUILD_IGNORES = {".git", ".idea", ".codex", ".agents", ".claude", ".cursor", "build", "local", "target", "__pycache__", ".venv", ".vs", "obj", "bin"}
FORBIDDEN = {".iso", ".pak", ".utoc", ".ucas", ".usmap", ".sav", ".dmp", ".glb", ".gltf",
             ".f64", ".npy", ".npz", ".rwcmset", ".stategraph", ".dwr1", ".edges", ".lips", ".exe", ".dll", ".pdb", ".zip"}

def verify():
    inventory = json.loads((ROOT / "SOURCE-ASSEMBLY.json").read_text())
    expected = {row["path"]: row for row in inventory["files"]}
    errors = []
    for name, row in expected.items():
        path = ROOT / name
        if not path.is_file() or path.is_symlink():
            errors.append("Missing or linked: " + name); continue
        raw = path.read_bytes()
        if len(raw) != row["bytes"] or hashlib.sha256(raw).hexdigest() != row["sha256"]:
            errors.append("Changed from selected source baseline: " + name)
    # Ignored output remains private; reject unexpected files in the source tree.
    for folder, directories, files in os.walk(ROOT):
        directories[:] = [x for x in directories if x not in BUILD_IGNORES]
        for filename in files:
            path = Path(folder) / filename; name = path.relative_to(ROOT).as_posix()
            if name in expected or name in {"SOURCE-ASSEMBLY.json", "provenance/UPSTREAM-README.md"}:
                continue
            if path.suffix.lower() in FORBIDDEN or ".private." in filename:
                errors.append("Excluded generated/game/binary file: " + name)
            else:
                errors.append("Unreviewed extra source file: " + name)
    if errors:
        raise ValueError("\n".join(errors[:30]))
    print(f"Verified {len(expected)} exact source files; release {inventory['release_version']}")

def shell_prefix(name):
    shell = shutil.which(name)
    if shell is None:
        raise ValueError("PowerShell executable unavailable: " + name)
    return [shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File"]

def component_command(component, output, shell="powershell", offline=False, test=False, dotnet="dotnet"):
    output = str(Path(output).absolute())
    args = shell_prefix(shell)
    if component == "worker":
        args += [str(COMPONENTS / "worker/Build-Worker.ps1"), "-TargetDirectory", output,
                 "-PythonExecutable", sys.executable]
        if test:
            args += ["-Mode", "Test"]
        if offline:
            args += ["-Offline"]
    elif component == "helpers":
        args += [str(COMPONENTS / "adapter/Build-Helpers.ps1"), "-OutputDirectory", output]
        if offline:
            args += ["-Offline"]
    elif component in {"exporter", "building-exporter"}:
        if offline:
            raise ValueError("Exporter script has no offline switch; use its locked NuGet cache configuration")
        executable = shutil.which(dotnet)
        if executable is None:
            raise ValueError(".NET SDK executable unavailable: " + dotnet)
        args += [str(COMPONENTS / component / "Build-Exporter.ps1"), "-Dotnet", executable, "-Output", output]
    else:
        raise ValueError("Unknown build component")
    return args

def run(command, cwd=ROOT, pythonpath=()):
    env = os.environ.copy(); env["PYTHONDONTWRITEBYTECODE"] = "1"
    if pythonpath:
        env["PYTHONPATH"] = os.pathsep.join(map(str, pythonpath))
    subprocess.run(command, cwd=cwd, env=env, check=True)

def run_suite(directory, paths):
    # Embedded Python ignores PYTHONPATH. Supply only the explicit portable
    # source/dependency directories before discovery, also for that interpreter.
    code = ('import json,sys,unittest;sys.path[:0]=json.loads(sys.argv[1]);'
            'suite=unittest.defaultTestLoader.discover(sys.argv[2],pattern="test_*.py");'
            'result=unittest.TextTestRunner(verbosity=2).run(suite);'
            'sys.exit(not result.wasSuccessful())')
    run([sys.executable, '-B', '-c', code, json.dumps([str(p) for p in paths]), str(directory)], cwd=directory)

def test_adapter(extra=False, dependency_paths=()):
    adapter = COMPONENTS / "adapter"
    if not extra:
        # Some unchanged upstream tests create test-runs beside their source.
        # Keep those synthetic binaries/fixtures out of the tracked source tree.
        with tempfile.TemporaryDirectory(prefix="dragonwilds-release-tests-") as temp:
            sandbox = Path(temp) / "adapter"
            shutil.copytree(adapter, sandbox)
            # Release archives store UI scripts separately from the Python
            # wizard tests. Restore their author-time adjacency in this isolated
            # fixture so the tests exercise the exact shipped display functions.
            if (adapter / "wizard").is_dir():
                shutil.copytree(adapter / "wizard", sandbox / "release-tools/wizard", dirs_exist_ok=True)
            for directory in (sandbox / "release-tools", sandbox / "release-tools/wizard"):
                run_suite(directory, [sandbox / "app", sandbox / "release-tools", directory, *dependency_paths])
        return
    tests = ROOT / "tests/adapter"
    if not tests.is_dir():
        raise ValueError("No supplemental adapter regression inventory was included in this source assembly")
    with tempfile.TemporaryDirectory(prefix="dragonwilds-source-tests-") as temp:
        sandbox = Path(temp)
        shutil.copytree(adapter / "app", sandbox, dirs_exist_ok=True)
        shutil.copytree(adapter / "host", sandbox / "host-probe")
        shutil.copytree(tests, sandbox, dirs_exist_ok=True)
        run_suite(sandbox, [sandbox, *dependency_paths])

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("verify"); commands.add_parser("plan")
    for operation in ("build", "test"):
        p = commands.add_parser(operation)
        choices = ["worker", "helpers", "exporter", "building-exporter"] if operation == "build" else ["worker", "adapter", "adapter-extra"]
        p.add_argument("component", choices=choices); p.add_argument("--output")
        p.add_argument("--powershell", default="powershell"); p.add_argument("--offline", action="store_true")
        p.add_argument("--dotnet", default="dotnet", help="Exact .NET SDK executable for exporter builds")
        p.add_argument("--test-dependency-path", action="append", default=[],
                       help="Optional existing Python package directory (for example pip --target output); never copied")
    p = commands.add_parser("package"); p.add_argument("--recipe", required=True); p.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.command == "verify":
        verify()
    elif args.command == "plan":
        print(json.dumps({"components": ["worker", "helpers", "exporter", "building-exporter"],
                          "tests": ["adapter", "worker", "adapter-extra (when reviewed inventory included)"],
                          "toolchains": {"rust": "1.96.0", "dotnet": "10.0.401", "python": "3.12"},
                          "packaging": "Requires local designations and dependency inputs; see docs/PACKAGING.md",
                          "game_data": "Not included; never needed for ordinary source checks"}, indent=2))
    elif args.command == "test" and args.component.startswith("adapter"):
        paths = [Path(p).resolve() for p in args.test_dependency_path]
        if any(not p.is_dir() for p in paths):
            parser.error("Every --test-dependency-path must be an existing directory")
        test_adapter(args.component == "adapter-extra", paths)
    elif args.command in {"build", "test"}:
        if not args.output:
            parser.error("Native builds/tests require an explicit --output directory")
        run(component_command(args.component, args.output, args.powershell, args.offline, args.command == "test", args.dotnet))
    else:
        builder = COMPONENTS / "adapter/release-tools/build_release.py"
        recipe = Path(args.recipe).absolute(); output = Path(args.output).absolute()
        run([sys.executable, "-B", str(builder), "stage", "--recipe", str(recipe), "--output", str(output)])
        run([sys.executable, "-B", str(builder), "audit", "--package", str(output)])

if __name__ == "__main__":
    main()
