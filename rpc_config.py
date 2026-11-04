"""Shared RPC endpoint configuration for the BSC workers."""

import os
from pathlib import Path

from dotenv import load_dotenv


load_dotenv(Path(__file__).with_name(".env"))


def add_fallback_rpc_urls(default_urls):
    """Insert private fallback endpoints before public fallback providers."""
    extra_urls = [
        url.strip()
        for url in os.getenv("BSC_RPC_FALLBACK_URLS", "").split(",")
        if url.strip()
    ]
    fallback_index = next(
        (index for index, url in enumerate(default_urls) if "rpc.ankr.com" in url),
        len(default_urls),
    )
    return list(dict.fromkeys(
        list(default_urls[:fallback_index])
        + extra_urls
        + list(default_urls[fallback_index:])
    ))
