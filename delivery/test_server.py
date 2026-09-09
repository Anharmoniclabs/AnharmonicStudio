import copy
import hashlib
import hmac
import json
import sqlite3
import time

from itsdangerous import URLSafeTimedSerializer
import pytest

from delivery.server import PaymentUnavailable, create_app

SID = "cs_test_paid123456789"


@pytest.fixture
def service(tmp_path):
    artifacts = {}
    for key, suffix in (
        ("windows", ".exe"),
        ("mac-arm", ".dmg"),
        ("mac-intel", ".dmg"),
        ("linux", ".tar.gz"),
    ):
        data = (key + " installer").encode()
        filename = key + suffix
        (tmp_path / filename).write_bytes(data)
        (tmp_path / (key + "-source.zip")).write_bytes(b"source and notices")
        artifacts[key] = {
            "file": filename,
            "sha256": hashlib.sha256(data).hexdigest(),
            "materials": {
                "file": key + "-source.zip",
                "sha256": hashlib.sha256(b"source and notices").hexdigest(),
            },
        }
    catalog = {"release": "0.1.0-rc.1", "artifacts": artifacts}
    (tmp_path / "catalog.json").write_text(json.dumps(catalog))
    config = {
        "STRIPE_SECRET_KEY": "sk_test_fixture",
        "STRIPE_WEBHOOK_SECRET": "whsec_fixture",
        "STRIPE_PAYMENT_LINK_ID": "plink_fixture",
        "STRIPE_PRICE_ID": "price_fixture",
        "DOWNLOAD_SIGNING_KEY": "test-only-signing-key-with-32-characters",
        "DELIVERY_CATALOG": str(tmp_path / "catalog.json"),
        "DELIVERY_DB": str(tmp_path / "purchases.sqlite"),
    }
    paid = {
        "id": SID,
        "livemode": False,
        "mode": "payment",
        "payment_link": "plink_fixture",
        "currency": "usd",
        "amount_subtotal": 100,
        "amount_total": 109,
        "payment_status": "paid",
        "status": "complete",
        "client_reference_id": "as_v1_windows",
        "line_items": {
            "has_more": False,
            "data": [{"quantity": 1, "price": {"id": "price_fixture"}}],
        },
        "payment_intent": {
            "latest_charge": {
                "paid": True,
                "refunded": False,
                "amount_refunded": 0,
                "disputed": False,
            }
        },
    }
    app = create_app(config, retrieve=lambda _sid: copy.deepcopy(paid))
    app.testing = True
    return app.test_client(), paid, config


def claim(client):
    return client.post("/api/claim", json={"session_id": SID})


@pytest.mark.parametrize("platform", ["windows", "mac-arm", "mac-intel", "linux"])
def test_paid_purchase_downloads_selected_platform_with_tax_and_sources(service, platform):
    client, paid, _ = service
    paid["client_reference_id"] = f"as_v1_{platform}"
    response = claim(client)
    assert response.status_code == 200
    assert response.json["platform"] == platform
    selected = next(item for item in response.json["downloads"] if item["platform"] == platform)
    response = client.get(selected["url"])
    assert response.data == (platform + " installer").encode()
    assert response.headers["Content-Disposition"].startswith("attachment;")
    assert "no-store" in response.headers["Cache-Control"]
    assert client.get(selected["materials_url"]).data == b"source and notices"
    resumed = client.get(selected["url"], headers={"Range": "bytes=0-3"})
    assert resumed.status_code == 206
    assert resumed.data == platform[:4].encode()


@pytest.mark.parametrize(
    "field,value",
    [
        ("livemode", True),
        ("mode", "subscription"),
        ("currency", "eur"),
        ("amount_subtotal", 99),
        ("amount_subtotal", 4500),
        ("amount_total", 50),
        ("total_details", {"amount_discount": 50}),
        ("payment_link", "plink_donation"),
        ("id", "cs_test_other123456"),
        ("line_items", {"has_more": False, "data": []}),
    ],
)
def test_wrong_payment_never_grants_downloads(service, field, value):
    client, paid, config = service
    paid[field] = value
    assert claim(client).status_code == 403
    with sqlite3.connect(config["DELIVERY_DB"]) as conn:
        assert conn.execute("SELECT count(*) FROM purchases").fetchone()[0] == 0


def test_wrong_price_and_duplicate_quantity_denied(service):
    client, paid, _ = service
    paid["line_items"]["data"][0]["price"]["id"] = "price_donation"
    assert claim(client).status_code == 403
    paid["line_items"]["data"][0]["price"]["id"] = "price_fixture"
    paid["line_items"]["data"][0]["quantity"] = 2
    assert claim(client).status_code == 403


