"""Private installer delivery. Run separately from the public Pages storefront."""

import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from flask import Flask, abort, jsonify, render_template, request, send_file
from itsdangerous import BadData, URLSafeTimedSerializer

PLATFORMS = {
    "windows": "Windows",
    "mac-arm": "Mac — Apple Silicon",
    "mac-intel": "Mac — Intel",
    "linux": "Linux",
}
SESSION_ID = re.compile(r"cs_(?:test|live)_[A-Za-z0-9]{8,200}\Z")


class PaymentUnavailable(Exception):
    pass


def stripe_session(key, session_id):
    query = urlencode(
        [
            ("expand[]", "line_items"),
            ("expand[]", "payment_intent.latest_charge"),
        ]
    )
    req = Request(
        f"https://api.stripe.com/v1/checkout/sessions/{session_id}?{query}",
        headers={"Authorization": f"Bearer {key}", "Stripe-Version": "2025-02-24.acacia"},
    )
    try:
        with urlopen(req, timeout=15) as response:
            return json.load(response)
    except HTTPError as exc:
        if exc.code == 404:
            return {}
        raise PaymentUnavailable from None
    except (URLError, TimeoutError, ValueError):
        raise PaymentUnavailable from None


def create_app(settings=None, retrieve=None):
    app = Flask(__name__, static_folder=None)
    app.config.update(MAX_CONTENT_LENGTH=262144)
    config = dict(os.environ if settings is None else settings)
    required = (
        "STRIPE_SECRET_KEY",
        "STRIPE_WEBHOOK_SECRET",
        "STRIPE_PAYMENT_LINK_ID",
        "STRIPE_PRICE_ID",
        "DOWNLOAD_SIGNING_KEY",
        "DELIVERY_CATALOG",
        "DELIVERY_DB",
    )
    if any(not config.get(key) for key in required):
        raise ValueError("Configure the delivery service environment before starting it")
    mode = config.get("PAYMENT_MODE", "test")
    if mode not in ("test", "live") or not config["STRIPE_SECRET_KEY"].startswith(
        (f"sk_{mode}_", f"rk_{mode}_")
    ):
        raise ValueError("Stripe key and payment mode must match")
    if len(config["DOWNLOAD_SIGNING_KEY"]) < 32:
        raise ValueError("DOWNLOAD_SIGNING_KEY needs at least 32 random characters")
    catalog_file = Path(config["DELIVERY_CATALOG"]).resolve()
    catalog = json.loads(catalog_file.read_text())
    release = catalog["release"]
    artifacts = catalog["artifacts"]
    if set(artifacts) != set(PLATFORMS) or not isinstance(release, str) or not release:
        raise ValueError("Catalog must contain one release and all four platforms")

    def validate_file(item):
        path = (catalog_file.parent / item["file"]).resolve()
        if not path.is_relative_to(catalog_file.parent) or not path.is_file():
            raise ValueError("Private artifact is missing or escapes storage")
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != item["sha256"]:
            raise ValueError("Artifact checksum mismatch")
        item["path"] = path
        return path

    for platform, artifact in artifacts.items():
        path = validate_file(artifact)
        suffix = {"windows": ".exe", "mac-arm": ".dmg", "mac-intel": ".dmg", "linux": ".tar.gz"}[
            platform
        ]
        if not path.name.endswith(suffix):
            raise ValueError(f"Incorrect installer type: {platform}")
        validate_file(artifact["materials"])
    db = Path(config["DELIVERY_DB"])
    db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS purchases (
            session_id TEXT PRIMARY KEY, release TEXT NOT NULL, platform TEXT,
            fulfilled_at INTEGER NOT NULL)""")
    signer = URLSafeTimedSerializer(config["DOWNLOAD_SIGNING_KEY"], salt="installer-v1")
    fetch = retrieve or (lambda sid: stripe_session(config["STRIPE_SECRET_KEY"], sid))

    def fulfill(sid):
        if not isinstance(sid, str) or not SESSION_ID.fullmatch(sid):
            abort(400, "This purchase link is incomplete. Open the link from Stripe again.")
        session = fetch(sid)
        items = session.get("line_items") or {}
        lines = items.get("data", [])
        if (
            session.get("id") != sid
            or session.get("livemode") is not (mode == "live")
            or session.get("mode") != "payment"
            or session.get("payment_link") != config["STRIPE_PAYMENT_LINK_ID"]
            or session.get("currency") != "usd"
            or session.get("amount_subtotal") != 100
            or not isinstance(session.get("amount_total"), int)
            or session["amount_total"] < 100
            or (session.get("total_details") or {}).get("amount_discount", 0) != 0
            or items.get("has_more") is not False
            or len(lines) != 1
            or lines[0].get("quantity") != 1
            or (lines[0].get("price") or {}).get("id") != config["STRIPE_PRICE_ID"]
        ):
            abort(403, "This payment does not match the $1 Anharmonic download.")
        if session.get("payment_status") != "paid" or session.get("status") != "complete":
            abort(409, "Stripe has not confirmed payment yet. Please try again shortly.")
        intent = session.get("payment_intent") or {}
        charge = (intent.get("latest_charge") or {}) if isinstance(intent, dict) else {}
        if (
            not isinstance(charge, dict)
            or charge.get("paid") is not True
            or charge.get("refunded") is not False
            or charge.get("amount_refunded") != 0
            or charge.get("disputed") is not False
        ):
            abort(403, "Download access is unavailable for this payment. Contact support.")
        reference = session.get("client_reference_id") or ""
        platform = reference.removeprefix("as_v1_") if isinstance(reference, str) else ""
        platform = platform if platform in PLATFORMS and reference == f"as_v1_{platform}" else None
        with sqlite3.connect(db) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO purchases VALUES (?, ?, ?, ?)",
                (sid, release, platform, int(time.time())),
            )
            saved = conn.execute(
                "SELECT release FROM purchases WHERE session_id = ?", (sid,)
            ).fetchone()
        # This service delivers this release's major-version updates only.
        if saved[0].split(".")[0] != release.split(".")[0]:
            abort(
                403,
                "This purchase covers an earlier major version. Contact support for its download.",
            )
        return platform

    @app.after_request
    def private_headers(response):
        response.headers.update(
            {
                "Cache-Control": "private, no-store",
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
                "X-Robots-Tag": "noindex, nofollow",
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; "
                "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
            }
        )
        return response

    @app.errorhandler(PaymentUnavailable)
    def stripe_unavailable(_error):
        return jsonify(
            error="Stripe could not be reached. Please try again; do not pay again."
        ), 503

    for code in (400, 403, 404, 409, 413):
        app.register_error_handler(
            code, lambda error: (jsonify(error=error.description), error.code)
        )

    @app.get("/health")
    def health():
        return jsonify(ready=True, mode=mode, release=release)

    @app.get("/download-success")
    def success():
        return render_template("success.html")

    @app.get("/assets/<name>")
    def assets(name):
        if name not in ("success.js", "success.css"):
            abort(404)
        return send_file(Path(__file__).parent / "assets" / name)

    @app.post("/api/claim")
    def claim():
        if not request.is_json:
            abort(400, "Expected a purchase reference.")
        body = request.get_json(silent=True)
        sid = body.get("session_id") if isinstance(body, dict) else None
        platform = fulfill(sid)
        ticket = signer.dumps({"session_id": sid, "release": release})
        return jsonify(
            mode=mode,
            release=release,
            platform=platform,
            downloads=[
                {
                    "platform": key,
                    "label": PLATFORMS[key],
                    "filename": item["path"].name,
                    "sha256": item["sha256"],
                    "url": f"/files/{ticket}/{key}",
                    "materials_url": f"/files/{ticket}/{key}?part=materials",
                }
                for key, item in artifacts.items()
            ],
        )

    @app.get("/files/<ticket>/<platform>")
    def download(ticket, platform):
        try:
            payload = signer.loads(ticket, max_age=900)
        except BadData:
            abort(
                403, "This download link expired. Return to the download page and press Try again."
            )
        if (
            not isinstance(payload, dict)
            or payload.get("release") != release
            or platform not in artifacts
        ):
            abort(403, "This download link is not valid for this release.")
        # Re-check Stripe here too, so refunds/disputes invalidate issued links.
        fulfill(payload.get("session_id"))
        part = request.args.get("part", "installer")
        if part not in ("installer", "materials"):
            abort(404)
        item = artifacts[platform] if part == "installer" else artifacts[platform]["materials"]
        return send_file(item["path"], as_attachment=True, conditional=True)

    @app.post("/webhook")
    def webhook():
        raw = request.get_data()
        fields = request.headers.get("Stripe-Signature", "").split(",")
        timestamps = [value[2:] for value in fields if value.startswith("t=")]
        signatures = [value[3:] for value in fields if re.fullmatch(r"v1=[a-f0-9]{64}", value)]
        try:
            timestamp = int(timestamps[0]) if len(timestamps) == 1 else 0
        except ValueError:
            timestamp = 0
        expected = hmac.new(
            config["STRIPE_WEBHOOK_SECRET"].encode(),
            str(timestamp).encode() + b"." + raw,
            hashlib.sha256,
        ).hexdigest()
        if abs(time.time() - timestamp) > 300 or not any(
            hmac.compare_digest(expected, signature) for signature in signatures
        ):
            abort(400, "Invalid Stripe signature.")
        event = request.get_json(silent=True)
        if not isinstance(event, dict):
            abort(400)
        if event.get("type") in (
            "checkout.session.completed",
            "checkout.session.async_payment_succeeded",
        ):
            obj = (event.get("data") or {}).get("object") or {}
            if (
                obj.get("payment_link") == config["STRIPE_PAYMENT_LINK_ID"]
                and obj.get("payment_status") == "paid"
            ):
                fulfill(obj.get("id"))
        return jsonify(received=True)

    return app
