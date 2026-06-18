"""CLI entry point for email -> Paperless import pipeline."""
import argparse
import os
import sys
from pathlib import Path

# Add parent to path for script usage
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from paperless_import.config import load_config
from paperless_import.himalaya import fetch_message, move_message, list_all_envelopes
from paperless_import.paperless import PaperlessClient
from paperless_import.pdf import generate_order_pdf
from paperless_import.parsers.amazon import AmazonParser


def get_registered_parsers():
    """Return list of available email parsers. Add new parsers here."""
    return [AmazonParser()]


def resolve_folder(email_id: int, inbox_ids: set) -> str:
    return "INBOX" if email_id in inbox_ids else "[Gmail]/All Mail"


def main():
    cfg = load_config()
    parsers = get_registered_parsers()

    # OneCLI setup
    proxy_url = cfg.get("onecli", {}).get("proxy_url", "")
    ca_cert = cfg.get("onecli", {}).get("ca_cert", "/workspace/onecli-ca.pem")
    if not proxy_url:
        print("Error: No OneCLI proxy configured. Set ONECLI_TOKEN or config.yaml")
        sys.exit(1)

    # Paperless client
    pl = PaperlessClient(
        base_url=cfg.get("paperless", {}).get("base_url", ""),
        proxy_url=proxy_url,
        ca_cert=ca_cert,
    )

    amazon_cfg = cfg.get("amazon", {})
    cf_cfg = cfg.get("custom_fields", {})
    archive_folder = cfg.get("archive_folder", "Hermes-Archive-Order")

    parser = argparse.ArgumentParser(description="Import order emails into Paperless-ngx")
    sub = parser.add_subparsers(dest="command")

    # import
    cmd_import = sub.add_parser("import", help="Import specific email IDs")
    cmd_import.add_argument("--email-ids", type=int, nargs="+", help="Email IDs to process")
    cmd_import.add_argument("--folder", default="", help="Source folder (auto-detected if empty)")

    # bulk-amazon
    cmd_bulk = sub.add_parser("bulk-amazon", help="Import all unprocessed Amazon orders")
    cmd_bulk.add_argument("--dry-run", action="store_true", help="Only list what would be imported")

    # list
    cmd_list = sub.add_parser("list", help="Show available parsers and config")
    cmd_list.add_argument("--emails", action="store_true", help="List unprocessed Amazon orders")

    args = parser.parse_args()

    if args.command == "list":
        print("Registered parsers:")
        for p in parsers:
            print(f"  {p.__class__.__name__}")
        print()
        print("Config source:", os.environ.get("PAPERLESS_IMPORT_CONFIG", "env vars"))
        print(f"  Paperless URL: {cfg.get('paperless', {}).get('base_url', 'N/A')}")
        print(f"  Amazon correspondent: {amazon_cfg.get('correspondent_id', 'N/A')}")
        print(f"  Tags: {amazon_cfg.get('tags', [])}")
        print(f"  Archive folder: {archive_folder}")
        if args.emails:
            print()
            process_bulk_amazon(pl, parsers, cfg, dry_run=True)
        return

    if args.command == "import":
        if not args.email_ids:
            print("Provide --email-ids")
            sys.exit(1)
        process_emails(args.email_ids, args.folder, pl, parsers, cfg)

    elif args.command == "bulk-amazon":
        process_bulk_amazon(pl, parsers, cfg, dry_run=args.dry_run)

    else:
        parser.print_help()


def process_emails(email_ids, folder_override, pl, parsers, cfg):
    """Process a list of email IDs."""
    amazon_cfg = cfg.get("amazon", {})
    cf_cfg = cfg.get("custom_fields", {})
    archive_folder = cfg.get("archive_folder", "Hermes-Archive-Order")

    amazon_parser = next((p for p in parsers if isinstance(p, AmazonParser)), None)
    if not amazon_parser:
        print("Amazon parser not found")
        return

    for eid in email_ids:
        print(f"\n  ID {eid} ...", end=" ", flush=True)

        # Determine folder
        if folder_override:
            folder = folder_override
        else:
            inbox_check = __import__("paperless_import.himalaya", fromlist=["list_all_envelopes"])
            inbox_ids = {e["id"] for e in list_all_envelopes("INBOX", "from auto-confirm@amazon.com")}
            folder = "INBOX" if eid in inbox_ids else "[Gmail]/All Mail"

        raw = fetch_message(folder, eid)
        if not raw:
            print("fetch fail"); continue

        # Get subject/date from raw headers
        from email import message_from_string
        msg = message_from_string(raw)
        subject = msg.get("Subject", "")
        from_addr = msg.get("From", "")
        date_str = msg.get("Date", "")

        order = amazon_parser.parse(raw, subject, from_addr, date_str)
        if not order:
            print("parse fail"); continue

        # Generate PDF
        pdf_path = generate_order_pdf(
            merchant=order.merchant,
            title=order.pdf_title,
            order_date=order.order_date or "",
            order_number=order.order_number or "",
            total=order.total,
            shipping=order.shipping,
            tax=order.tax,
            items=[{"name": i.name, "qty": i.qty, "price": i.price} for i in order.items],
            source_subject=order.subject,
        )

        # Build custom fields
        cfs = {}
        if order.total is not None:   cfs[str(cf_cfg.get("order_total", 3))] = order.total
        if order.order_number:        cfs[str(cf_cfg.get("order_number", 4))] = order.order_number
        if order.order_date:          cfs[str(cf_cfg.get("order_date", 5))] = order.order_date
        if order.shipping is not None: cfs[str(cf_cfg.get("shipping_cost", 6))] = order.shipping
        if order.tax is not None:     cfs[str(cf_cfg.get("tax_amount", 7))] = order.tax
        if order.item_count is not None: cfs[str(cf_cfg.get("item_count", 8))] = order.item_count

        # Upload
        ok = pl.upload_document(
            pdf_path=pdf_path,
            title=order.title,
            correspondent_id=amazon_cfg.get("correspondent_id", 0),
            document_type_id=amazon_cfg.get("document_type_id", 0),
            tags=amazon_cfg.get("tags", []),
            created=order.order_date or "",
            custom_fields=cfs,
        )

        try:
            os.unlink(pdf_path)
        except OSError:
            pass

        if ok:
            move_message(eid, folder, archive_folder)
            print(order.title[:60])
        else:
            print("upload fail")