def test_pending_payment_can_retry(service):
    client, paid, _ = service
    paid["payment_status"] = "unpaid"
    assert claim(client).status_code == 409
    paid["payment_status"] = "paid"
    assert claim(client).status_code == 200


@pytest.mark.parametrize("reference", [None, "", "../../secrets", "as_v1_unknown", "windows"])
def test_legacy_or_invalid_selection_shows_platform_chooser(service, reference):
    client, paid, _ = service
    paid["client_reference_id"] = reference
    assert claim(client).json["platform"] is None


@pytest.mark.parametrize(
    "field,value", [("refunded", True), ("amount_refunded", 1), ("disputed", True)]
)
def test_refunds_and_disputes_revoke_already_issued_links(service, field, value):
    client, paid, _ = service
    link = claim(client).json["downloads"][0]["url"]
    paid["payment_intent"]["latest_charge"][field] = value
    assert client.get(link).status_code == 403
    assert claim(client).status_code == 403


def test_tickets_cannot_be_forged_or_used_for_other_releases(service):
    client, _, config = service
    assert client.get("/files/forged/windows").status_code == 403
    signer = URLSafeTimedSerializer(config["DOWNLOAD_SIGNING_KEY"], salt="installer-v1")
    ticket = signer.dumps({"session_id": SID, "release": "other"})
    assert client.get(f"/files/{ticket}/windows").status_code == 403


def test_expired_link_can_be_refreshed(service, monkeypatch):
    client, _, _ = service
    link = claim(client).json["downloads"][0]["url"]
    now = time.time()
    with monkeypatch.context() as patch:
        patch.setattr(time, "time", lambda: now + 901)
        assert client.get(link).status_code == 403
    assert client.get(claim(client).json["downloads"][0]["url"]).status_code == 200


def test_invalid_purchase_references_and_json_denied(service):
    client, _, _ = service
    for body in ({}, {"session_id": "../../secret"}, {"session_id": []}, []):
        assert client.post("/api/claim", json=body).status_code == 400
    assert client.post("/api/claim", data="session_id=paid").status_code == 400
    assert client.get("/assets/server.py").status_code == 404
    assert client.get("/static/catalog.json").status_code == 404


def signed_event(config, timestamp, event):
    data = json.dumps(event).encode()
    signature = hmac.new(
        config["STRIPE_WEBHOOK_SECRET"].encode(),
        str(timestamp).encode() + b"." + data,
        hashlib.sha256,
    ).hexdigest()
    return {
        "data": data,
        "content_type": "application/json",
        "headers": {"Stripe-Signature": f"t={timestamp},v1={signature}"},
    }


def test_webhooks_and_return_visits_fulfill_only_once(service):
    client, paid, config = service
    event = {"type": "checkout.session.completed", "data": {"object": paid}}
    for _ in range(3):
        assert (
            client.post("/webhook", **signed_event(config, int(time.time()), event)).status_code
            == 200
        )
        assert claim(client).status_code == 200
    with sqlite3.connect(config["DELIVERY_DB"]) as conn:
        assert conn.execute("SELECT count(*) FROM purchases").fetchone()[0] == 1


def test_webhooks_require_fresh_authentic_signature(service):
    client, paid, config = service
    event = {"type": "checkout.session.completed", "data": {"object": paid}}
    assert client.post("/webhook", json=event).status_code == 400
    assert (
        client.post("/webhook", **signed_event(config, int(time.time()) - 301, event)).status_code
        == 400
    )
    signed = signed_event(config, int(time.time()), event)
    signed["data"] += b" "
    assert client.post("/webhook", **signed).status_code == 400


def test_stripe_outage_does_not_grant_access_or_leak_secrets(service):
    _, _, config = service

    def unavailable(_sid):
        raise PaymentUnavailable("private diagnostic")

    client = create_app(config, retrieve=unavailable).test_client()
    response = claim(client)
    assert response.status_code == 503
    assert "private diagnostic" not in response.text
    assert config["STRIPE_SECRET_KEY"] not in response.text


def test_startup_rejects_corrupt_files_and_wrong_key_mode(service):
    _, _, config = service
    with pytest.raises(ValueError, match="must match"):
        create_app(dict(config, PAYMENT_MODE="live"))
    catalog = json.loads(open(config["DELIVERY_CATALOG"]).read())
    catalog["artifacts"]["windows"]["sha256"] = "0" * 64
    with open(config["DELIVERY_CATALOG"], "w") as stream:
        json.dump(catalog, stream)
    with pytest.raises(ValueError, match="checksum"):
        create_app(config)
