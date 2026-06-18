"""CLI entry point for email -> Paperless import pipeline."""
import argparse
import logging
import os
import sys
from pathlib import Path
from email import message_from_string

# Add parent to path for script usage
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from paperless_import.config import load_config, load_state, save_state
from paperless_import.himalaya import fetch_message, move_message, list_all_envelopes
from paperless_import.paperless import PaperlessClient
from paperless_import.pdf import generate_order_pdf
from paperless_import.parsers.amazon import AmazonParser

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def get_registered_parsers():
    """Return list of available email parsers. Add new parsers here."""
    return [AmazonParser()]


def init_heuristics(cfg):
    """Initialize tag classifier and resolver from config. Returns (classifier, resolver) or (None, None)."""
    h_cfg = cfg.get("tag_heuristics", {})
    if not h_cfg.get("enabled", False):
        logger.info("Tag heuristics disabled in config")
        return None, None

    min_confidence = h_cfg.get("min_confidence", 0.55)
    auto_create = h_cfg.get("auto_create_tags", True)
    cat_config = h_cfg.get("categories", {})

    try:
        from paperless_import.tag_heuristics import SemanticTagClassifier, TagResolver

        classifier = SemanticTagClassifier(min_confidence=min_confidence)
        resolver = TagResolver(
            pl_client=None,  # set after PaperlessClient init
            categories=cat_config,
            auto_create=auto_create,
        )
        logger.info(
            f"Tag heuristics enabled (min_confidence={min_confidence}, "
            f"auto_create_tags={auto_create})"
        )
        return classifier, resolver
    except ImportError as e:
        logger.warning(
            f"sentence-transformers not available, heuristics disabled: {e}"
        )
        return None, None
    except Exception as e:
        logger.warning(f"Failed to initialize tag heuristics: {e}")
        return None, None


def resolve_folder(email_id: int, inbox_ids: set) -> str:
    return "INBOX" if email_id in inbox_ids else "[Gmail]/All Mail"


def run_classifier(classifier, order):
    """Run heuristic classification on parsed order data."""
    if not classifier or not order or not order.items:
        return

    item_names = [i.name for i in order.items if i.name]
    if not item_names:
        return

    try:
        result = classifier.classify(item_names)
        if result:
            order.suggested_tags = list(result.keys())
            logger.info(
                f"  Heuristic tags: {order.suggested_tags} (from {len(item_names)} items)"
            )
    except Exception as e:
        logger.warning(f"Classification failed: {e}")


def resolve_heuristic_tag_ids(resolver, order):
    """Resolve suggested tag names to Paperless tag IDs."""
    if not resolver or not order or not order.suggested_tags:
        return []

    try:
        return resolver.resolve(order.suggested_tags)
    except Exception as e:
        logger.warning(f"Tag resolution failed: {e}")
        return []


def build_tags(base_tags, heuristic_ids):
    """Combine base and heuristic tags, deduplicating."""
    seen = set(base_tags)
    all_tags = list(base_tags)
    for tid in heuristic_ids:
        if tid not in seen:
            seen.add(tid)
            all_tags.append(tid)
    return all_tags


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

    # Initialize tag heuristics (handles missing deps gracefully)
    classifier, resolver = init_heuristics(cfg)
    # Wire Paperless client into resolver once it's created
    if resolver is not None:
        resolver._pl = pl

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
    cmd_bulk.add_argument("--limit", type=int, default=0, help="Max emails to process (for test runs)")

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
        print(f"  Tag heuristics enabled: {classifier is not None}")
        if args.emails:
            print()
            process_bulk_amazon(pl, parsers, cfg, classifier, resolver, dry_run=True)
        return

    if args.command == "import":
        if not args.email_ids:
            print("Provide --email-ids")
            sys.exit(1)
        process_emails(args.email_ids, args.folder, pl, parsers, cfg, classifier, resolver)

    elif args.command == "bulk-amazon":
        process_bulk_amazon(pl, parsers, cfg, classifier, resolver, dry_run=args.dry_run, limit=args.limit)

    else:
        parser.print_help()


