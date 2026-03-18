from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
REPO_ROOT = PROJECT_ROOT.parent


def _run_git(args: list[str]) -> tuple[int, str, str]:
    proc = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def cmd_status(_: argparse.Namespace) -> int:
    rc1, branch, err1 = _run_git(["rev-parse", "--abbrev-ref", "HEAD"])
    rc2, sha, err2 = _run_git(["rev-parse", "--short", "HEAD"])
    rc3, dirty, err3 = _run_git(["status", "--porcelain"])
    if rc1 != 0 or rc2 != 0 or rc3 != 0:
        print(err1 or err2 or err3)
        return 1
    payload = {
        "branch": branch,
        "commit_short": sha,
        "dirty": bool(dirty.strip()),
        "dirty_files_count": len([x for x in dirty.splitlines() if x.strip()]),
    }
    print(json.dumps(payload, indent=2))
    return 0


def cmd_create(args: argparse.Namespace) -> int:
    name = args.name or f"exp/{args.series}/{args.lane}/i{args.iteration:03d}"
    from_ref = args.from_ref or "HEAD"
    rc, _, err = _run_git(["branch", name, from_ref])
    if rc != 0:
        print(err)
        return 1
    if args.switch:
        rc, _, err = _run_git(["switch", name])
        if rc != 0:
            print(err)
            return 1
    print(f"created_branch={name}")
    print(f"from_ref={from_ref}")
    return 0


def cmd_start_from_run(args: argparse.Namespace) -> int:
    manifest = PROJECT_ROOT / "runs" / args.run_id / "manifest.json"
    if not manifest.exists():
        print(f"manifest not found for run {args.run_id}: {manifest.as_posix()}")
        return 1
    data = json.loads(manifest.read_text(encoding="utf-8"))
    from_ref = str(data.get("git_sha") or "HEAD")
    name = args.name or f"exp/restart/{args.run_id}"
    rc, _, err = _run_git(["branch", name, from_ref])
    if rc != 0:
        print(err)
        return 1
    if args.switch:
        rc, _, err = _run_git(["switch", name])
        if rc != 0:
            print(err)
            return 1
    print(f"created_branch={name}")
    print(f"from_run={args.run_id}")
    print(f"from_ref={from_ref}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Branch helper for experiment lanes.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    status = sub.add_parser("status", help="Show current git status context.")
    status.set_defaults(func=cmd_status)

    create = sub.add_parser("create", help="Create experiment branch.")
    create.add_argument("--name", default=None)
    create.add_argument("--series", default="s1")
    create.add_argument("--lane", default="small")
    create.add_argument("--iteration", type=int, default=0)
    create.add_argument("--from-ref", default="HEAD")
    create.add_argument("--switch", action="store_true")
    create.set_defaults(func=cmd_create)

    from_run = sub.add_parser("start-from-run", help="Create branch from run manifest git sha.")
    from_run.add_argument("--run-id", required=True)
    from_run.add_argument("--name", default=None)
    from_run.add_argument("--switch", action="store_true")
    from_run.set_defaults(func=cmd_start_from_run)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

