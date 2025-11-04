import os
import json
import logging
from pathlib import Path
from typing import Dict, Optional


logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger('exporter')


def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)


def storage_state_to_cookie_header(storage_state: Optional[Dict]) -> Dict:
    if not storage_state or 'cookies' not in storage_state:
        raise ValueError('storage_state missing cookies')
    parts = []
    for c in storage_state['cookies']:
        if 'pwc.com' in c.get('domain', ''):
            parts.append(f"{c['name']}={c['value']}")
    if not parts:
        # fallback: include all
        parts = [f"{c['name']}={c['value']}" for c in storage_state['cookies']]
    cookie = '; '.join(parts)
    return {
        'Cookie': cookie,
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36',
        'Accept': 'application/json, text/plain, */*'
    }


