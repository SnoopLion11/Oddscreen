import base64

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa

from oddscreen import combos, devig, kalshi
from oddscreen.combos import Leg, Quote


# --- combo fair -------------------------------------------------------------

def test_game_key_groups_market_types_of_one_game():
    assert combos.game_key("KXNFLGAME-25OCT26KCLV") == combos.game_key("KXNFLTOTAL-25OCT26KCLV")
    assert combos.game_key("KXNFLGAME-25OCT26KCLV") != combos.game_key("KXNFLGAME-25OCT26PHINYG")


def test_independent_only_when_every_leg_is_a_different_game():
    a = Leg("KXNFLGAME-25OCT26KCLV-KC", "KXNFLGAME-25OCT26KCLV", "yes")
    b = Leg("KXNFLTOTAL-25OCT26KCLV-47", "KXNFLTOTAL-25OCT26KCLV", "yes")
    c = Leg("KXNFLGAME-25OCT26PHINYG-PHI", "KXNFLGAME-25OCT26PHINYG", "no")
    assert combos.independent([a, c])
    assert not combos.independent([a, b])


def test_leg_fair_is_worst_case_devig_of_that_markets_own_asks():
    lf = combos.leg_fair("yes", 0.62, 0.41)
    expect = devig.devig_market([0.62, 0.41])[0]
    assert lf.fair == expect.fair and lf.method == expect.method
    no = combos.leg_fair("no", 0.62, 0.41)
    assert no.fair == devig.devig_market([0.62, 0.41])[1].fair


def test_leg_fair_rejects_missing_unbuyable_or_thin_books():
    assert combos.leg_fair("yes", None, 0.4) is None
    assert combos.leg_fair("yes", 1.0, 0.4) is None
    assert combos.leg_fair("yes", 0.6, 0.42, yes_size=5, no_size=50, min_size=10) is None


def test_combo_fair_is_product_and_none_if_any_leg_missing():
    a, b = combos.leg_fair("yes", 0.62, 0.41), combos.leg_fair("no", 0.55, 0.48)
    assert combos.combo_fair([a, b]) == pytest.approx(a.fair * b.fair)
    assert combos.combo_fair([a, None]) is None
    assert combos.combo_fair([]) is None


# --- fees -------------------------------------------------------------------

def test_taker_fee_rounds_whole_trade_up_to_a_cent():
    # 0.07 * 10 * 0.5 * 0.5 = $0.175 -> $0.18 for the trade
    assert combos.taker_fee_per_contract(0.5, 10) == pytest.approx(0.018)
    # 0.07 * 1 * 0.5 * 0.5 = 0.0175 -> 0.02
    assert combos.taker_fee_per_contract(0.5, 1) == pytest.approx(0.02)
    assert combos.taker_fee_per_contract(0.5, 10, multiplier=0) == 0.0


# --- quotes -----------------------------------------------------------------

def test_quote_sides_map_to_requester_costs():
    q = Quote("q1", "m1", yes_bid=0.20, no_bid=0.75, first_seen_s=0.4)
    assert q.yes_cost == pytest.approx(0.25)    # buy YES by hitting the maker's NO bid
    assert q.no_cost == pytest.approx(0.80)
    assert q.mid == pytest.approx(0.225)


def test_summary_finds_best_side_ev_and_leave_one_out_consensus():
    quotes = [
        Quote("a", "m1", 0.18, 0.76, 0.3),   # YES cost 0.24, mid 0.21
        Quote("b", "m2", 0.19, 0.77, 0.5),   # YES cost 0.23, mid 0.21
        Quote("c", "m3", 0.20, 0.82, 0.9),   # YES cost 0.18, mid 0.19  <- outlier
    ]
    s = combos.summarize(quotes, fair=0.21, contracts=10, fee_multiplier=0)
    assert s.n_quoters == 3
    assert s.yes.creator == "m3" and s.yes.cost == pytest.approx(0.18)
    assert s.yes.ev_pct_fair == pytest.approx((0.21 / 0.18 - 1) * 100)
    assert s.yes.consensus == pytest.approx(0.21)               # median of the other two mids
    assert s.yes.edge_vs_consensus_c == pytest.approx(3.0)
    assert s.no.cost == pytest.approx(0.80)                      # 1 - best yes_bid
    assert s.no.ev_pct_fair == pytest.approx((0.79 / 0.80 - 1) * 100)


def test_summary_keeps_each_quoters_latest_quote_only():
    quotes = [Quote("a1", "m1", 0.10, 0.70, 0.2), Quote("a2", "m1", 0.18, 0.78, 1.5)]
    s = combos.summarize(quotes, fair=None, contracts=10)
    assert s.n_quotes == 2 and s.n_quoters == 1
    assert s.yes.cost == pytest.approx(0.22)
    assert s.yes.ev_pct_fair is None and s.yes.consensus is None


def test_summary_fee_reduces_ev():
    q = [Quote("a", "m1", 0.40, 0.55, 0.1)]
    pre = combos.summarize(q, 0.5, 10, fee_multiplier=0).yes
    post = combos.summarize(q, 0.5, 10, fee_multiplier=1).yes
    assert post.fee > 0 and post.ev_pct_fair < pre.ev_pct_fair_prefee


