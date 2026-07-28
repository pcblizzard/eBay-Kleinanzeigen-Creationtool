"""Prüft den eBay-Angebotsfluss ohne Netzwerk.

Gegen die echte API kann hier nicht getestet werden - es liegen keine
Zugangsdaten vor. Geprüft wird deshalb, dass Adressen, Kopfzeilen und
Nutzdaten exakt der Dokumentation entsprechen; der Rest muss in der eBay-
Sandbox belegt werden.
"""

import json
import tempfile
import unittest
import urllib.error
import urllib.parse
from io import BytesIO
from pathlib import Path

from ebay_listing import (
    ENVIRONMENTS,
    EbayError,
    EbayListingClient,
    ListingDraft,
    Tokens,
    authorization_code,
    condition_code,
    consent_url,
    sku_for,
)


class Response(BytesIO):
    """Minimale Antwort mit Kontextverwaltung wie bei urlopen."""

    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
        return False


class RecordingClient(EbayListingClient):
    """Zeichnet Anfragen auf, statt sie zu senden."""

    def __init__(self, answers=None, **kwargs):
        super().__init__("client-id", "client-secret", **kwargs)
        self.requests = []
        self.answers = list(answers or [])
        self.tokens = Tokens(
            access_token="test-token", refresh_token="r", expires_at=2 ** 40
        )
        self._open = self._record

    def _record(self, request, timeout=30):
        self.requests.append(request)
        payload = self.answers.pop(0) if self.answers else b"{}"
        return Response(payload)


class ConsentTests(unittest.TestCase):
    def test_consent_url_carries_the_documented_parameters(self):
        url = consent_url("APP-ID", "Michael-RuName", "sandbox", state="xyz")
        parsed = urllib.parse.urlparse(url)
        values = urllib.parse.parse_qs(parsed.query)
        self.assertTrue(
            url.startswith(ENVIRONMENTS["sandbox"]["auth"]), url
        )
        self.assertEqual(values["client_id"], ["APP-ID"])
        # eBay erwartet als redirect_uri den RuName, nicht die Zieladresse.
        self.assertEqual(values["redirect_uri"], ["Michael-RuName"])
        self.assertEqual(values["response_type"], ["code"])
        self.assertEqual(values["state"], ["xyz"])
        self.assertIn(
            "https://api.ebay.com/oauth/api_scope/sell.inventory",
            values["scope"][0].split(" "),
        )

    def test_consent_url_needs_client_and_runame(self):
        with self.assertRaises(EbayError):
            consent_url("", "RuName")
        with self.assertRaises(EbayError):
            consent_url("APP-ID", "")

    def test_authorization_code_is_read_from_the_redirect(self):
        self.assertEqual(
            authorization_code(
                "https://example.test/accept?code=v%5E1.1%23abc&expires_in=299"
            ),
            "v^1.1#abc",
        )
        # Auch der blosse Code darf eingefuegt werden.
        self.assertEqual(authorization_code("abc123"), "abc123")
        self.assertEqual(authorization_code(""), "")

    def test_a_denied_consent_is_reported(self):
        with self.assertRaises(EbayError) as caught:
            authorization_code(
                "https://example.test/?error=access_denied"
                "&error_description=Der+Nutzer+hat+abgelehnt"
            )
        self.assertIn("abgelehnt", str(caught.exception))


class TokenTests(unittest.TestCase):
    def test_code_is_exchanged_with_basic_authentication(self):
        client = RecordingClient(
            answers=[json.dumps({
                "access_token": "A", "refresh_token": "R", "expires_in": 7200,
            }).encode()],
            environment="sandbox",
        )
        tokens = client.exchange_code("the-code", "Michael-RuName")
        request = client.requests[0]
        self.assertEqual(request.full_url, ENVIRONMENTS["sandbox"]["token"])
        self.assertTrue(request.headers["Authorization"].startswith("Basic "))
        body = urllib.parse.parse_qs(request.data.decode())
        self.assertEqual(body["grant_type"], ["authorization_code"])
        self.assertEqual(body["code"], ["the-code"])
        self.assertEqual(body["redirect_uri"], ["Michael-RuName"])
        self.assertEqual(tokens.access_token, "A")
        self.assertTrue(tokens.valid())

    def test_an_expired_token_is_refreshed_before_use(self):
        client = RecordingClient(
            answers=[
                json.dumps({"access_token": "neu", "expires_in": 7200}).encode(),
                b"{}",
            ]
        )
        client.tokens = Tokens(
            access_token="alt", refresh_token="R", expires_at=0
        )
        client.call("GET", "/sell/inventory/v1/location/x")
        self.assertEqual(len(client.requests), 2)
        refresh_body = urllib.parse.parse_qs(client.requests[0].data.decode())
        self.assertEqual(refresh_body["grant_type"], ["refresh_token"])
        self.assertEqual(
            client.requests[1].headers["Authorization"], "Bearer neu"
        )

    def test_refresh_without_consent_is_refused(self):
        client = RecordingClient()
        client.tokens = Tokens()
        with self.assertRaises(EbayError):
            client.refresh()


