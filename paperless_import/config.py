"""Configuration loader — reads from env vars with YAML file fallback."""
import os
import json
from pathlib import Path

CONFIG_PATHS = [
    os.environ.get("PAPERLESS_IMPORT_CONFIG", ""),
    str(Path.cwd() / "config.yaml"),
    str(Path.home() / ".paperless-import" / "config.yaml"),
]


def load_config():
    cfg = {}

    config_path = next((p for p in CONFIG_PATHS if p and Path(p).exists()), None)

    if config_path:
        import yaml
        with open(config_path) as f:
            cfg = yaml.safe_load(f) or {}
    else:
        cfg = {
            "onecli": {
                "ca_cert": os.environ.get("ONECLI_CA_CERT", "/workspace/onecli-ca.pem"),
            },
            "paperless": {
                "base_url": os.environ.get("PAPERLESS_URL", ""),
            },
            "amazon": {
                "correspondent_id": int(os.environ.get("PAPERLESS_CORRESPONDENT_AMAZON", "0")),
                "document_type_id": int(os.environ.get("PAPERLESS_DOCTYPE_RECEIPT", "0")),
                "tags": [int(x) for x in os.environ.get("PAPERLESS_TAGS", "").split(",") if x],
            },
            "custom_fields": {
                "order_total": int(os.environ.get("CF_ORDER_TOTAL", "3")),
                "order_number": int(os.environ.get("CF_ORDER_NUMBER", "4")),
                "order_date": int(os.environ.get("CF_ORDER_DATE", "5")),
                "shipping_cost": int(os.environ.get("CF_SHIPPING", "6")),
                "tax_amount": int(os.environ.get("CF_TAX", "7")),
                "item_count": int(os.environ.get("CF_ITEM_COUNT", "8")),
            },
            "archive_folder": os.environ.get("ARCHIVE_FOLDER", "Hermes-Archive-Order"),
            "state_file": os.environ.get("STATE_FILE", str(Path.home() / ".paperless-import-state.json")),
        }

    # Resolve OneCLI token
    token = cfg.get("onecli", {}).get("token") or os.environ.get("ONECLI_TOKEN", "")
    if not token:
        token_file = Path("/workspace/.onecli-agent-token")
        if token_file.exists():
            token = token_file.read_text().strip()
    cfg["_token"] = token

    # Build proxy URL
    proxy = cfg.get("onecli", {}).get("proxy_url")
    if not proxy and token:
        agent = cfg.get("onecli", {}).get("agent_name", "hermes")
        gateway = cfg.get("onecli", {}).get("gateway", "proxy.example.com:3128")
        proxy = f"http://{agent}:{token}@{gateway}"
        cfg.setdefault("onecli", {})["proxy_url"] = proxy

    return cfg


def load_state(state_file: str) -> set:
    """Load set of processed Message-IDs from state file."""
    path = Path(state_file)
    if path.exists():
        try:
            data = json.loads(path.read_text())
            return set(data.get("processed_message_ids", []))
        except (json.JSONDecodeError, KeyError):
            pass
    return set()


def save_state(state_file: str, processed: set):
    """Save set of processed Message-IDs to state file."""
    path = Path(state_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"processed_message_ids": list(processed)}, indent=2))
