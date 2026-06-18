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
    return run(["message", "read", str(email_id), "-f", folder])


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
        if len(parts) >= 5:
            try:
                emails.append({
                    "id": int(parts[1]),
                    "subject": parts[2],
                    "from_addr": parts[3],
                    "date_str": parts[4],
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
    """Paginate through all matching envelopes."""
    all_emails = []
    page = 1
    while True:
        batch = list_envelopes(folder, query, page=page)
        if not batch:
            break
        all_emails.extend(batch)
        if len(batch) < 500:
            break
        page += 1
    return all_emails
