"""Replace the values that identify the owner's AWS account, services and e-mail with examples, in files that are going to be public.

    python scripts/scrub_notebooks.py PATH...      rewrites each file in place and prints "PATH: N replaced"

It counts what it replaces and never prints a value it found. A notebook is JSON, so replacing text keeps it valid. `SENSITIVE` is
also what the history rewrite (the reorganization plan, Task 7) looks for in old versions of files."""
import re
import sys
from pathlib import Path

EXAMPLE_ACCOUNT = "123456789012"  # the account id AWS's own documentation uses

RULES: list[tuple[re.Pattern, str]] = [
    # an account id inside an ARN, other than the example one
    (re.compile(r"(arn:aws:[a-z0-9-]+:[a-z0-9-]*:)(?!" + EXAMPLE_ACCOUNT + r")\d{12}(?=:)"), r"\g<1>" + EXAMPLE_ACCOUNT),
    (re.compile(r"3881" r"04035559"), EXAMPLE_ACCOUNT),  # in pieces, so this file does not hold the id it looks for
    (re.compile(r"\b[a-z]{20}\.supabase\.(?:co|com)\b"), "example-project.supabase.co"),
    (re.compile(r"\b[a-z0-9]{32}\.lambda-url\.[a-z0-9-]+\.on\.aws\b"), "example.lambda-url.us-east-1.on.aws"),
    (re.compile(r"\b[a-z0-9]{13,14}\.cloudfront\.net\b"), "example.cloudfront.net"),
    (re.compile(r"sk-or-v1-[A-Za-z0-9]{20,}"), "<redacted>"),
    (re.compile(r"eyJhbGciOi[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_.-]+"), "<redacted>"),
    (re.compile(r"(postgres(?:ql)?://[^\s:/@\"']+:)(?!<redacted>|password\b|PASSWORD\b|\$|\{|\[)[^\s@\"']{6,}(@[^\s\"'/]+)"), r"\g<1><redacted>\g<2>"),
    (re.compile(r"alexguimenti\.search" r"atlas@gmail\.com"), "alexguimenti@gmail.com"),
]
SENSITIVE: list[re.Pattern] = [pattern for pattern, _ in RULES]


def scrub_text(text: str) -> tuple[str, int]:
    """The text with each sensitive value replaced by its example, and how many values that was."""
    replaced = 0
    for pattern, replacement in RULES:
        text, n = pattern.subn(replacement, text)
        replaced += n
    return text, replaced


def main(paths: list[str]) -> None:
    for name in paths:
        path = Path(name)
        raw = path.read_bytes()
        text, replaced = scrub_text(raw.decode("utf-8"))
        if replaced:
            path.write_bytes(text.encode("utf-8"))  # bytes, so a file's line endings stay as they were
        print(f"{name}: {replaced} replaced")


if __name__ == "__main__":
    main(sys.argv[1:])