def process_bulk_amazon(pl, parsers, cfg, dry_run=False):
    """Bulk import all unprocessed Amazon orders."""
    amazon_cfg = cfg.get("amazon", {})
    archive_folder = cfg.get("archive_folder", "Hermes-Archive-Order")

    print("Discovering Amazon auto-confirm emails...")
    all_emails = list_all_envelopes("[Gmail]/All Mail", "from auto-confirm@amazon.com")
    print(f"  Total in All Mail: {len(all_emails)}")

    archived = list_all_envelopes(archive_folder, "from auto-confirm@amazon.com")
    archived_ids = {e["id"] for e in archived}
    print(f"  Already archived: {len(archived_ids)}")

    # Inbox IDs for folder routing
    inbox_emails = list_all_envelopes("INBOX", "from auto-confirm@amazon.com")
    inbox_ids = {e["id"] for e in inbox_emails}

    to_process = [e for e in all_emails if e["id"] not in archived_ids]
    print(f"  To process: {len(to_process)}")

    if dry_run:
        for e in to_process[:20]:
            print(f"    ID {e['id']}: {e['subject'][:60]} ({e['date_str']})")
        if len(to_process) > 20:
            print(f"    ... and {len(to_process) - 20} more")
        return

    amazon_parser = next((p for p in parsers if isinstance(p, AmazonParser)), None)
    cf_cfg = cfg.get("custom_fields", {})
    success = 0
    failed = 0

    for i, em in enumerate(to_process, 1):
        eid = em["id"]
        folder = resolve_folder(eid, inbox_ids)

        print(f"  [{i}/{len(to_process)}] ID {eid} ...", end=" ", flush=True)

        raw = fetch_message(folder, eid)
        if not raw:
            print("fetch fail"); failed += 1; continue

        order = amazon_parser.parse(raw, em["subject"], em["from_addr"], em["date_str"])
        if not order:
            print("parse fail"); failed += 1; continue

        pdf_path = generate_order_pdf(
            merchant=order.merchant,
            title=order.pdf_title,
            order_date=order.order_date or "",
            order_number=order.order_number or "",
            total=order.total,
            shipping=order.shipping,
            tax=order.tax,
            items=[{"name": i.name, "qty": i.qty, "price": i.price} for i in order.items],
            source_subject=order.subject,
        )

        cfs = {}
        if order.total is not None:   cfs[str(cf_cfg.get("order_total", 3))] = order.total
        if order.order_number:        cfs[str(cf_cfg.get("order_number", 4))] = order.order_number
        if order.order_date:          cfs[str(cf_cfg.get("order_date", 5))] = order.order_date
        if order.shipping is not None: cfs[str(cf_cfg.get("shipping_cost", 6))] = order.shipping
        if order.tax is not None:     cfs[str(cf_cfg.get("tax_amount", 7))] = order.tax
        if order.item_count is not None: cfs[str(cf_cfg.get("item_count", 8))] = order.item_count

        ok = pl.upload_document(
            pdf_path=pdf_path,
            title=order.title,
            correspondent_id=amazon_cfg.get("correspondent_id", 0),
            document_type_id=amazon_cfg.get("document_type_id", 0),
            tags=amazon_cfg.get("tags", []),
            created=order.order_date or "",
            custom_fields=cfs,
        )

        try:
            os.unlink(pdf_path)
        except OSError:
            pass

        if ok:
            move_message(eid, folder, archive_folder)
            print(order.title[:60])
            success += 1
        else:
            print("upload fail")
            failed += 1

        if i % 25 == 0:
            print(f"  --- checkpoint: {i}/{len(to_process)} ({success} ok, {failed} fail) ---")

    print(f"\n  Summary: {success} ok, {failed} fail of {len(to_process)}")


if __name__ == "__main__":
    main()