def process_emails(email_ids, folder_override, pl, parsers, cfg, classifier=None, resolver=None):
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
            inbox_ids = {e["id"] for e in list_all_envelopes("INBOX", "from auto-confirm@amazon.com")}
            folder = "INBOX" if eid in inbox_ids else "[Gmail]/All Mail"

        raw = fetch_message(folder, eid)
        if not raw:
            print("fetch fail"); continue

        # Get subject/date from raw headers
        msg = message_from_string(raw)
        subject = msg.get("Subject", "")
        from_addr = msg.get("From", "")
        date_str = msg.get("Date", "")

        order = amazon_parser.parse(raw, subject, from_addr, date_str)
        if not order:
            print("parse fail"); continue

        # --- Tag heuristics ---
        run_classifier(classifier, order)
        heuristic_ids = resolve_heuristic_tag_ids(resolver, order)
        all_tags = build_tags(amazon_cfg.get("tags", []), heuristic_ids)

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
            order_url=order.order_url or "",
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
            tags=all_tags,
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


def process_bulk_amazon(pl, parsers, cfg, classifier=None, resolver=None, dry_run=False, limit=0):
    """Bulk import all unprocessed Amazon orders.

    Uses Message-ID for dedup (writes state to JSON file) instead of
    folder-scoped IMAP UIDs (which change when moving between Gmail folders).
    """
    amazon_cfg = cfg.get("amazon", {})
    archive_folder = cfg.get("archive_folder", "Hermes-Archive-Order")
    state_file = cfg.get("state_file", os.path.expanduser("~/.paperless-import-state.json"))

    # Load previously processed Message-IDs
    processed_msg_ids = load_state(state_file)
    print(f"Previously processed (by Message-ID): {len(processed_msg_ids)}")

    print("Discovering Amazon auto-confirm emails...")
    all_emails = list_all_envelopes("[Gmail]/All Mail", "from auto-confirm@amazon.com")
    print(f"  Total in All Mail: {len(all_emails)}")

    # Inbox IDs for folder routing
    inbox_emails = list_all_envelopes("INBOX", "from auto-confirm@amazon.com")
    inbox_ids = {e["id"] for e in inbox_emails}

    candidates = all_emails[:]
    if limit > 0:
        candidates = candidates[:limit]
    print(f"  Candidates: {len(candidates)}")

    if dry_run:
        print(f"\nWould process {len(candidates)} emails (in batches of 100)")
        for e in candidates[:10]:
            print(f"    ID {e['id']}: {e['subject'][:60]} ({e['date_str']})")
        if len(candidates) > 10:
            print(f"    ... and {len(candidates) - 10} more")
        return

    amazon_parser = next((p for p in parsers if isinstance(p, AmazonParser)), None)
    cf_cfg = cfg.get("custom_fields", {})
    success = 0
    failed = 0
    skipped = 0
    new_msg_ids = set()
    batch_msg_ids = set()

    for i, em in enumerate(candidates, 1):
        eid = em["id"]
        folder = resolve_folder(eid, inbox_ids)

        print(f"  [{i}/{len(candidates)}] ID {eid} ...", end=" ", flush=True)

        raw = fetch_message(folder, eid)
        if not raw:
            print("fetch fail"); failed += 1; continue

        # Extract Message-ID for dedup
        msg = message_from_string(raw)
        msg_id = msg.get("Message-ID", "").strip().strip("<>")

        if msg_id and msg_id in processed_msg_ids:
            print("skip (Message-ID already processed)")
            skipped += 1
            continue

        order = amazon_parser.parse(raw, em["subject"], em["from_addr"], em["date_str"])
        if not order:
            print("parse fail"); failed += 1; continue

        if msg_id:
            batch_msg_ids.add(msg_id)

        # --- Tag heuristics ---
        run_classifier(classifier, order)
        heuristic_ids = resolve_heuristic_tag_ids(resolver, order)
        all_tags = build_tags(amazon_cfg.get("tags", []), heuristic_ids)

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
            order_url=order.order_url or "",
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
            tags=all_tags,
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

        # Save state every 25 to prevent data loss on crash
        if i % 25 == 0:
            processed_msg_ids.update(batch_msg_ids)
            save_state(state_file, processed_msg_ids)
            batch_msg_ids = set()
            print(f"  --- checkpoint: {i}/{len(candidates)} ({success} ok, {failed} fail, {skipped} skip) ---")

    # Final save
    processed_msg_ids.update(batch_msg_ids)
    save_state(state_file, processed_msg_ids)
    print(f"\n  Summary: {success} ok, {failed} fail, {skipped} skip of {len(candidates)}")
    print(f"  Total Message-IDs tracked: {len(processed_msg_ids)}")


if __name__ == "__main__":
    main()