class ListingTests(unittest.TestCase):
    def draft(self, **overrides):
        values = dict(
            sku="FANTEC-QB-X2US3R",
            title="Fantec QB-X2US3R, schwarz",
            description="Beschreibung",
            condition="NEW",
            price="50.00",
            category_id="175669",
            image_urls=["https://i.ebayimg.com/1.jpg"],
            fulfillment_policy_id="F1",
            payment_policy_id="P1",
            return_policy_id="R1",
        )
        values.update(overrides)
        return ListingDraft(**values)

    def test_missing_fields_are_named_before_publishing(self):
        self.assertEqual(self.draft().missing_fields(), [])
        incomplete = self.draft(category_id="", image_urls=[])
        self.assertEqual(
            set(incomplete.missing_fields()), {"category_id", "images"}
        )

    def test_inventory_item_matches_the_documented_shape(self):
        client = RecordingClient()
        client.create_inventory_item(self.draft())
        request = client.requests[0]
        self.assertEqual(request.method, "PUT")
        self.assertTrue(request.full_url.endswith(
            "/sell/inventory/v1/inventory_item/FANTEC-QB-X2US3R"
        ))
        self.assertEqual(request.headers["X-ebay-c-marketplace-id"], "EBAY_DE")
        payload = json.loads(request.data)
        self.assertEqual(payload["condition"], "NEW")
        self.assertEqual(
            payload["availability"]["shipToLocationAvailability"]["quantity"], 1
        )
        self.assertEqual(
            payload["product"]["imageUrls"], ["https://i.ebayimg.com/1.jpg"]
        )

    def test_offer_carries_policies_price_and_location(self):
        client = RecordingClient(answers=[json.dumps({"offerId": "O1"}).encode()])
        self.assertEqual(client.create_offer(self.draft()), "O1")
        payload = json.loads(client.requests[0].data)
        self.assertEqual(payload["sku"], "FANTEC-QB-X2US3R")
        self.assertEqual(payload["marketplaceId"], "EBAY_DE")
        self.assertEqual(payload["format"], "FIXED_PRICE")
        self.assertEqual(payload["categoryId"], "175669")
        self.assertEqual(
            payload["pricingSummary"]["price"],
            {"currency": "EUR", "value": "50.00"},
        )
        self.assertEqual(payload["listingPolicies"], {
            "fulfillmentPolicyId": "F1",
            "paymentPolicyId": "P1",
            "returnPolicyId": "R1",
        })
        # Ohne Lagerort scheitert das Veroeffentlichen laut Dokumentation.
        self.assertTrue(payload["merchantLocationKey"])

    def test_publishing_returns_the_listing_id(self):
        client = RecordingClient(
            answers=[json.dumps({"listingId": "1234567890"}).encode()]
        )
        self.assertEqual(client.publish_offer("O1"), "1234567890")
        self.assertTrue(client.requests[0].full_url.endswith(
            "/sell/inventory/v1/offer/O1/publish"
        ))
        self.assertEqual(client.requests[0].method, "POST")

    def test_publishing_without_a_listing_id_is_an_error(self):
        client = RecordingClient(answers=[json.dumps({"warnings": []}).encode()])
        with self.assertRaises(EbayError):
            client.publish_offer("O1")

    def test_nothing_is_published_while_building_the_offer(self):
        """Bestandsartikel und Angebot duerfen nichts veroeffentlichen."""
        client = RecordingClient(answers=[b"{}", json.dumps({"offerId": "O"}).encode()])
        client.create_inventory_item(self.draft())
        client.create_offer(self.draft())
        self.assertFalse(
            any("publish" in request.full_url for request in client.requests)
        )


