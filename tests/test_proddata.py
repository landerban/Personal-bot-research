"""
Stage 16 Part B: the rails on the production data client.

The client is allowed to name a production host ONLY because it cannot sign
and cannot POST. These tests are what makes that "only" true rather than
asserted, so they are deliberately adversarial: they try to make the module
sign, try to make it POST, and try to reach a path off the allow-list.
"""

from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from live import proddata  # noqa: E402
from live.proddata import (  # noqa: E402
    ALLOWED_PATHS, PROD_BASE, ProdDataClient, ReadOnlyViolation,
    assert_execution_is_simulated,
)


def test_module_imports_no_crypto_and_names_no_credential():
    """There must be no signing PATH, not merely an unused one."""
    src = Path(proddata.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(n.name.split(".")[0] for n in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    for banned in ("hmac", "hashlib", "cryptography", "secrets"):
        assert banned not in imported, f"{banned} imported: a signing path exists"

    # no constructor may accept a credential, even optionally
    sig = inspect.signature(ProdDataClient.__init__)
    for name in sig.parameters:
        assert not any(w in name.lower() for w in ("key", "secret", "token",
                                                   "cred", "sign")), name
    print("PASS proddata_no_crypto_no_credential")


def test_cannot_sign_even_with_credentials_in_the_environment(monkeypatch):
    """The adversarial case: keys present, and it still cannot sign."""
    monkeypatch.setenv("BINANCE_TESTNET_KEY", "x" * 64)
    monkeypatch.setenv("BINANCE_TESTNET_SECRET", "y" * 64)
    monkeypatch.setenv("BINANCE_API_KEY", "z" * 64)
    c = ProdDataClient()
    assert not hasattr(c, "_secret") and not hasattr(c, "_key")
    with pytest.raises(ReadOnlyViolation, match="cannot sign"):
        c.signed("GET", "/fapi/v2/account")
    print("PASS proddata_cannot_sign_with_keys_present")


def test_cannot_post_or_trade():
    c = ProdDataClient()
    for method in ("post", "place_order", "cancel_order", "cancel_all"):
        with pytest.raises(ReadOnlyViolation, match="cannot POST"):
            getattr(c, method)(symbol="BTCUSDT", side="BUY")
    print("PASS proddata_cannot_post")


def test_only_allow_listed_paths_are_reachable():
    c = ProdDataClient()
    for path in ("/fapi/v1/order", "/fapi/v2/account", "/fapi/v1/positionRisk",
                 "/fapi/v1/listenKey", "/fapi/v1/userTrades"):
        with pytest.raises(ReadOnlyViolation, match="allow-list"):
            c.get(path)
    assert "/fapi/v1/order" not in ALLOWED_PATHS
    assert "/fapi/v2/account" not in ALLOWED_PATHS
    assert "/fapi/v1/klines" in ALLOWED_PATHS
    print(f"PASS proddata_allow_list ({len(ALLOWED_PATHS)} read paths)")


def test_get_is_the_only_verb_the_transport_uses():
    """Every outbound request must be built with method='GET'."""
    src = Path(proddata.__file__).read_text(encoding="utf-8")
    assert 'method="GET"' in src
    for verb in ('"POST"', '"PUT"', '"DELETE"', '"PATCH"'):
        assert f"method={verb}" not in src, f"a {verb} request is constructed"
    print("PASS proddata_get_only_transport")


def test_refuses_a_non_tls_base():
    with pytest.raises(ValueError, match="non-TLS"):
        ProdDataClient(base_url="http://fapi.example.com")
    assert PROD_BASE.startswith("https://")
    print("PASS proddata_tls_only")


def test_production_feed_may_only_pair_with_simulated_execution():
    """NOTES 56.1's combination rule, as a startup refusal rather than a
    warning: the safety argument for reading production data is that nothing
    can act on it."""
    assert_execution_is_simulated("production", "simulated")   # allowed
    assert_execution_is_simulated("testnet", "live")           # unrelated
    for execution in ("live", "real", "testnet"):
        with pytest.raises(ReadOnlyViolation, match="ONLY with simulated"):
            assert_execution_is_simulated("production", execution)
    print("PASS proddata_feed_execution_pairing")


def test_the_trading_client_still_cannot_reach_production():
    """The narrowing must not have leaked: TestnetClient is unchanged."""
    from live.client import TESTNET_HOSTS, assert_testnet_url

    assert PROD_BASE.split("/")[2] not in TESTNET_HOSTS
    with pytest.raises(ValueError):
        assert_testnet_url(PROD_BASE, True)
    print("PASS proddata_trading_client_unchanged")
