"""Extract marked fenced code blocks from a Markdown file.

CI runs the README's OWN commands rather than a copy, so the doc and the check cannot
drift. Fails closed: no marked block -> exit 2, and the build goes red.

Lives under ``tests/`` so MP-11's ``test_docs_claims.py`` can import the same parser —
one parser, several consumers, rather than a second README grammar to keep in step.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def extract(md: str, marker: str) -> list[str]:
    """Bodies of every fenced block immediately preceded by ``<!-- {marker} -->``."""
    needle, lines, out, i = f"<!-- {marker} -->", md.splitlines(), [], 0
    while i < len(lines):
        if lines[i].strip() == needle:
            j = i + 1
            while j < len(lines) and not lines[j].lstrip().startswith("```"):
                j += 1
            if j >= len(lines):
                break
            k, body = j + 1, []
            while k < len(lines) and not lines[k].strip().startswith("```"):
                body.append(lines[k])
                k += 1
            out.append("\n".join(body))
            i = k
        i += 1
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--marker", required=True)
    ap.add_argument("--out")
    a = ap.parse_args()
    blocks = extract(Path(a.path).read_text(encoding="utf-8"), a.marker)
    if not blocks:
        print(f"{a.path}: no fenced block carries <!-- {a.marker} -->.", file=sys.stderr)
        print("The doc changed and this check no longer covers anything.", file=sys.stderr)
        return 2
    text = "\n\n".join(blocks) + "\n"
    if a.out:
        # newline="\n" is load-bearing: a text-mode write on Windows turns `\` + LF into
        # `\` + CRLF, which bash reads as an escaped CR, silently splitting the commands.
        with open(a.out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
