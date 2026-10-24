"""Kalshi Trade API v2 client for the log-only combo RFQ recorder.

Confirmed from docs.kalshi.com (see docs/kalshi_api.md):
  - Signing: KALSHI-ACCESS-KEY / -TIMESTAMP (ms) / -SIGNATURE headers.
    Signed string = timestamp + METHOD + full path from root
    (e.g. /trade-api/v2/communications/rfqs), query string stripped.
    RSA keys: RSA-PSS, SHA-256, MGF1(SHA-256), salt = digest length.
    Ed25519 keys (Kalshi's default): sign the same string directly.
  - Prices come back as dollar strings (`*_dollars`, e.g. "0.4200").

Safety: this client can create combo markets, create RFQs and delete its
own RFQs. It can NOT accept or confirm quotes, place orders, or create
quotes. Any path not on the allowlist raises before a request is made.
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa

DEFAULT_BASE = "https://external-api.kalshi.com/trade-api/v2"
DEMO_BASE = "https://external-api.demo.kalshi.co/trade-api/v2"

_T = r"[A-Za-z0-9._-]+"   # a ticker or id path segment

# (method, path relative to /trade-api/v2). Anything else is refused.
_ALLOWED = [
    ("GET", re.compile(r"^/exchange/status$")),
    ("GET", re.compile(r"^/markets$")),
    ("GET", re.compile(rf"^/markets/{_T}$")),
    ("GET", re.compile(rf"^/markets/{_T}/orderbook$")),
    ("GET", re.compile(r"^/multivariate_event_collections$")),
    ("GET", re.compile(rf"^/multivariate_event_collections/{_T}$")),
    # Creates (or returns) the combo market for a set of legs. Kalshi
    # requires this before an RFQ on that combo. Limit: 5000 per week.
    ("POST", re.compile(rf"^/multivariate_event_collections/{_T}$")),
    ("GET", re.compile(r"^/communications/rfqs$")),
    ("GET", re.compile(rf"^/communications/rfqs/{_T}$")),
    ("POST", re.compile(r"^/communications/rfqs$")),
    ("DELETE", re.compile(rf"^/communications/rfqs/{_T}$")),
    ("GET", re.compile(r"^/communications/quotes$")),
]

# Never allowed, even if a future allowlist edit would match them.
_FORBIDDEN = re.compile(r"/accept|/confirm|/orders|/portfolio|/quotes/")


def check_allowed(method: str, path: str) -> None:
    if _FORBIDDEN.search(path) or not any(m == method and r.match(path) for m, r in _ALLOWED):
        raise PermissionError(f"refused: {method} {path} is not allowed in the log-only recorder")


# --- signing ----------------------------------------------------------------

class Signer:
    def __init__(self, key_id: str, private_key):
        if not isinstance(private_key, (rsa.RSAPrivateKey, ed25519.Ed25519PrivateKey)):
            raise TypeError("Kalshi keys must be RSA or Ed25519")
        self.key_id = key_id
        self._key = private_key

    @classmethod
    def from_pem(cls, key_id: str, pem: str | bytes) -> "Signer":
        text = pem.decode() if isinstance(pem, bytes) else pem
        if "-----BEGIN" not in text:
            # Just the key's base64 body (e.g. the single middle line of an Ed25519 key file).
            der = base64.b64decode(re.sub(r"\s+|\\n", "", text).strip("\"'"))
            return cls(key_id, serialization.load_der_private_key(der, password=None))
        return cls(key_id, serialization.load_pem_private_key(normalize_pem(text), password=None))

    @classmethod
    def from_pem_file(cls, key_id: str, path: str | Path) -> "Signer":
        return cls.from_pem(key_id, Path(path).read_bytes())

    def sign(self, timestamp_ms: str, method: str, full_path: str) -> str:
        msg = (timestamp_ms + method + full_path.split("?", 1)[0]).encode()
        if isinstance(self._key, ed25519.Ed25519PrivateKey):
            sig = self._key.sign(msg)
        else:
            sig = self._key.sign(
                msg,
                padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
                hashes.SHA256(),
            )
        return base64.b64encode(sig).decode()

    def headers(self, method: str, full_path: str) -> dict[str, str]:
        ts = str(int(time.time() * 1000))
        return {
            "KALSHI-ACCESS-KEY": self.key_id,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": self.sign(ts, method, full_path),
        }


def normalize_pem(pem: str | bytes) -> bytes:
    """Rebuild a PEM pasted into a settings box that mangled its line breaks
    (joined onto one line, newlines turned into spaces or a literal "\\n")."""
    text = pem.decode() if isinstance(pem, bytes) else pem
    text = text.replace("\\n", "\n").strip().strip('"').strip("'")
    m = re.search(r"-----BEGIN ([A-Z0-9 ]+)-----(.*?)-----END \1-----", text, re.S)
    if not m:
        raise ValueError("private key text has no -----BEGIN ...----- / -----END ...----- lines")
    body = re.sub(r"\s+", "", m.group(2))
    lines = "\n".join(body[i:i + 64] for i in range(0, len(body), 64))
    return f"-----BEGIN {m.group(1)}-----\n{lines}\n-----END {m.group(1)}-----\n".encode()


# --- REST -------------------------------------------------------------------

class KalshiError(RuntimeError):
    def __init__(self, status: int, method: str, path: str, body):
        super().__init__(f"{method} {path} -> HTTP {status}: {str(body)[:300]}")
        self.status = status
        self.body = body


class Client:
    """Minimal REST client. Paths are relative to the API root (/trade-api/v2)."""

    def __init__(self, signer: Signer, base: str = DEFAULT_BASE, min_gap_s: float = 0.12):
        self.base = base.rstrip("/")
        self.root = urllib.parse.urlparse(self.base).path   # "/trade-api/v2"
        self.signer = signer
        self.min_gap_s = min_gap_s   # Basic tier: 10 req/s sustained; stay well below
        self._last = 0.0

    def request(self, method: str, path: str, params: Optional[dict] = None, body: Optional[dict] = None):
        check_allowed(method, path)
        full_path = self.root + path
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        headers = {"Accept": "application/json", **self.signer.headers(method, full_path)}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        wait = self.min_gap_s - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        for attempt in range(4):
            self._last = time.monotonic()
            req = urllib.request.Request(url, data=data, method=method, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=15) as r:
                    raw = r.read()
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as e:
                raw = e.read()
                if e.code == 429 and attempt < 3:      # no Retry-After header; back off
                    time.sleep(0.5 * 2 ** attempt)
                    headers.update(self.signer.headers(method, full_path))
                    continue
                try:
                    parsed = json.loads(raw)
                except ValueError:
                    parsed = raw.decode(errors="replace")
                raise KalshiError(e.code, method, path, parsed) from None
        raise AssertionError("unreachable")

    # Market data
    def market(self, ticker: str) -> dict:
        return self.request("GET", f"/markets/{ticker}").get("market", {})

    def markets(self, **params) -> list[dict]:
        out, cursor = [], None
        while True:
            page = self.request("GET", "/markets", {**params, "limit": 1000, "cursor": cursor})
            out += page.get("markets", [])
            cursor = page.get("cursor")
            if not cursor:
                return out

    def orderbook(self, ticker: str) -> dict:
        return self.request("GET", f"/markets/{ticker}/orderbook")

    # Combos
    def collections(self, **params) -> list[dict]:
        out, cursor = [], None
        while True:
            page = self.request("GET", "/multivariate_event_collections", {**params, "cursor": cursor})
            out += page.get("multivariate_contracts") or page.get("multivariate_event_collections") or []
            cursor = page.get("cursor")
            if not cursor:
                return out

    def collection(self, ticker: str) -> dict:
        page = self.request("GET", f"/multivariate_event_collections/{ticker}")
        return page.get("multivariate_contract") or page.get("multivariate_event_collection") or page

    def create_combo_market(self, collection_ticker: str, legs: list[dict]) -> dict:
        """legs: [{market_ticker, event_ticker, side}]. Returns {market_ticker, event_ticker, ...}."""
        return self.request("POST", f"/multivariate_event_collections/{collection_ticker}",
                            body={"selected_markets": legs})

    # RFQs (requester side only)
    def create_rfq(self, market_ticker: str, contracts: int) -> dict:
        return self.request("POST", "/communications/rfqs", body={
            "market_ticker": market_ticker,
            "contracts": contracts,
            "rest_remainder": False,
        })

    def quotes(self, rfq_id: str) -> list[dict]:
        return self.request("GET", "/communications/quotes", {"rfq_id": rfq_id}).get("quotes", [])

    def delete_rfq(self, rfq_id: str) -> None:
        self.request("DELETE", f"/communications/rfqs/{rfq_id}")


# --- parsing helpers ----------------------------------------------------------

def dollars(obj: dict, name: str) -> Optional[float]:
    """Read `name_dollars` (a dollar string); fall back to legacy integer cents `name`."""
    v = obj.get(f"{name}_dollars")
    if v not in (None, ""):
        return float(v)
    v = obj.get(name)
    if v not in (None, ""):
        return float(v) / 100.0
    return None


def best_asks(orderbook: dict) -> tuple[Optional[float], float, Optional[float], float]:
    """(yes_ask, yes_ask_size, no_ask, no_ask_size) from a GET /markets/{t}/orderbook response.

    Kalshi returns BIDS only, ascending, as [price_dollars, count_fp] strings.
    A NO bid at p is a YES ask at 1 - p, and vice versa.
    """
    book = orderbook.get("orderbook_fp") or orderbook.get("orderbook") or {}
    yes = book.get("yes_dollars") or []
    no = book.get("no_dollars") or []
    best_yes_bid = max(((float(p), float(q)) for p, q in yes), default=None)
    best_no_bid = max(((float(p), float(q)) for p, q in no), default=None)
    yes_ask = (1.0 - best_no_bid[0], best_no_bid[1]) if best_no_bid else (None, 0.0)
    no_ask = (1.0 - best_yes_bid[0], best_yes_bid[1]) if best_yes_bid else (None, 0.0)
    return yes_ask[0], yes_ask[1], no_ask[0], no_ask[1]


def load_env(root: Path) -> dict[str, str]:
    env = dict(os.environ)
    f = root / ".env"
    if f.exists():
        for line in f.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return env
