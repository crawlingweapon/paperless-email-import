"""Configuration loader — reads from env vars with YAML file fallback."""
import os
from pathlib import Path

# Config file: looks for PAPERLESS_IMPORT_CONFIG env var, then ./config.yaml
CONFIG_PATHS = [
    os.environ.get("PAPERLESS_IMPORT_CONFIG", ""),
    str(Path.cwd() / "config.yaml"),
    str(Path.home() / ".paperless-import" / "config.yaml"),
]


def load_config():
    """Load config from YAML file or env vars."""
    config_path = next((p for p in CONFIG_PATHS if p and Path(p).exists()), None)

    cfg = {}

    if config_path:
        import yaml
        with open(config_path) as f:
            cfg = yaml.safe_load(f) or {}
    else:
        # Minimal env-var based config
        cfg = {
            "onecli": {
                "proxy_url": os.environ.get("ONECLI_PROXY_URL"),
                "ca_cert": os.environ.get("ONECLI_CA_CERT", "/workspace/onecli-ca.pem"),
            },
            "paperless": {
                "base_url": os.environ.get("PAPERLESS_URL", ""),
            },
            "himalaya": {
                "account": os.environ.get("HIMALAYA_ACCOUNT", ""),
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
        }

    # Resolve OneCLI token
    token = cfg.get("onecli", {}).get("token") or os.environ.get("ONECLI_TOKEN", "")
    if not token:
        token_file = Path("/workspace/.onecli-agent-token")
        if token_file.exists():
            token = token_file.read_text().strip()
    cfg["_token"] = token

    # Build proxy URL if not set
    proxy = cfg.get("onecli", {}).get("proxy_url")
    if not proxy and token:
        agent = cfg.get("onecli", {}).get("agent_name", "hermes")
        gateway = cfg.get("onecli", {}).get("gateway", "proxy.example.com:3128")
        proxy = f"http://{agent}:{token}@{gateway}"
        cfg.setdefault("onecli", {})["proxy_url"] = proxy

    return cfg
