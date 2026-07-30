"""Warn when documentation lands outside the canonical set.

Canonical documentation is a closed set: the entry points, the two design
documents, ADRs, and READMEs sitting beside the code they describe. Everything
else drifts, because nothing points at a central `docs/` file from any code
path, so nothing ever tells you it has gone stale.

This check warns rather than fails. It is a norm, not a wall -- but the warning
is loud, and CI turns it into a pull-request annotation so it is visible where
the decision is actually made.
"""

from __future__ import annotations

import subprocess
import warnings
from fnmatch import fnmatch
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent

# Exact paths that are canonical documentation.
CANONICAL_DOCS = frozenset(
    {
        "README.md",
        "CLAUDE.md",
        "docs/PRD.md",
        "docs/RAG.md",
    }
)

# Patterns that are canonical by construction.
#   adr/       -- numbered, dated, immutable; the right home for a real decision
#   reference/ -- explicitly historical, never updated
#   README     -- lives beside its code, so you see it when you change that code
CANONICAL_PATTERNS = (
    "docs/adr/*.md",
    "docs/reference/*",
    "docs/reference/**/*",
    "src/*/README.md",
    "src/*/*/README.md",
    "evals/*/README.md",
)

GUIDANCE = """
  Canonical documentation is a closed set. For anything else:

    a decision or trade-off  ->  docs/adr/NNN-short-title.md
    a changed contract       ->  edit the one canonical doc that owns it
    a plan, status, or TODO  ->  the pull-request description or an issue
    superseded material      ->  docs/reference/

  A plan in docs/ silently claims to be current forever. The same words in a
  PR body are dated by construction and never mislead anyone.
"""


def tracked_markdown() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "*.md"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return sorted(line.strip() for line in result.stdout.splitlines() if line.strip())


def is_canonical(path: str) -> bool:
    return path in CANONICAL_DOCS or any(fnmatch(path, pattern) for pattern in CANONICAL_PATTERNS)


def line_count(path: str) -> int:
    return len((REPO_ROOT / path).read_text(encoding="utf-8", errors="replace").splitlines())


def test_documentation_stays_in_the_canonical_set() -> None:
    unexpected = [path for path in tracked_markdown() if not is_canonical(path)]
    if not unexpected:
        return

    listing = "\n".join(f"    {path:<55} {line_count(path):>5} lines" for path in unexpected)
    warnings.warn(
        f"\n\n  {len(unexpected)} documentation file(s) outside the canonical set:\n\n"
        f"{listing}\n{GUIDANCE}",
        UserWarning,
        stacklevel=2,
    )


def test_report_canonical_documentation_ratio() -> None:
    """Report the ratio so the trend is visible, not just individual files."""
    tracked = tracked_markdown()
    archived = [p for p in tracked if p.startswith("docs/reference/")]
    live = [p for p in tracked if p not in archived]
    canonical = [p for p in live if is_canonical(p)]

    live_lines = sum(line_count(p) for p in live)
    canonical_lines = sum(line_count(p) for p in canonical)
    if live_lines == 0:
        return

    ratio = canonical_lines / live_lines
    if ratio < 0.75:
        warnings.warn(
            f"\n\n  Canonical documentation is {ratio:.0%} of {live_lines} live markdown lines "
            f"({canonical_lines} canonical, {live_lines - canonical_lines} other).\n"
            f"  Archived and excluded from this ratio: {len(archived)} file(s).\n"
            f"{GUIDANCE}",
            UserWarning,
            stacklevel=2,
        )


def main() -> int:
    """Emit GitHub Actions annotations so the warning is visible on the diff.

    Always exits 0. The point is to put the note where the decision is made,
    not to block the build.
    """
    unexpected = [path for path in tracked_markdown() if not is_canonical(path)]
    for path in unexpected:
        print(
            f"::warning file={path}::{line_count(path)} lines outside the canonical "
            "documentation set. A decision belongs in docs/adr/; a contract change in "
            "the doc that owns it; a plan or status in the PR description."
        )
    if unexpected:
        total = sum(line_count(path) for path in unexpected)
        print(f"::notice::{len(unexpected)} non-canonical documentation file(s), {total} lines.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
