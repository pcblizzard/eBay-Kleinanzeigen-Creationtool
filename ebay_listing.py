"""Offizieller Weg, ein eBay-Angebot aus dem Werkzeug heraus einzustellen.

Anders als bei Kleinanzeigen gibt es hier dokumentierte Schnittstellen, die
das Einstellen ausdrücklich vorsehen. Der Ablauf folgt der Sell-Inventory-API:

    1. Benutzer-Einwilligung einholen (OAuth Authorization Code Grant)
    2. Eigene Fotos zu den eBay Picture Services laden
    3. Lagerort anlegen, sofern noch keiner besteht
    4. Bestandsartikel schreiben (``createOrReplaceInventoryItem``)
    5. Angebot anlegen (``createOffer``)
    6. Angebot veröffentlichen (``publishOffer``)

Veröffentlicht wird ausschließlich auf ausdrückliche Anforderung; die Schritte
1 bis 5 verändern nichts an aktiven Angeboten.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

ENVIRONMENTS = {
    "production": {
        "auth": "https://auth.ebay.com/oauth2/authorize",
        "token": "https://api.ebay.com/identity/v1/oauth2/token",
        "api": "https://api.ebay.com",
        "trading": "https://api.ebay.com/ws/api.dll",
    },
    "sandbox": {
        "auth": "https://auth.sandbox.ebay.com/oauth2/authorize",
        "token": "https://api.sandbox.ebay.com/identity/v1/oauth2/token",
        "api": "https://api.sandbox.ebay.com",
        "trading": "https://api.sandbox.ebay.com/ws/api.dll",
    },
}

# Bestand schreiben, Richtlinien lesen. Bewusst keine weiteren Berechtigungen.
SCOPES = (
    "https://api.ebay.com/oauth/api_scope/sell.inventory",
    "https://api.ebay.com/oauth/api_scope/sell.account",
)
MARKETPLACE = "EBAY_DE"
TRADING_SITE_ID = "77"          # Deutschland
TRADING_COMPATIBILITY = "1193"
DEFAULT_LOCATION_KEY = "creationtool-standort"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024

# Zustandswerte des Inserat-Assistenten auf die eBay-Aufzaehlung abgebildet.
CONDITION_MAP = {
    "neu": "NEW",
    "new": "NEW",
    "neu (sonstige)": "NEW_OTHER",
    "neuwertig": "LIKE_NEW",
    "like new": "LIKE_NEW",
    "sehr gut": "USED_EXCELLENT",
    "gut": "USED_GOOD",
    "akzeptabel": "USED_ACCEPTABLE",
    "gebraucht": "USED_GOOD",
    "used": "USED_GOOD",
    "defekt": "FOR_PARTS_OR_NOT_WORKING",
    "ersatzteil": "FOR_PARTS_OR_NOT_WORKING",
}


class EbayError(RuntimeError):
    """Fehler der eBay-Schnittstelle mit lesbarer Meldung."""


@dataclass
class Tokens:
    """Zugangsdaten einer Benutzer-Einwilligung."""

    access_token: str = ""
    refresh_token: str = ""
    expires_at: float = 0.0

    def valid(self, safety_seconds: int = 120) -> bool:
        return bool(self.access_token) and (
            self.expires_at - safety_seconds > time.time()
        )


@dataclass
class ListingDraft:
    """Alles, was ein Angebot braucht."""

    sku: str = ""
    title: str = ""
    description: str = ""
    condition: str = "USED_GOOD"
    price: str = "0.00"
    quantity: int = 1
    category_id: str = ""
    image_urls: list = field(default_factory=list)
    aspects: dict = field(default_factory=dict)
    merchant_location_key: str = DEFAULT_LOCATION_KEY
    fulfillment_policy_id: str = ""
    payment_policy_id: str = ""
    return_policy_id: str = ""

    def missing_fields(self) -> list:
        """Nennt fehlende Pflichtangaben vor dem Veröffentlichen."""
        required = {
            "sku": self.sku,
            "title": self.title,
            "category_id": self.category_id,
            "price": self.price,
            "images": self.image_urls,
            "fulfillment_policy_id": self.fulfillment_policy_id,
            "payment_policy_id": self.payment_policy_id,
            "return_policy_id": self.return_policy_id,
            "merchant_location_key": self.merchant_location_key,
        }
        return [name for name, value in required.items() if not value]


def condition_code(label: str) -> str:
    """Bildet einen Zustandstext auf den eBay-Wert ab."""
    normalized = re.sub(r"\s+", " ", str(label or "")).strip().casefold()
    if normalized in CONDITION_MAP:
        return CONDITION_MAP[normalized]
    for key, value in CONDITION_MAP.items():
        if normalized.startswith(key):
            return value
    return "USED_GOOD"


def sku_for(name: str, identifier: str = "") -> str:
    """Erzeugt eine stabile, zulaessige SKU aus dem Produktnamen."""
    base = re.sub(r"[^A-Za-z0-9]+", "-", f"{name} {identifier}").strip("-")
    return (base[:40] or "artikel").upper()


def consent_url(client_id: str, ru_name: str, environment: str = "production",
                state: str = "", scopes=SCOPES) -> str:
    """Baut die Seite, auf der der Nutzer die Berechtigung erteilt."""
    if not client_id or not ru_name:
        raise EbayError("Client-ID und RuName werden benoetigt.")
    query = urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": ru_name,
        "response_type": "code",
        "scope": " ".join(scopes),
        "state": state or secrets.token_urlsafe(16),
        "prompt": "login",
    })
    return f"{ENVIRONMENTS[environment]['auth']}?{query}"


def authorization_code(redirect_response: str, expected_state: str = "") -> str:
    """Liest den Code aus der Adresse, auf die eBay weitergeleitet hat.

    Der Nutzer kopiert die vollstaendige Adresse aus der Adresszeile; der
    Code ist darin URL-kodiert enthalten.

    Ist ``expected_state`` gesetzt, muss der zurueckgegebene Statuswert damit
    uebereinstimmen. Andernfalls koennte eine untergeschobene Adresse das
    Werkzeug an ein fremdes eBay-Konto binden.
    """
    text = str(redirect_response or "").strip()
    if not text:
        return ""
    query = urllib.parse.urlparse(text).query or text
    values = urllib.parse.parse_qs(query)
    if "error" in values:
        raise EbayError(
            values.get("error_description", values["error"])[0]
        )
    code = values.get("code", [""])[0]
    if expected_state and code:
        returned = values.get("state", [""])[0]
        if not secrets.compare_digest(returned, expected_state):
            raise EbayError(
                "Der Statuswert der Antwort passt nicht zur Anfrage. "
                "Die Adresse stammt moeglicherweise nicht aus diesem Vorgang "
                "- bitte den Zugriff erneut erteilen."
            )
    # Wer nur den Code einfuegt, soll ebenfalls weiterkommen.
    return code or (text if "&" not in text and "?" not in text else "")


class EbayListingClient:
    """Spricht die Sell-APIs; die Zugangsdaten kommen von aussen."""

    def __init__(self, client_id, client_secret, environment="production",
                 marketplace=MARKETPLACE, opener=None):
        self.client_id = client_id
        self.client_secret = client_secret
        self.environment = (
            environment if environment in ENVIRONMENTS else "production"
        )
        self.marketplace = marketplace
        self.tokens = Tokens()
        # Ersetzbar, damit Tests ohne Netzwerk auskommen.
        self._open = opener or self._urlopen

    # ---------------------------------------------------------------- Netz

    @staticmethod
    def _urlopen(request, timeout=30):
        return urllib.request.urlopen(request, timeout=timeout)

    def endpoint(self, name):
        return ENVIRONMENTS[self.environment][name]

    def _read(self, request):
        try:
            with self._open(request) as response:
                # Begrenzt gelesen: eine unerwartet riesige Antwort soll
                # weder den Speicher fuellen noch den XML-Parser beschaeftigen.
                body = response.read(MAX_RESPONSE_BYTES + 1)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise EbayError("Antwort von eBay ist zu gross.")
                return response.status, body
        except urllib.error.HTTPError as error:
            body = error.read(MAX_RESPONSE_BYTES)
            raise EbayError(self.describe_error(error.code, body)) from error

    @staticmethod
    def describe_error(status, body):
        """Macht aus einer API-Antwort eine lesbare Meldung ohne Zugangsdaten."""
        text = body.decode("utf-8", errors="replace") if body else ""
        try:
            payload = json.loads(text)
        except (TypeError, ValueError):
            return f"HTTP {status}: {text[:300]}"
        messages = []
        for entry in payload.get("errors", []) or []:
            message = entry.get("longMessage") or entry.get("message", "")
            parameters = ", ".join(
                f"{item.get('name')}={item.get('value')}"
                for item in entry.get("parameters", []) or []
            )
            messages.append(
                f"{entry.get('errorId', '')} {message} {parameters}".strip()
            )
        return f"HTTP {status}: " + ("; ".join(messages) or text[:300])

    # -------------------------------------------------------------- OAuth

    def _token_request(self, data):
        basic = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode("utf-8")
        ).decode("ascii")
        request = urllib.request.Request(
            self.endpoint("token"),
            data=urllib.parse.urlencode(data).encode("utf-8"),
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": f"Basic {basic}",
            },
            method="POST",
        )
        _status, body = self._read(request)
        payload = json.loads(body.decode("utf-8"))
        self.tokens = Tokens(
            access_token=payload.get("access_token", ""),
            refresh_token=payload.get(
                "refresh_token", self.tokens.refresh_token
            ),
            expires_at=time.time() + int(payload.get("expires_in", 0)),
        )
        return self.tokens

    def exchange_code(self, code, ru_name):
        """Tauscht den Einwilligungscode gegen Zugriffs- und Erneuerungstoken."""
        if not code:
            raise EbayError("Es wurde kein Autorisierungscode uebergeben.")
        return self._token_request({
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": ru_name,
        })

    def refresh(self, refresh_token=""):
        """Holt einen frischen Zugriffstoken zum gespeicherten Erneuerungstoken."""
        token = refresh_token or self.tokens.refresh_token
        if not token:
            raise EbayError("Keine gespeicherte Einwilligung vorhanden.")
        self.tokens.refresh_token = token
        return self._token_request({
            "grant_type": "refresh_token",
            "refresh_token": token,
            "scope": " ".join(SCOPES),
        })

    def access_token(self):
        if not self.tokens.valid():
            self.refresh()
        return self.tokens.access_token

    # ---------------------------------------------------------- REST-Aufruf

    def call(self, method, path, payload=None, headers=None):
        """Ruft eine Sell-API auf und liefert die geparste Antwort."""
        request_headers = {
            "Authorization": f"Bearer {self.access_token()}",
            "Accept": "application/json",
            "Content-Language": "de-DE",
            "X-EBAY-C-MARKETPLACE-ID": self.marketplace,
        }
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        request_headers.update(headers or {})
        request = urllib.request.Request(
            f"{self.endpoint('api')}{path}",
            data=data,
            headers=request_headers,
            method=method,
        )
        status, body = self._read(request)
        if not body:
            return {}
        try:
            return json.loads(body.decode("utf-8"))
        except ValueError:
            return {"status": status}

    # ------------------------------------------------------------- Bilder

    def upload_picture(self, image_path):
        """Laedt ein eigenes Foto zu den eBay Picture Services.

        Die Sell-Inventory-API erwartet oeffentlich erreichbare Bildadressen.
        Lokale Dateien muessen deshalb zuerst hierher; die Trading-API ist der
        dafuer vorgesehene Weg und nimmt den OAuth-Token als IAF-Header.
        """
        path = Path(image_path)
        if not path.is_file():
            raise EbayError(f"Bilddatei nicht gefunden: {path}")
        xml_request = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<UploadSiteHostedPicturesRequest'
            ' xmlns="urn:ebay:apis:eBLBaseComponents">'
            '<PictureName>' + self.escape(path.stem) + '</PictureName>'
            '</UploadSiteHostedPicturesRequest>'
        )
        boundary = f"----creationtool{uuid.uuid4().hex}"
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        body = b"".join([
            f"--{boundary}\r\n".encode("utf-8"),
            b'Content-Disposition: form-data; name="XML Payload"\r\n',
            b"Content-Type: text/xml;charset=utf-8\r\n\r\n",
            xml_request.encode("utf-8"),
            f"\r\n--{boundary}\r\n".encode("utf-8"),
            f'Content-Disposition: form-data; name="image"; '
            f'filename="{path.name}"\r\n'.encode("utf-8"),
            f"Content-Type: {mime}\r\n\r\n".encode("utf-8"),
            path.read_bytes(),
            f"\r\n--{boundary}--\r\n".encode("utf-8"),
        ])
        request = urllib.request.Request(
            self.endpoint("trading"),
            data=body,
            headers={
                "X-EBAY-API-CALL-NAME": "UploadSiteHostedPictures",
                "X-EBAY-API-SITEID": TRADING_SITE_ID,
                "X-EBAY-API-COMPATIBILITY-LEVEL": TRADING_COMPATIBILITY,
                "X-EBAY-API-IAF-TOKEN": self.access_token(),
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            method="POST",
        )
        _status, response = self._read(request)
        return self.picture_url(response)

    @staticmethod
    def escape(value):
        return (
            str(value).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    @staticmethod
    def picture_url(xml_body):
        """Liest die Bildadresse aus der Trading-Antwort."""
        try:
            root = ET.fromstring(xml_body)
        except ET.ParseError as error:
            raise EbayError("Unlesbare Antwort beim Bild-Upload.") from error
        namespace = "{urn:ebay:apis:eBLBaseComponents}"
        full_url = root.find(f".//{namespace}FullURL")
        if full_url is not None and (full_url.text or "").strip():
            return full_url.text.strip()
        errors = [
            (node.findtext(f"{namespace}LongMessage") or "").strip()
            for node in root.findall(f".//{namespace}Errors")
        ]
        raise EbayError(
            "Bild-Upload fehlgeschlagen: " + ("; ".join(filter(None, errors))
                                              or "unbekannter Grund")
        )

    # ------------------------------------------------------- Voraussetzungen

    def policies(self):
        """Liest die Versand-, Zahlungs- und Ruecknahmerichtlinien."""
        result = {}
        for kind, path in (
            ("fulfillment", "fulfillment_policy"),
            ("payment", "payment_policy"),
            ("return", "return_policy"),
        ):
            payload = self.call(
                "GET",
                f"/sell/account/v1/{path}?marketplace_id={self.marketplace}",
            )
            result[kind] = [
                {
                    "id": entry.get(f"{kind}PolicyId", ""),
                    "name": entry.get("name", ""),
                }
                for entry in payload.get(f"{kind}Policies", []) or []
            ]
        return result

    def ensure_location(self, key, address):
        """Legt einen Lagerort an, falls er noch nicht besteht."""
        try:
            self.call("GET", f"/sell/inventory/v1/location/{key}")
            return key
        except EbayError:
            pass
        self.call(
            "POST",
            f"/sell/inventory/v1/location/{key}",
            {
                "location": {"address": address},
                "locationTypes": ["WAREHOUSE"],
                "merchantLocationStatus": "ENABLED",
                "name": "Privatverkauf",
            },
        )
        return key

    # -------------------------------------------------------------- Angebot

    def create_inventory_item(self, draft: ListingDraft):
        """Schreibt den Bestandsartikel; veroeffentlicht noch nichts."""
        self.call(
            "PUT",
            f"/sell/inventory/v1/inventory_item/{urllib.parse.quote(draft.sku)}",
            {
                "availability": {
                    "shipToLocationAvailability": {"quantity": draft.quantity}
                },
                "condition": draft.condition,
                "product": {
                    "title": draft.title[:80],
                    "description": draft.description,
                    "imageUrls": list(draft.image_urls),
                    "aspects": {
                        key: [value] if isinstance(value, str) else list(value)
                        for key, value in (draft.aspects or {}).items()
                    },
                },
            },
        )
        return draft.sku

    def create_offer(self, draft: ListingDraft):
        """Legt das Angebot an und liefert dessen Kennung."""
        payload = {
            "sku": draft.sku,
            "marketplaceId": self.marketplace,
            "format": "FIXED_PRICE",
            "availableQuantity": draft.quantity,
            "categoryId": draft.category_id,
            "listingDescription": draft.description,
            "listingPolicies": {
                "fulfillmentPolicyId": draft.fulfillment_policy_id,
                "paymentPolicyId": draft.payment_policy_id,
                "returnPolicyId": draft.return_policy_id,
            },
            "pricingSummary": {
                "price": {"currency": "EUR", "value": str(draft.price)}
            },
            "merchantLocationKey": draft.merchant_location_key,
        }
        answer = self.call("POST", "/sell/inventory/v1/offer", payload)
        offer_id = answer.get("offerId", "")
        if not offer_id:
            raise EbayError("eBay hat keine Angebotskennung zurueckgegeben.")
        return offer_id

    def publish_offer(self, offer_id):
        """Veroeffentlicht das Angebot - erst hier entsteht ein Inserat."""
        answer = self.call(
            "POST", f"/sell/inventory/v1/offer/{offer_id}/publish"
        )
        listing_id = answer.get("listingId", "")
        if not listing_id:
            raise EbayError(
                "Das Angebot wurde nicht veroeffentlicht: "
                + self.describe_error(200, json.dumps(answer).encode("utf-8"))
            )
        return listing_id
