from __future__ import annotations

import argparse
import re
from pathlib import Path


def _extract_system_prompt(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    m = re.search(r'SYSTEM_PROMPT\s*=\s*"""(.*?)"""', text, flags=re.DOTALL)
    if not m:
        raise ValueError(f"SYSTEM_PROMPT triple-quoted block not found in {path.as_posix()}")
    return m.group(1)


def _tokens_est(chars: int) -> int:
    return max(1, int(chars / 4))


def cmd_check(args: argparse.Namespace) -> int:
    prompt_path = Path(args.prompt_file)
    prompt = _extract_system_prompt(prompt_path)
    chars = len(prompt)
    tokens_est = _tokens_est(chars)

    ok = True
    if chars > args.max_chars:
        ok = False
    if tokens_est > args.max_tokens_est:
        ok = False

    print(f"prompt_file={prompt_path.as_posix()}")
    print(f"prompt_chars={chars}")
    print(f"prompt_tokens_est={tokens_est}")
    print(f"max_chars={args.max_chars}")
    print(f"max_tokens_est={args.max_tokens_est}")
    print(f"status={'ok' if ok else 'violation'}")
    return 0 if ok else 2


def cmd_diff(args: argparse.Namespace) -> int:
    old_prompt = _extract_system_prompt(Path(args.old_prompt_file))
    new_prompt = _extract_system_prompt(Path(args.new_prompt_file))
    delta = len(new_prompt) - len(old_prompt)
    ok = abs(delta) <= args.max_delta_chars
    print(f"old_chars={len(old_prompt)}")
    print(f"new_chars={len(new_prompt)}")
    print(f"delta_chars={delta}")
    print(f"max_delta_chars={args.max_delta_chars}")
    print(f"status={'ok' if ok else 'violation'}")
    return 0 if ok else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prompt governance checks.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    check = sub.add_parser("check", help="Check prompt size budget.")
    check.add_argument(
        "--prompt-file",
        default="analytics-agent-autoresearch/candidate/prompt.py",
    )
    check.add_argument("--max-chars", type=int, default=5000)
    check.add_argument("--max-tokens-est", type=int, default=1400)
    check.set_defaults(func=cmd_check)

    diff = sub.add_parser("diff", help="Check prompt delta budget.")
    diff.add_argument("--old-prompt-file", required=True)
    diff.add_argument("--new-prompt-file", required=True)
    diff.add_argument("--max-delta-chars", type=int, default=500)
    diff.set_defaults(func=cmd_diff)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

