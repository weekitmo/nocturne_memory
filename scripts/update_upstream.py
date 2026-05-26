#!/usr/bin/env python3
"""Update this checkout from upstream and refresh local runtime deps.

This script is intentionally conservative:
- it refuses to merge when the git worktree has local changes unless
  --allow-dirty is provided;
- it creates/updates the uv-managed .venv when needed;
- it installs frontend deps with pnpm;
- it prints an MCP stdio config whose command points at this checkout's
  venv Python and whose args use this checkout's absolute backend path.

Usage:
  python scripts/update_upstream.py
  python scripts/update_upstream.py --remote upstream --branch main
  python scripts/update_upstream.py --skip-git --write-mcp-config mcp.json
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = ROOT / "backend"
FRONTEND_DIR = ROOT / "frontend"
VENV_DIR = ROOT / ".venv"
UV_CACHE_DIR = ROOT / ".uv-cache"


def log(message: str = "") -> None:
    print(message, flush=True)


def fail(message: str, code: int = 1) -> NoReturn:
    print(f"[ERROR] {message}", file=sys.stderr)
    raise SystemExit(code)


def which_or_fail(binary: str, install_hint: str) -> str:
    found = shutil.which(binary)
    if not found:
        fail(f"Missing '{binary}'. {install_hint}")
    return found


def run(cmd: list[str], *, cwd: Path = ROOT, check: bool = True) -> subprocess.CompletedProcess[str]:
    log(f"$ {' '.join(cmd)}")
    env = os.environ.copy()
    env.setdefault("UV_CACHE_DIR", str(UV_CACHE_DIR))
    result = subprocess.run(cmd, cwd=str(cwd), env=env, text=True)
    if check and result.returncode != 0:
        fail(f"Command failed with exit code {result.returncode}: {' '.join(cmd)}", result.returncode)
    return result


def capture(cmd: list[str], *, cwd: Path = ROOT, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.setdefault("UV_CACHE_DIR", str(UV_CACHE_DIR))
    result = subprocess.run(cmd, cwd=str(cwd), env=env, text=True, capture_output=True)
    if check and result.returncode != 0:
        if result.stdout:
            print(result.stdout, file=sys.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        fail(f"Command failed with exit code {result.returncode}: {' '.join(cmd)}", result.returncode)
    return result


def git_remote_names() -> set[str]:
    result = capture(["git", "remote"], check=False)
    if result.returncode != 0:
        fail("This script must be run inside a git checkout.")
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def pick_remote(requested: str | None) -> str:
    remotes = git_remote_names()
    if requested:
        if requested not in remotes:
            fail(f"Git remote '{requested}' does not exist. Existing remotes: {', '.join(sorted(remotes)) or '(none)'}")
        return requested
    if "upstream" in remotes:
        return "upstream"
    if "origin" in remotes:
        log("[WARN] Remote 'upstream' not found; falling back to 'origin'.")
        return "origin"
    fail("No git remote found. Add 'upstream' or 'origin' first.")


def ensure_clean_worktree(allow_dirty: bool) -> None:
    status = capture(["git", "status", "--porcelain"], check=True).stdout.strip()
    if status and not allow_dirty:
        fail(
            "Git worktree has local changes. Commit/stash them first, or re-run with --allow-dirty.\n"
            + status
        )


def remote_branch_exists(remote: str, branch: str) -> bool:
    result = capture(["git", "rev-parse", "--verify", f"refs/remotes/{remote}/{branch}"], check=False)
    return result.returncode == 0


def current_branch() -> str | None:
    result = capture(["git", "branch", "--show-current"], check=False)
    branch = result.stdout.strip()
    return branch or None


def update_from_remote(remote: str, branch: str, allow_dirty: bool) -> None:
    ensure_clean_worktree(allow_dirty)
    run(["git", "fetch", remote, "--prune"])

    selected_branch = branch
    if not remote_branch_exists(remote, selected_branch):
        cur = current_branch()
        if cur and remote_branch_exists(remote, cur):
            selected_branch = cur
            log(f"[WARN] {remote}/{branch} not found; using {remote}/{cur} instead.")
        else:
            fail(f"Remote branch '{remote}/{branch}' does not exist.")

    run(["git", "merge", "--ff-only", f"{remote}/{selected_branch}"])


def venv_python_path() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def ensure_backend_deps(include_dev: bool) -> None:
    which_or_fail("uv", "Install uv first: https://docs.astral.sh/uv/getting-started/installation/")
    pyproject = ROOT / "pyproject.toml"
    backend_pyproject = BACKEND_DIR / "pyproject.toml"

    if pyproject.is_file():
        log("\n==> Syncing backend environment with uv sync")
        run(["uv", "sync"] + (["--all-extras"] if include_dev else []))
        return

    if backend_pyproject.is_file():
        log("\n==> Syncing backend environment with uv sync --project backend")
        run(["uv", "sync", "--project", str(BACKEND_DIR)] + (["--all-extras"] if include_dev else []))
        return

    python_path = venv_python_path()
    if not python_path.exists():
        log("\n==> Creating uv virtual environment: .venv")
        run(["uv", "venv", str(VENV_DIR)])
    else:
        log("\n==> Existing uv virtual environment found: .venv")

    requirements = BACKEND_DIR / "requirements.txt"
    if not requirements.is_file():
        fail(f"Missing backend requirements file: {requirements}")

    log("\n==> Installing/updating backend dependencies")
    run(["uv", "pip", "install", "--python", str(python_path), "-r", str(requirements)])

    dev_requirements = BACKEND_DIR / "requirements-dev.txt"
    if include_dev and dev_requirements.is_file():
        log("\n==> Installing/updating backend dev dependencies")
        run(["uv", "pip", "install", "--python", str(python_path), "-r", str(dev_requirements)])


def ensure_frontend_deps(approve_builds: bool, build: bool) -> None:
    if not (FRONTEND_DIR / "package.json").is_file():
        log("\n==> No frontend/package.json found; skipping frontend deps.")
        return

    which_or_fail("pnpm", "Install pnpm first: https://pnpm.io/installation")

    if not (FRONTEND_DIR / "node_modules").is_dir():
        log("\n==> Installing frontend dependencies with pnpm (fresh node_modules)")
    else:
        log("\n==> Syncing frontend dependencies with pnpm")

    run(["pnpm", "install"], cwd=FRONTEND_DIR)

    if approve_builds:
        log("\n==> Approving/running pnpm dependency build scripts when needed")
        result = run(["pnpm", "approve-builds", "--all"], cwd=FRONTEND_DIR, check=False)
        if result.returncode != 0:
            log("[WARN] pnpm approve-builds --all failed or is unsupported by this pnpm version; continuing.")

    if build:
        log("\n==> Building frontend with pnpm")
        run(["pnpm", "build"], cwd=FRONTEND_DIR)
        write_frontend_build_marker()


def write_frontend_build_marker() -> None:
    package_json = FRONTEND_DIR / "package.json"
    dist_dir = FRONTEND_DIR / "dist"
    if not package_json.is_file() or not dist_dir.is_dir():
        return
    try:
        version = json.loads(package_json.read_text(encoding="utf-8")).get("version")
    except (json.JSONDecodeError, OSError):
        version = None
    if version:
        (dist_dir / ".build_version").write_text(str(version), encoding="utf-8")


def mcp_config() -> dict[str, object]:
    python_path = venv_python_path().absolute()
    server_path = (BACKEND_DIR / "mcp_server.py").resolve()
    return {
        "mcpServers": {
            "nocturne_memory": {
                "command": str(python_path),
                "args": [str(server_path)],
            }
        }
    }


def print_mcp_config(write_path: str | None) -> None:
    config = mcp_config()
    rendered = json.dumps(config, indent=2, ensure_ascii=False)
    log("\n==> MCP client config (copy into your MCP client)")
    log(rendered)
    if write_path:
        target = Path(write_path)
        if not target.is_absolute():
            target = ROOT / target
        target.write_text(rendered + "\n", encoding="utf-8")
        log(f"\n[OK] Wrote MCP config to: {target}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update Nocturne Memory from upstream and refresh uv/pnpm deps.")
    parser.add_argument("--remote", default=None, help="Git remote to fetch/merge. Defaults to upstream, then origin.")
    parser.add_argument("--branch", default="main", help="Remote branch to merge. Default: main.")
    parser.add_argument("--allow-dirty", action="store_true", help="Allow running with a dirty git worktree.")
    parser.add_argument("--skip-git", action="store_true", help="Skip git fetch/merge and only refresh local deps/config output.")
    parser.add_argument("--skip-backend", action="store_true", help="Skip uv/.venv backend dependency setup.")
    parser.add_argument("--skip-frontend", action="store_true", help="Skip pnpm frontend dependency setup.")
    parser.add_argument("--include-dev", action="store_true", help="Install backend requirements-dev.txt too when using requirements files.")
    parser.add_argument("--skip-pnpm-approve-builds", action="store_true", help="Do not run pnpm approve-builds --all after pnpm install.")
    parser.add_argument("--skip-frontend-build", action="store_true", help="Do not run pnpm build after pnpm install.")
    parser.add_argument("--write-mcp-config", default=None, help="Also write the generated MCP JSON to this path, e.g. mcp.json.")
    parser.add_argument("--print-mcp-only", action="store_true", help="Only print/write generated MCP JSON; do not update git or deps.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    os.chdir(ROOT)

    log(f"Project root: {ROOT}")

    if not args.print_mcp_only:
        if not args.skip_git:
            remote = pick_remote(args.remote)
            log(f"\n==> Updating from {remote}/{args.branch}")
            update_from_remote(remote, args.branch, args.allow_dirty)

        if not args.skip_backend:
            ensure_backend_deps(args.include_dev)

        if not args.skip_frontend:
            ensure_frontend_deps(
                approve_builds=not args.skip_pnpm_approve_builds,
                build=not args.skip_frontend_build,
            )

    if not venv_python_path().exists():
        log(f"[WARN] venv Python does not exist yet: {venv_python_path()}")
        log("       Run without --skip-backend/--print-mcp-only to create it.")

    print_mcp_config(args.write_mcp_config)

    log("\n[OK] Update flow complete.")
    log("Start dashboard for local development: scripts/start_dashboard.sh")


if __name__ == "__main__":
    main()