# --- kalshi client safety and parsing ---------------------------------------

@pytest.mark.parametrize("method,path", [
    ("PUT", "/communications/rfqs/r1/quotes/q1/accept"),
    ("PUT", "/communications/quotes/q1/accept"),
    ("PUT", "/communications/rfqs/r1/quotes/q1/confirm"),
    ("POST", "/portfolio/orders"),
    ("POST", "/communications/quotes"),
    ("DELETE", "/communications/rfqs/r1/quotes/q1"),
    ("DELETE", "/markets/ABC"),
    ("GET", "/portfolio/balance"),
])
def test_trading_paths_are_refused(method, path):
    with pytest.raises(PermissionError):
        kalshi.check_allowed(method, path)


@pytest.mark.parametrize("method,path", [
    ("GET", "/markets/KXNFLGAME-25OCT26KCLV-KC/orderbook"),
    ("GET", "/multivariate_event_collections/KXMVENFL-25"),
    ("POST", "/multivariate_event_collections/KXMVENFL-25"),
    ("POST", "/communications/rfqs"),
    ("GET", "/communications/quotes"),
    ("DELETE", "/communications/rfqs/abc-123"),
])
def test_recorder_paths_are_allowed(method, path):
    kalshi.check_allowed(method, path)


def test_client_refuses_before_any_request():
    signer = kalshi.Signer("k", ed25519.Ed25519PrivateKey.generate())
    with pytest.raises(PermissionError):
        kalshi.Client(signer, base="http://127.0.0.1:9").request("POST", "/portfolio/orders")


def test_rsa_pss_signature_verifies_and_ignores_query_string():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    s = kalshi.Signer("k", key)
    sig = s.sign("1700000000000", "GET", "/trade-api/v2/communications/quotes?rfq_id=x")
    key.public_key().verify(
        base64.b64decode(sig), b"1700000000000GET/trade-api/v2/communications/quotes",
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )


def test_ed25519_signature_verifies():
    key = ed25519.Ed25519PrivateKey.generate()
    sig = base64.b64decode(kalshi.Signer("k", key).sign("1", "POST", "/trade-api/v2/communications/rfqs"))
    key.public_key().verify(sig, b"1POST/trade-api/v2/communications/rfqs")
    with pytest.raises(InvalidSignature):
        key.public_key().verify(sig, b"1POST/communications/rfqs")


def test_best_asks_from_bids_only_book():
    ob = {"orderbook_fp": {
        "yes_dollars": [["0.3800", "50.00"], ["0.4100", "12.00"]],
        "no_dollars": [["0.5200", "30.00"], ["0.5600", "8.00"]],
    }}
    ya, ys, na, ns = kalshi.best_asks(ob)
    assert ya == pytest.approx(0.44) and ys == 8.0     # 1 - best NO bid
    assert na == pytest.approx(0.59) and ns == 12.0    # 1 - best YES bid
    assert kalshi.best_asks({"orderbook_fp": {"yes_dollars": [], "no_dollars": None}}) == (None, 0.0, None, 0.0)


def test_dollars_reads_dollar_strings_and_legacy_cents():
    assert kalshi.dollars({"yes_bid_dollars": "0.2300"}, "yes_bid") == pytest.approx(0.23)
    assert kalshi.dollars({"yes_bid": 23}, "yes_bid") == pytest.approx(0.23)
    assert kalshi.dollars({}, "yes_bid") is None


def test_signer_loads_pem_text_including_one_line_escaped_form():
    from cryptography.hazmat.primitives import serialization
    key = ed25519.Ed25519PrivateKey.generate()
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    mangled = (pem, pem.replace("\n", "\\n"), pem.replace("\n", " "), " ".join(pem.split()), f'"{pem}"')
    for text in mangled:
        sig = base64.b64decode(kalshi.Signer.from_pem("k", text).sign("1", "GET", "/x"))
        key.public_key().verify(sig, b"1GET/x")


def test_rsa_pem_survives_being_flattened():
    from cryptography.hazmat.primitives import serialization
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                            serialization.NoEncryption()).decode()
    s = kalshi.Signer.from_pem("k", pem.replace("\n", " "))
    sig = base64.b64decode(s.sign("1", "GET", "/x"))
    key.public_key().verify(sig, b"1GET/x",
                            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
                            hashes.SHA256())


def test_signer_loads_bare_key_body_without_begin_end_lines():
    from cryptography.hazmat.primitives import serialization
    key = ed25519.Ed25519PrivateKey.generate()
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    body = pem.splitlines()[1]                     # the one middle line
    sig = base64.b64decode(kalshi.Signer.from_pem("k", body).sign("1", "GET", "/x"))
    key.public_key().verify(sig, b"1GET/x")


def test_leg_fair_rejects_a_market_with_an_empty_side():
    assert combos.leg_fair("yes", 0.18, 0.99, max_vig=0.06) is None
    assert combos.leg_fair("yes", 0.52, 0.51, max_vig=0.06) is not None
