"""Himalaya IMAP email client wrapper."""
import subprocess
import re
from typing import Optional


def run(args: list[str], quiet: bool = True) -> Optional[str]:
    """Run a himalaya command and return stdout, or None on error."""
    cmd = ["himalaya"]
    if quiet:
        cmd.append("--quiet")
    cmd.extend(args)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return r.stdout if r.returncode == 0 else None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def fetch_message(folder: str, email_id: int) -> Optional[str]:
    """Fetch raw MIME content of an email."""
    return run(["message", "read", str(email_id), "-f", folder,
                "-H", "Date", "-H", "Message-ID", "-H", "Content-Type"])


def move_message(email_id: int, source: str, target: str) -> bool:
    """Move an email to another folder."""
    result = run(["message", "move", target, str(email_id), "-f", source])
    return result is not None


def parse_envelope_table(output: str) -> list[dict]:
    """Parse himalaya envelope list table output into list of dicts."""
    emails = []
    for line in output.split("\n"):
        if "|" not in line:
            continue
        line = line.strip()
        if line.startswith("|---") or line.startswith("| ID"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 6:  # ID | FLAGS | SUBJECT | FROM | DATE
            try:
                emails.append({
                    "id": int(parts[1]),
                    "subject": parts[3],
                    "from_addr": parts[4],
                    "date_str": parts[5],
                })
            except ValueError:
                pass
    return emails


def list_envelopes(
    folder: str,
    query: str = "",
    page: int = 1,
    page_size: int = 500,
    sort: str = "order by date asc",
) -> list[dict]:
    """List email envelopes matching a query."""
    args = ["envelope", "list", "-f", folder, "-s", str(page_size), "-p", str(page)]
    if query:
        args.append(query)
    if sort:
        args.append(sort)
    out = run(args)
    if not out:
        return []
    return parse_envelope_table(out)


def list_all_envelopes(folder: str, query: str = "") -> list[dict]:
    """Fetch all matching envelopes using a single large page.

    Note: himalaya v1.2.0 pagination is broken when combined with query filters,
    so we use a single page_size=10000 request instead of pagination.
    """
    batch = list_envelopes(folder, query, page=1, page_size=10000)
    return batch
