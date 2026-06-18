# paperless-email-import

Import email order confirmations into Paperless-ngx as structured PDF documents with metadata.

## Pipeline

```
Gmail (via Himalaya IMAP) → Parse order data → Generate PDF → Upload to Paperless → Archive email
```

## Setup

```bash
pip install -r requirements.txt
```

### Configuration

Set environment variables or create a `config.yaml` (see `config.yaml.example`):

```bash
# OneCLI proxy (for Paperless auth)
export ONECLI_TOKEN="aoc_..."

# Paperless metadata IDs
export PAPERLESS_CORRESPONDENT_AMAZON=77
export PAPERLESS_DOCTYPE_RECEIPT=17
export PAPERLESS_TAGS="28,26,27,30,29"
export PAPERLESS_URL="https://paperless.example.com/api"
```

### Himalaya

Requires [himalaya](https://github.com/soywod/himalaya) v1.x with IMAP account configured.

## Usage

```bash
# List available parsers and config
python -m paperless_import.cli list

# Process specific email IDs
python -m paperless_import.cli import --email-ids 12345 12346

# Bulk import all unprocessed Amazon orders (dry-run first)
python -m paperless_import.cli bulk-amazon --dry-run
python -m paperless_import.cli bulk-amazon
```

## Adding a Merchant Parser

1. Create `paperless_import/parsers/merchantname.py`
2. Extend `BaseParser` and implement `matches()` and `parse()`
3. Register in `paperless_import/cli.py` `get_registered_parsers()`

## Custom Field Schema

| ID | Name         | Type    | Description        |
|----|--------------|---------|--------------------|
| 3  | Order Total  | float   | Grand total paid   |
| 4  | Order Number | string  | Merchant order ID  |
| 5  | Order Date   | date    | When ordered       |
| 6  | Shipping Cost| float   | Separate shipping  |
| 7  | Tax Amount   | float   | Sales tax          |
| 8  | Item Count   | integer | Number of items    |

## License

MIT