class PictureTests(unittest.TestCase):
    ANSWER = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<UploadSiteHostedPicturesResponse'
        ' xmlns="urn:ebay:apis:eBLBaseComponents">'
        '<Ack>Success</Ack><SiteHostedPictureDetails>'
        '<FullURL>https://i.ebayimg.com/00/s/foto.jpg</FullURL>'
        '</SiteHostedPictureDetails></UploadSiteHostedPicturesResponse>'
    ).encode()

    def test_picture_upload_uses_the_trading_api_with_an_oauth_token(self):
        client = RecordingClient(answers=[self.ANSWER])
        with tempfile.TemporaryDirectory() as folder:
            photo = Path(folder) / "01-hauptbild.jpg"
            photo.write_bytes(b"bilddaten")
            url = client.upload_picture(photo)
        self.assertEqual(url, "https://i.ebayimg.com/00/s/foto.jpg")
        request = client.requests[0]
        self.assertEqual(request.full_url, ENVIRONMENTS["production"]["trading"])
        headers = {key.lower(): value for key, value in request.headers.items()}
        self.assertEqual(
            headers["x-ebay-api-call-name"], "UploadSiteHostedPictures"
        )
        # OAuth-Token gehoert in den IAF-Header, nicht in Authorization.
        self.assertEqual(headers["x-ebay-api-iaf-token"], "test-token")
        self.assertNotIn("authorization", headers)
        self.assertIn("multipart/form-data", headers["content-type"])
        self.assertIn(b"bilddaten", request.data)
        self.assertIn(b"UploadSiteHostedPicturesRequest", request.data)

    def test_a_failed_upload_reports_ebays_reason(self):
        failure = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<UploadSiteHostedPicturesResponse'
            ' xmlns="urn:ebay:apis:eBLBaseComponents">'
            '<Ack>Failure</Ack><Errors>'
            '<LongMessage>Bild zu gross</LongMessage>'
            '</Errors></UploadSiteHostedPicturesResponse>'
        ).encode()
        client = RecordingClient(answers=[failure])
        with tempfile.TemporaryDirectory() as folder:
            photo = Path(folder) / "gross.jpg"
            photo.write_bytes(b"x")
            with self.assertRaises(EbayError) as caught:
                client.upload_picture(photo)
        self.assertIn("Bild zu gross", str(caught.exception))

    def test_a_missing_file_is_refused_before_any_request(self):
        client = RecordingClient()
        with self.assertRaises(EbayError):
            client.upload_picture("gibt-es-nicht.jpg")
        self.assertEqual(client.requests, [])


class ErrorTests(unittest.TestCase):
    def test_api_errors_are_readable_and_carry_no_credentials(self):
        body = json.dumps({"errors": [{
            "errorId": 25002,
            "longMessage": "Ein Artikel mit dieser SKU besteht bereits.",
            "parameters": [{"name": "sku", "value": "ABC"}],
        }]}).encode()
        message = EbayListingClient.describe_error(400, body)
        self.assertIn("25002", message)
        self.assertIn("SKU besteht bereits", message)
        self.assertIn("sku=ABC", message)

    def test_http_errors_become_ebay_errors(self):
        client = RecordingClient()

        def failing(request, timeout=30):
            raise urllib.error.HTTPError(
                request.full_url, 401, "Unauthorized", {},
                BytesIO(json.dumps({"errors": [
                    {"errorId": 1001, "longMessage": "Token abgelaufen"}
                ]}).encode()),
            )

        client._open = failing
        with self.assertRaises(EbayError) as caught:
            client.call("GET", "/sell/inventory/v1/location/x")
        self.assertIn("Token abgelaufen", str(caught.exception))


class HelperTests(unittest.TestCase):
    def test_condition_labels_map_to_ebay_values(self):
        self.assertEqual(condition_code("Neu"), "NEW")
        self.assertEqual(condition_code("Neuwertig"), "LIKE_NEW")
        self.assertEqual(condition_code("Sehr gut"), "USED_EXCELLENT")
        self.assertEqual(condition_code("Defekt"), "FOR_PARTS_OR_NOT_WORKING")
        self.assertEqual(condition_code("unbekannt"), "USED_GOOD")

    def test_sku_is_stable_and_uses_allowed_characters(self):
        sku = sku_for("Fantec QB-X2US3R, schwarz, USB-B 3.0", "4250199300182")
        self.assertEqual(sku, sku_for("Fantec QB-X2US3R, schwarz, USB-B 3.0",
                                      "4250199300182"))
        self.assertRegex(sku, r"^[A-Z0-9-]+$")
        self.assertLessEqual(len(sku), 40)


if __name__ == "__main__":
    unittest.main()
