import inspect
import re
import json
import tempfile
import tkinter as tk
import unittest
import unicodedata
from types import SimpleNamespace
from unittest.mock import Mock, patch
from pathlib import Path

from PIL import Image
from PIL.TiffImagePlugin import IFDRational

from product_generator_gui import (
    SUPERSEDED_CLAUSES,
    BUYBACK_SERVICES,
    TabbedProductGeneratorGUI,
    OWN_IMAGE_MAX_EDGE,
    ProductGenerator,
    ProductGeneratorGUI,
    SECRET_PLACEHOLDER,
    WARRANTY_CLAUSE,
    default_products_file,
    prepare_own_image,
)
from listing_store import (
    ListingStore,
    PLATFORM_PROFILES,
    canonical_fact_key,
    safe_filename,
)


def display_available():
    """Prüft, ob Tk ein Fenster öffnen kann.

    Auf Buildservern ohne X-Server ist das nicht der Fall; die betroffenen
    Tests werden dann übersprungen statt zu scheitern.
    """
    try:
        root = tk.Tk()
    except Exception:
        return False
    root.destroy()
    return True


DISPLAY_AVAILABLE = display_available()


class ProductGeneratorTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.generator = ProductGenerator(
            products_file=default_products_file(),
            output_dir=self.temp_dir.name,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    def platform_description(body, legal_clause=WARRANTY_CLAUSE):
        """Ruft den echten Zusammenbau der Oberfläche ohne Tk-Fenster auf."""
        stub = SimpleNamespace(legal_clause=legal_clause)
        return ProductGeneratorGUI.full_platform_description(stub, body)

    def test_required_clause_is_always_german_and_at_the_end(self):
        listing = self.platform_description("Reviewed description")
        self.assertIn("Reviewed description", listing)
        self.assertTrue(listing.rstrip().endswith(WARRANTY_CLAUSE))

    def test_custom_legal_clause_replaces_default_at_the_end(self):
        custom = "Individuell bearbeiteter Privatverkaufs-Hinweis."
        listing = self.platform_description("Beschreibung", legal_clause=custom)
        self.assertTrue(listing.rstrip().endswith(custom))
        self.assertNotIn(WARRANTY_CLAUSE, listing)

    @staticmethod
    def assistant(language='de', price_type='VB'):
        gui = ProductGeneratorGUI.__new__(ProductGeneratorGUI)
        gui.language = language
        gui.price_type_var = SimpleNamespace(get=lambda: price_type)
        return gui

    def draft_body(self, language='de'):
        return ProductGeneratorGUI.platform_body_from_draft(
            ProductGenerator.build_sales_draft(
                "Fantec QB-X2US3R", "Marke: Fantec", language
            )
        )

    def test_assistant_details_replace_the_generated_placeholders(self):
        gui = self.assistant()
        merged = gui.merge_assistant_details(
            self.draft_body(), "Neu",
            ProductGeneratorGUI.scope_items("Gehäuse, Originalverpackung"),
            gui.assistant_price_text(50.0),
        )
        # Der Zustand steht konkret da, die Auswahlsaetze sind weg.
        self.assertIn("### Zustand\n\nNeu", merged)
        self.assertNotIn("**[neuem / neuwertigem", merged)
        self.assertNotIn("Normale Gebrauchsspuren", merged)
        self.assertNotIn("Nicht Zutreffendes", merged)
        # Der Lieferumfang steht genau einmal, als Stichpunkte.
        self.assertEqual(merged.count("### Lieferumfang"), 1)
        self.assertIn("* Gehäuse\n* Originalverpackung", merged)
        self.assertNotIn("[Ladekabel / Netzteil]", merged)
        # Kein zusaetzlicher Sammelabschnitt mehr.
        self.assertNotIn("Angaben zum angebotenen Artikel", merged)
        self.assertTrue(merged.rstrip().endswith("Bei Fragen einfach melden."))

    def test_applying_the_assistant_twice_changes_nothing(self):
        gui = self.assistant()
        items = ProductGeneratorGUI.scope_items("Gehäuse, Originalverpackung")
        price = gui.assistant_price_text(50.0)
        once = gui.merge_assistant_details(
            self.draft_body(), "Neu", items, price
        )
        twice = gui.merge_assistant_details(once, "Neu", items, price)
        self.assertEqual(once, twice)

    def test_placeholders_stay_while_no_condition_is_chosen(self):
        merged = self.assistant().merge_assistant_details(
            self.draft_body(), "", [], ""
        )
        self.assertIn("**[neuem / neuwertigem", merged)
        self.assertIn("Nicht Zutreffendes", merged)

    def test_price_carries_the_kleinanzeigen_price_type(self):
        self.assertEqual(
            self.assistant(price_type='VB').assistant_price_text(50.0),
            "50,00 € VB",
        )
        self.assertEqual(
            self.assistant(price_type='Festpreis').assistant_price_text(1234.5),
            "1.234,50 € Festpreis",
        )
        # "Zu verschenken" ersetzt den Betrag, statt ihn zu ergaenzen.
        self.assertEqual(
            self.assistant(price_type='Zu verschenken')
            .assistant_price_text(50.0),
            "Zu verschenken",
        )
        self.assertEqual(
            self.assistant('en', 'Negotiable').assistant_price_text(1234.5),
            "1,234.50 € Negotiable",
        )

    def photo_with_location(self, path, size=(3000, 2000), colour=(200, 30, 30)):
        """Erzeugt ein Foto mit GPS-Daten und gedrehter Orientierung."""
        image = Image.new("RGB", size, colour)
        exif = image.getexif()
        exif[274] = 6                       # Orientierung: 90 Grad
        exif[271] = "TestPhone"             # Kamerahersteller
        gps = exif.get_ifd(0x8825)
        gps[1] = 'N'
        gps[2] = tuple(IFDRational(value) for value in (52, 31, 12))
        image.save(path, exif=exif)
        return path

    def test_prepared_photos_lose_their_location_data(self):
        with tempfile.TemporaryDirectory() as folder:
            source = self.photo_with_location(Path(folder) / "IMG_0001.jpg")
            target = Path(folder) / "01-hauptbild.jpg"
            self.assertTrue(prepare_own_image(source, target))
            with Image.open(target) as prepared:
                exif = prepared.getexif()
                # Standortdaten wuerden sonst die Wohnadresse verraten.
                self.assertFalse(exif.get_ifd(0x8825))
                self.assertIsNone(exif.get(271))
                self.assertIsNone(exif.get(274))
                # Orientierung angewandt, Kantenlaenge begrenzt.
                self.assertEqual(max(prepared.size), OWN_IMAGE_MAX_EDGE)
                self.assertGreater(prepared.height, prepared.width)

    def test_own_photos_keep_their_order_in_the_export(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = ListingStore(root / "listings.db")
            try:
                product_id = store.upsert_product("Testprodukt", "1")
                store.save_draft(product_id, "kleinanzeigen", "Titel", "Text")
                paths = [
                    self.photo_with_location(
                        root / f"IMG_{n}.jpg", colour=(40 * n, 30, 200)
                    )
                    for n in range(1, 4)
                ]
                ids = [store.add_image(product_id, path) for path in paths]
                # Dasselbe Foto zweimal ergibt keinen zweiten Eintrag.
                self.assertEqual(store.add_image(product_id, paths[0]), ids[0])
                self.assertEqual(len(store.images(product_id, True)), 3)
                # Das dritte Foto wird zum Hauptbild.
                store.reorder_images([ids[2], ids[0], ids[1]])
                ordered = [
                    Path(image['path']).name
                    for image in store.images(product_id, own_only=True)
                ]
                self.assertEqual(
                    ordered, ["IMG_3.jpg", "IMG_1.jpg", "IMG_2.jpg"]
                )
                exported = store.export_package(
                    product_id, root / "export",
                    images=[
                        image['path']
                        for image in store.images(product_id, own_only=True)
                    ],
                    prepare=prepare_own_image,
                )
                names = sorted(
                    item.name for item in exported.iterdir() if item.is_file()
                )
                self.assertIn("01-hauptbild.jpg", names)
                self.assertIn("02-produktbild.jpg", names)
                self.assertIn("03-produktbild.jpg", names)
                with Image.open(exported / "01-hauptbild.jpg") as main:
                    self.assertFalse(main.getexif().get_ifd(0x8825))
                store.remove_image(ids[0])
                self.assertEqual(len(store.images(product_id, True)), 2)
                # Die Originaldatei bleibt unangetastet.
                self.assertTrue(paths[0].is_file())
            finally:
                store.close()

    def test_deleting_credentials_covers_every_stored_secret(self):
        """Jedes gespeicherte Geheimnis muss auch loeschbar sein.

        Der eBay-Erneuerungstoken kam spaeter dazu und fehlte zunaechst in der
        Loeschung - der Zugriff auf das Konto waere sonst geblieben.
        """
        source = inspect.getsource(
            TabbedProductGeneratorGUI._delete_marketplace_credentials
        )
        gui_source = Path("product_generator_gui.py").read_text(
            encoding="utf-8"
        )
        stored = set(re.findall(r"set_secret\(\s*'([a-z_]+)'", gui_source))
        stored |= set(re.findall(r"get_secret\('([a-z_]+)'\)", gui_source))
        # Namen, die nur gelesen werden, weil sie aus Umgebungsvariablen
        # stammen koennen, zaehlen ebenfalls.
        for name in sorted(stored):
            self.assertIn(
                name, source,
                f"{name} wird gespeichert, aber nicht geloescht",
            )

    def test_menubar_entries_start_at_index_zero(self):
        """Ein Tearoff-Eintrag wuerde alle Menue-Indizes verschieben.

        Unter Windows legt Tk ihn ohne ``tearoff=0`` auf Index 0; jedes
        ``entryconfig(0, label=…)`` beim Sprachwechsel bricht dann ab.
        """
        root = tk.Tk()
        try:
            root.withdraw()
            menubar = tk.Menu(root, tearoff=0)
            menubar.add_cascade(
                label="Datei", menu=tk.Menu(menubar, tearoff=0)
            )
            root.config(menu=menubar)
            self.assertEqual(menubar.type(0), 'cascade')
            menubar.entryconfig(0, label="File")
        finally:
            root.destroy()

    def test_scope_input_becomes_separate_items(self):
        items = ProductGeneratorGUI.scope_items
        self.assertEqual(
            items("Gehäuse, [Originalverpackung]; Kabel\nNetzteil"),
            ["Gehäuse", "Originalverpackung", "Kabel", "Netzteil"],
        )
        self.assertEqual(items("* Nur Gehäuse"), ["Nur Gehäuse"])
        self.assertEqual(items(""), [])

    def test_german_and_english_price_notations_are_parsed(self):
        parse = ProductGeneratorGUI.parse_price
        self.assertAlmostEqual(parse("1.234,56"), 1234.56)
        self.assertAlmostEqual(parse("1,234.56"), 1234.56)
        self.assertAlmostEqual(parse("1234,56"), 1234.56)
        self.assertAlmostEqual(parse("1234.56"), 1234.56)
        self.assertAlmostEqual(parse("99,90 EUR"), 99.90)
        self.assertAlmostEqual(parse("1234"), 1234.0)
        self.assertIsNone(parse("keine Zahl"))
        self.assertIsNone(parse(""))
        self.assertEqual(ProductGeneratorGUI.format_price(1234.5), "1.234,50")

    def test_shortened_body_keeps_leading_text_contiguous(self):
        stub = SimpleNamespace(legal_clause="Hinweis")
        body = "\n\n".join(["A" * 60, "B" * 400, "C" * 20])
        shortened = ProductGeneratorGUI.fit_platform_body(stub, body, 200)
        # Der zu lange Block bricht ab; spätere Absätze dürfen nicht
        # nachrücken und den Text in der Mitte auftrennen.
        self.assertTrue(shortened.startswith("A" * 60))
        self.assertNotIn("C" * 20, shortened)

    def test_duplicate_names_do_not_overwrite_files(self):
        first = self.generator.save_listing("eins", "Produkt")
        second = self.generator.save_listing("zwei", "Produkt")
        self.assertNotEqual(first, second)
        self.assertEqual(Path(first).read_text(encoding="utf-8"), "eins")
        self.assertEqual(Path(second).read_text(encoding="utf-8"), "zwei")

    def test_unrelated_partial_terms_do_not_block_online_search(self):
        self.assertEqual(
            self.generator.search_products("Google Pixel 10 Pro"), []
        )
        self.assertEqual(self.generator.search_products("Borderlands 3"), [])
        self.assertEqual(self.generator.search_products("Samsung Galaxy S21"), [])

    def test_known_family_still_returns_all_variants(self):
        names = [
            result["variant"]["name"]
            for result in self.generator.search_products("Galaxy S26")
        ]
        self.assertEqual(
            names,
            [
                "Samsung Galaxy S26",
                "Samsung Galaxy S26 Plus",
                "Samsung Galaxy S26 Ultra",
            ],
        )

    def test_sales_draft_contains_complete_editable_structure(self):
        raw = (
            "Display: 6,1 Zoll Dynamic AMOLED 2X\n"
            "Arbeitsspeicher: 8 GB\n"
            "Akku: 3.700 mAh"
        )
        draft = self.generator.build_sales_draft(
            "Samsung Galaxy S22", raw, "de"
        )
        self.assertIn("Samsung Galaxy S22", draft)
        self.assertIn("### Technische Daten", draft)
        self.assertIn("* Display: 6,1 Zoll Dynamic AMOLED 2X", draft)
        self.assertIn("### Lieferumfang", draft)
        self.assertIn("[Originalverpackung]", draft)
        # Neuware muss genauso anbietbar sein wie Gebrauchtes.
        self.assertIn(
            "[neuem / neuwertigem / sehr gutem / gutem / gebrauchtem]",
            draft,
        )
        self.assertIn("ungeöffnet originalverpackt", draft)
        self.assertNotIn(WARRANTY_CLAUSE, draft)

    def test_book_draft_uses_book_specific_fields(self):
        title = (
            "Die LET THEM Theorie: Zwei Worte, die dein Leben verändern "
            "werden - Das Buch"
        )
        raw = "Autorin: Mel Robbins\nFormat: Gebundene Ausgabe\nSeiten: 336"
        draft = self.generator.build_sales_draft(title, raw, "de")
        self.assertIn("### Buchdetails", draft)
        self.assertIn(f"* {title}", draft)
        self.assertIn("Einband und Seiten", draft)
        self.assertIn("Markierungen oder Notizen", draft)
        self.assertNotIn("### Technische Daten", draft)
        self.assertNotIn("[Originalverpackung]", draft)
        self.assertNotIn("[Ladekabel / Netzteil]", draft)


class ListingStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = ListingStore(
            Path(self.temp_dir.name) / "listings.db"
        )
        self.generator = ProductGenerator(
            products_file=default_products_file(),
            output_dir=self.temp_dir.name,
        )
        self.product_id = self.store.upsert_product(
            "Samsung Galaxy S23", identifier="4006381333931"
        )

    def tearDown(self):
        self.store.close()
        self.temp_dir.cleanup()

    def test_platform_profiles_use_verified_limits(self):
        self.assertEqual(
            PLATFORM_PROFILES["kleinanzeigen"].title_limit, 65
        )
        self.assertEqual(
            PLATFORM_PROFILES["kleinanzeigen"].description_limit, 4000
        )
        self.assertEqual(PLATFORM_PROFILES["ebay"].title_limit, 80)
        self.assertEqual(
            PLATFORM_PROFILES["ebay_detailed"].description_limit, 500000
        )
        self.assertEqual(
            PLATFORM_PROFILES["ebay_mobile"].description_limit, 800
        )

    def test_conflicting_facts_require_confirmation(self):
        self.store.add_fact(
            self.product_id, "Speicher", "128 GB", "Amazon"
        )
        self.store.add_fact(
            self.product_id, "Speicherkapazität", "256 GB", "eBay"
        )
        self.assertIn(
            "Speicherkapazität", self.store.conflicts(self.product_id)
        )
        self.assertNotIn(
            "Speicherkapazität",
            self.store.confirmed_values(self.product_id),
        )
        self.store.confirm_fact(
            self.product_id, "Speicherkapazität", "256 GB"
        )
        self.assertNotIn(
            "Speicherkapazität", self.store.conflicts(self.product_id)
        )
        self.assertEqual(
            self.store.confirmed_values(
                self.product_id
            )["Speicherkapazität"],
            "256 GB",
        )

    def test_drafts_are_independent_and_versioned(self):
        self.store.save_draft(
            self.product_id, "kleinanzeigen", "Kurzer Titel", "Text A"
        )
        self.store.save_draft(
            self.product_id, "ebay", "Längerer eBay-Titel", "Text B"
        )
        self.store.save_draft(
            self.product_id, "kleinanzeigen", "Kurzer Titel", "Text A2"
        )
        drafts = self.store.load_drafts(self.product_id)
        self.assertEqual(
            drafts["kleinanzeigen"]["description"], "Text A2"
        )
        self.assertEqual(drafts["ebay"]["description"], "Text B")
        version_count = self.store.connection.execute(
            "SELECT COUNT(*) FROM draft_versions"
        ).fetchone()[0]
        self.assertEqual(version_count, 1)

    def test_product_state_survives_later_read_only_upsert(self):
        state = {
            "condition": "Sehr gut",
            "scope": "Gerät und Ladekabel",
            "asking_price": "250",
        }
        self.store.update_product_state(self.product_id, state)
        same_id = self.store.upsert_product(
            "Samsung Galaxy S23",
            identifier="4006381333931",
            source_url="https://example.test/product",
            state=None,
        )
        self.assertEqual(same_id, self.product_id)
        self.assertEqual(self.store.product_state(self.product_id), state)

    def test_price_summary_separates_active_and_sold(self):
        self.store.add_price(
            self.product_id, "eBay", 100, kind="active", shipping=5
        )
        self.store.add_price(
            self.product_id, "eBay", 100, kind="active", shipping=5
        )
        self.store.add_price(
            self.product_id, "Kleinanzeigen", 125, kind="active"
        )
        self.store.add_price(
            self.product_id, "eBay", 90, kind="sold"
        )
        active = self.store.price_summary(self.product_id, "active")
        sold = self.store.price_summary(self.product_id, "sold")
        self.assertEqual(active["median"], 115)
        self.assertEqual(sold["median"], 90)
        self.assertEqual(active["count"], 2)

    def test_persistent_cache_and_clean_expiry(self):
        self.store.cache_put("product:1", {"name": "Pixel"}, 60)
        self.assertEqual(
            self.store.cache_get("product:1"), {"name": "Pixel"}
        )
        self.store.connection.execute(
            "UPDATE cache SET expires_at=0 WHERE cache_key='product:1'"
        )
        self.store.connection.commit()
        self.assertIsNone(self.store.cache_get("product:1"))

    def test_export_package_keeps_user_folder_simple(self):
        self.store.add_fact(
            self.product_id, "Marke", "Samsung", "eBay",
            "https://www.ebay.de/itm/1",
        )
        self.store.save_draft(
            self.product_id, "kleinanzeigen",
            "Samsung Galaxy S23", "Kleinanzeigen-Text"
        )
        self.store.save_draft(
            self.product_id, "ebay",
            "Samsung Galaxy S23", "eBay-Text"
        )
        folder = self.store.export_package(
            self.product_id, self.temp_dir.name
        )
        self.assertTrue((folder / "beitrag-kleinanzeigen.txt").exists())
        self.assertTrue((folder / "beitrag-ebay.txt").exists())
        self.assertTrue(
            (folder / ".creationtool" / "produktdaten.json").exists()
        )
        self.assertIn(
            "https://www.ebay.de/itm/1",
            (folder / ".creationtool" / "quellen.txt").read_text(
                encoding="utf-8"
            ),
        )

    def test_safe_filename_removes_windows_metacharacters(self):
        self.assertEqual(safe_filename('A/B:C*D?'), "A B C D")
        self.assertEqual(
            canonical_fact_key("Storage Capacity"), "Speicherkapazität"
        )

    def test_disc_draft_uses_physical_media_fields(self):
        draft = self.generator.build_sales_draft(
            "Blade Runner 2049 Blu-ray",
            "Format: Blu-ray\nLaufzeit: 164 Minuten\nRegion: B",
            "de",
        )
        self.assertIn("### Medienangaben", draft)
        self.assertIn("[Originalhülle]", draft)
        self.assertIn("[Booklet / Einleger]", draft)
        self.assertIn("Kratzer sind", draft)
        self.assertIn("Wiedergabe wurde", draft)
        self.assertNotIn("[Ladekabel / Netzteil]", draft)

    def test_disc_player_remains_hardware(self):
        draft = self.generator.build_sales_draft(
            "Sony Blu-ray Player", "Anschluss: HDMI", "de"
        )
        self.assertIn("### Technische Daten", draft)
        self.assertNotIn("### Medienangaben", draft)


class OnlineProviderTests(unittest.TestCase):
    def test_stored_secret_placeholder_is_never_saved_as_a_secret(self):
        self.assertEqual(
            ProductGeneratorGUI.entered_secret(SECRET_PLACEHOLDER), ""
        )
        self.assertEqual(ProductGeneratorGUI.entered_secret(""), "")
        self.assertEqual(
            ProductGeneratorGUI.entered_secret("  new-secret  "),
            "new-secret",
        )

    def test_private_and_local_product_urls_are_blocked(self):
        for url in (
            "http://localhost/admin",
            "http://127.0.0.1/",
            "http://192.168.1.1/",
            "file:///C:/Windows/win.ini",
        ):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    ProductGeneratorGUI.validate_remote_url(url)
        ProductGeneratorGUI.validate_remote_url(
            "https://www.amazon.de/dp/B07QB3369M"
        )

    def test_insecure_keyring_backend_is_rejected(self):
        backend = type("NullKeyring", (), {})()
        fake_keyring = type(
            "FakeKeyringModule", (),
            {"get_keyring": staticmethod(lambda: backend)},
        )
        with patch.dict("sys.modules", {"keyring": fake_keyring}):
            with self.assertRaises(RuntimeError):
                ProductGeneratorGUI.secure_keyring()

    def test_ebay_sandbox_uses_sandbox_api_host(self):
        gui = ProductGeneratorGUI.__new__(ProductGeneratorGUI)
        gui.ebay_environment = "sandbox"
        gui._ebay_access_token = None
        gui._ebay_access_token_expires = 0
        response = Mock()
        response.read.return_value = json.dumps({
            "access_token": "token", "expires_in": 7200
        }).encode("utf-8")
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with (
            patch.object(gui, "get_secret", side_effect=["id", "secret"]),
            patch("urllib.request.urlopen", return_value=response) as urlopen,
        ):
            gui.get_ebay_access_token()
        self.assertIn(
            "api.sandbox.ebay.com", urlopen.call_args.args[0].full_url
        )

    def test_kleinanzeigen_agent_maps_public_ad_metadata(self):
        gui = ProductGeneratorGUI.__new__(ProductGeneratorGUI)
        payload = {
            "data": {"ads": [{
                "ad_id": "123",
                "title": "Samsung Galaxy S23",
                "ad_url": "https://www.kleinanzeigen.de/s-anzeige/123",
                "category": {"name": "Handys"},
                "attributes": [{"label": "Farbe", "value": "Schwarz"}],
                "location": {"city": "Berlin"},
            }]}
        }
        response = Mock()
        response.read.return_value = json.dumps(payload).encode("utf-8")
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with (
            patch.object(gui, "get_secret", return_value="secret"),
            patch("urllib.request.urlopen", return_value=response) as urlopen,
        ):
            results = gui.search_kleinanzeigen_agent("Galaxy S23")
        self.assertEqual(results[0][0], "Samsung Galaxy S23")
        self.assertIn("Kategorie: Handys", results[0][1])
        self.assertIn("Farbe: Schwarz", results[0][1])
        self.assertEqual(
            results[0][2], "https://www.kleinanzeigen.de/s-anzeige/123"
        )
        request = urlopen.call_args.args[0]
        self.assertEqual(
            request.headers["User-agent"],
            "eBay-Kleinanzeigen-Creationtool/0.2",
        )

    def test_kleinanzeigen_connection_uses_regular_minimal_query(self):
        gui = ProductGeneratorGUI.__new__(ProductGeneratorGUI)
        response = Mock()
        response.read.return_value = json.dumps({
            "success": True, "data": {"ads": []}
        }).encode("utf-8")
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with (
            patch.object(gui, "get_secret", return_value="secret"),
            patch("urllib.request.urlopen", return_value=response) as urlopen,
        ):
            self.assertTrue(gui.test_kleinanzeigen_agent_connection())
        request = urlopen.call_args.args[0]
        self.assertIn("q=iphone", request.full_url)
        self.assertIn("size=1", request.full_url)

    def test_amazon_gallery_extracts_unique_high_resolution_images(self):
        html = r'''
        <img id="landingImage"
             data-old-hires="https://m.media-amazon.com/images/I/MAIN1._SL1500_.jpg">
        <script>
        'colorImages': {'initial': [
          {"hiRes":"https://m.media-amazon.com/images/I/MAIN1._SL1500_.jpg",
           "large":"https://m.media-amazon.com/images/I/MAIN1._SX600_.jpg"},
          {"hiRes":"https://m.media-amazon.com/images/I/SIDE2._SL1500_.jpg"},
          {"large":"https://m.media-amazon.com/images/I/BACK3._SX600_.jpg"}
        ]},
        'colorToAsin': {'initial': '{}'}
        </script>
        '''
        urls = ProductGeneratorGUI.extract_product_image_urls(
            html, "https://www.amazon.de/dp/B07QB3369M"
        )
        self.assertEqual(len(urls), 3)
        self.assertTrue(all("._SL1500_." in url for url in urls))
        self.assertIn("/MAIN1.", urls[0])
        self.assertIn("/SIDE2.", urls[1])
        self.assertIn("/BACK3.", urls[2])

    def test_ebay_search_uses_gtin_and_german_marketplace(self):
        gui = ProductGeneratorGUI.__new__(ProductGeneratorGUI)
        payload = {
            "itemSummaries": [{
                "title": "Testprodukt",
                "condition": "Neu",
                "categories": [{"categoryName": "Elektronik"}],
                "itemWebUrl": "https://www.ebay.de/itm/123",
            }]
        }
        response = Mock()
        response.read.return_value = json.dumps(payload).encode("utf-8")
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with (
            patch.object(gui, "get_ebay_access_token", return_value="token"),
            patch("urllib.request.urlopen", return_value=response) as urlopen,
        ):
            results = gui.search_ebay("4006381333931")
        request = urlopen.call_args.args[0]
        self.assertIn("gtin=4006381333931", request.full_url)
        self.assertEqual(
            request.headers["X-ebay-c-marketplace-id"], "EBAY_DE"
        )
        self.assertEqual(results[0][0], "Testprodukt")
        self.assertIn("Kategorie: Elektronik", results[0][1])

    def test_ebay_category_suggestions_include_full_path(self):
        gui = ProductGeneratorGUI.__new__(ProductGeneratorGUI)
        with (
            patch.object(
                gui, "get_ebay_default_category_tree_id", return_value="77"
            ),
            patch.object(gui, "ebay_api_json", return_value={
                "categorySuggestions": [{
                    "category": {
                        "categoryId": "9355",
                        "categoryName": "Handys & Smartphones",
                    },
                    "categoryTreeNodeAncestors": [
                        {"category": {
                            "categoryId": "15032",
                            "categoryName": "Handys & Kommunikation",
                        }},
                        {"category": {
                            "categoryId": "293",
                            "categoryName": "Elektronik",
                        }},
                    ],
                }]
            }) as api,
        ):
            categories = gui.get_ebay_category_suggestions(
                "Samsung Galaxy S23"
            )
        self.assertEqual(categories[0]["id"], "9355")
        self.assertEqual(
            categories[0]["path"],
            "Elektronik > Handys & Kommunikation > Handys & Smartphones",
        )
        self.assertEqual(
            api.call_args.args[1], {"q": "Samsung Galaxy S23"}
        )

    def test_ebay_aspects_mark_required_and_sort_them_first(self):
        gui = ProductGeneratorGUI.__new__(ProductGeneratorGUI)
        payload = {
            "aspects": [
                {
                    "localizedAspectName": "Farbe",
                    "aspectConstraint": {"aspectUsage": "RECOMMENDED"},
                    "aspectValues": [{"localizedValue": "Schwarz"}],
                },
                {
                    "localizedAspectName": "Marke",
                    "aspectConstraint": {
                        "aspectRequired": True,
                        "itemToAspectCardinality": "SINGLE",
                    },
                    "aspectValues": [{"localizedValue": "Samsung"}],
                },
            ]
        }
        with (
            patch.object(
                gui, "get_ebay_default_category_tree_id", return_value="77"
            ),
            patch.object(gui, "ebay_api_json", return_value=payload),
        ):
            aspects = gui.get_ebay_item_aspects("9355")
        self.assertEqual([item["name"] for item in aspects], ["Marke", "Farbe"])
        self.assertTrue(aspects[0]["required"])
        self.assertTrue(aspects[1]["recommended"])
        self.assertEqual(aspects[1]["values"], ["Schwarz"])

    def test_ebay_required_aspect_completeness(self):
        aspects = [
            {"name": "Marke", "required": True},
            {"name": "Farbe", "required": False},
            {"name": "Speicherkapazität", "required": True},
        ]
        self.assertEqual(
            ProductGeneratorGUI.missing_required_ebay_aspects(
                aspects, {"Marke": "Samsung"}
            ),
            ["Speicherkapazität"],
        )

    def test_ebay_item_details_map_category_aspects_and_images(self):
        gui = ProductGeneratorGUI.__new__(ProductGeneratorGUI)
        payload = {
            "itemId": "v1|123|0",
            "categoryId": "9355",
            "categoryPath": "Elektronik|Handys & Smartphones",
            "localizedAspects": [
                {"name": "Farbe", "value": "Schwarz"}
            ],
            "image": {"imageUrl": "https://i.ebayimg.com/main.jpg"},
            "product": {
                "brand": "Samsung",
                "gtins": ["4006381333931"],
                "additionalImages": [
                    {"imageUrl": "https://i.ebayimg.com/side.jpg"}
                ],
                "aspectGroups": [{
                    "aspects": [{
                        "localizedName": "Marke",
                        "localizedValues": ["Samsung"],
                    }]
                }],
            },
        }
        with patch.object(gui, "ebay_api_json", return_value=payload):
            details = gui.get_ebay_item_details("v1|123|0")
        self.assertEqual(details["category_id"], "9355")
        self.assertEqual(
            details["category_path"],
            "Elektronik > Handys & Smartphones",
        )
        self.assertEqual(details["aspect_values"]["Marke"], "Samsung")
        self.assertEqual(details["aspect_values"]["Farbe"], "Schwarz")
        self.assertEqual(len(details["image_urls"]), 2)

    def test_isbn_10_and_13_are_normalized_and_cross_searched(self):
        self.assertEqual(
            ProductGeneratorGUI.isbn_search_variants("ISBN-10: 3442180651"),
            ["3442180651", "9783442180653"],
        )
        self.assertEqual(
            ProductGeneratorGUI.isbn_search_variants("978-3442180653"),
            ["9783442180653", "3442180651"],
        )

    def test_dnb_isbn_returns_exact_structured_book(self):
        gui = ProductGeneratorGUI.__new__(ProductGeneratorGUI)
        xml = """<?xml version="1.0"?>
        <searchRetrieveResponse xmlns="http://www.loc.gov/zing/srw/">
          <recordData>
            <record xmlns="http://www.loc.gov/MARC21/slim">
              <controlfield tag="001">1358109125</controlfield>
              <datafield tag="020"><subfield code="a">9783442180653</subfield></datafield>
              <datafield tag="020"><subfield code="a">3442180651</subfield></datafield>
              <datafield tag="100"><subfield code="a">Robbins, Melanie Lee</subfield></datafield>
              <datafield tag="245">
                <subfield code="a">Die LET THEM Theorie</subfield>
                <subfield code="b">zwei Worte, die dein Leben verändern werden</subfield>
              </datafield>
              <datafield tag="264">
                <subfield code="b">Goldmann</subfield>
                <subfield code="c">April 2025</subfield>
              </datafield>
              <datafield tag="300"><subfield code="a">364 Seiten</subfield></datafield>
              <datafield tag="856">
                <subfield code="u">https://www.penguin.de/ean/9783442180653</subfield>
              </datafield>
            </record>
          </recordData>
        </searchRetrieveResponse>"""
        with patch.object(gui, 'fetch_url', return_value=xml):
            results = gui.search_dnb_isbn("978-3442180653")
        self.assertEqual(
            results[0][0],
            "Die LET THEM Theorie: zwei Worte, die dein Leben verändern werden",
        )
        self.assertIn("Autor: Robbins, Melanie Lee", results[0][1])
        self.assertIn("ISBN-13: 9783442180653", results[0][1])
        self.assertEqual(
            results[0][2], "https://www.penguin.de/ean/9783442180653"
        )
        self.assertTrue(unicodedata.is_normalized("NFC", results[0][0]))

    def test_zvab_fallback_resolves_independent_isbn(self):
        gui = ProductGeneratorGUI.__new__(ProductGeneratorGUI)
        html = """
        <h1>Daytrading für Einsteiger - Softcover</h1>
        <dl>
          <dt>Verlag</dt><dd>Independently published</dd>
          <dt>Erscheinungsdatum</dt><dd>2022</dd>
          <dt>ISBN 13</dt><dd>9798830537308</dd>
          <dt>Anzahl der Seiten</dt><dd>143</dd>
        </dl>
        """
        with patch.object(gui, 'fetch_url', return_value=html):
            results = gui.search_zvab_isbn("979-8830537308")
        self.assertEqual(results[0][0], "Daytrading für Einsteiger")
        self.assertIn("Anzahl der Seiten: 143", results[0][1])
        self.assertEqual(
            results[0][2],
            "https://www.zvab.com/products/isbn/9798830537308",
        )

    def test_decomposed_dnb_umlauts_are_normalized(self):
        self.assertEqual(
            ProductGeneratorGUI.clean_marc_text("Mu\u0308nchen vera\u0308ndern"),
            "München verändern",
        )

    def test_model_spellings_are_searched_in_both_forms(self):
        self.assertEqual(
            ProductGeneratorGUI.expand_search_spellings("Samsung Galaxy S23"),
            ["Samsung Galaxy S23", "Samsung Galaxy S 23"],
        )
        self.assertEqual(
            ProductGeneratorGUI.expand_search_spellings("Galaxy S 22"),
            ["Galaxy S 22", "Galaxy S22"],
        )

    def test_results_survive_when_second_spelling_is_blocked(self):
        gui = ProductGeneratorGUI.__new__(ProductGeneratorGUI)
        provider = Mock(side_effect=[
            [("Samsung Galaxy S23", "Treffer", "https://example.test/s23")],
            RuntimeError("HTTP 403"),
        ])
        results = gui.search_spelling_variants(provider, "Galaxy S23")
        self.assertEqual(results[0][0], "Samsung Galaxy S23")

    def test_ean_is_forwarded_unchanged_to_product_search(self):
        gui = ProductGeneratorGUI.__new__(ProductGeneratorGUI)
        provider = Mock(return_value=[
            ("EAN-Produkt", "Treffer", "https://example.test/product")
        ])
        results = gui.search_spelling_variants(provider, "8806090891108")
        provider.assert_called_once_with("8806090891108")
        self.assertEqual(results[0][0], "EAN-Produkt")

    def test_global_web_suggestions_return_fantec_models(self):
        gui = ProductGeneratorGUI.__new__(ProductGeneratorGUI)
        response = """["Fantec", [
          "fantec",
          "fantech",
          "fantec qb-35us3-6g",
          "fantec gehäuse"
        ]]"""
        with patch.object(gui, 'fetch_url', return_value=response):
            results = gui.search_web_suggestions("Fantec")
        self.assertEqual(
            [result[0] for result in results],
            ["fantec", "fantec qb-35us3-6g", "fantec gehäuse"],
        )
        # Hostname vergleichen statt Teilzeichenkette: sonst wuerde der Test
        # genau das Muster vorleben, das im Quelltext beseitigt wurde.
        self.assertTrue(all(
            ProductGeneratorGUI.host_is(item[2], "suggestqueries.google.com")
            for item in results
        ))

    def test_stale_online_search_is_rejected(self):
        gui = ProductGeneratorGUI.__new__(ProductGeneratorGUI)
        gui._search_generation = 8
        gui.search_var = type(
            "SearchVar", (), {"get": lambda self: "Google Pixel 10 Pro"}
        )()
        self.assertTrue(gui.is_search_current("Google Pixel 10 Pro", 8))
        self.assertFalse(gui.is_search_current("Google Pixel 10", 7))

    def test_geizhals_product_extracts_specifications(self):
        gui = ProductGeneratorGUI.__new__(ProductGeneratorGUI)
        html = """
        <h1>Google Pixel 10</h1>
        <dl>
          <dt>Display</dt><dd>6,3 Zoll AMOLED, 120 Hz</dd>
          <dt>RAM</dt><dd>12 GB</dd>
        </dl>
        """
        title, description = gui.extract_comparison_product(html, "geizhals")
        self.assertEqual(title, "Google Pixel 10")
        self.assertIn("Display: 6,3 Zoll AMOLED, 120 Hz", description)
        self.assertIn("RAM: 12 GB", description)

    def test_direct_geizhals_url_is_supported(self):
        gui = ProductGeneratorGUI.__new__(ProductGeneratorGUI)
        html = "<h1>Google Pixel 10</h1><dl><dt>RAM</dt><dd>12 GB</dd></dl>"
        url = "https://geizhals.de/google-pixel-10-v209140.html"
        with patch.object(gui, "fetch_url", return_value=html):
            results = gui.search_geizhals(url)
        self.assertEqual(results[0][0], "Google Pixel 10")
        self.assertEqual(results[0][2], url)

    def test_amazon_product_extracts_title_and_bullets(self):
        gui = ProductGeneratorGUI.__new__(ProductGeneratorGUI)
        html = """
        <span id="productTitle">FANTEC Qb-X2US3R Gehäuse</span>
        <div id="feature-bullets"><ul>
          <li><span>Externes Gehäuse für zwei SATA-Festplatten.</span></li>
          <li><span>Unterstützt RAID 0, RAID 1, JBOD und BIG.</span></li>
        </ul></div>
        """
        title, description = gui.extract_amazon_product(html)
        self.assertEqual(title, "FANTEC Qb-X2US3R Gehäuse")
        self.assertIn("zwei SATA-Festplatten", description)
        self.assertIn("RAID 0", description)

    def test_product_cover_is_read_from_open_graph_metadata(self):
        html = (
            '<meta property="og:image" '
            'content="https://images.example.test/cover.jpg">'
        )
        url = ProductGeneratorGUI.extract_product_image_url(
            html, "https://example.test/product"
        )
        self.assertEqual(url, "https://images.example.test/cover.jpg")

    def test_amazon_landing_image_prefers_high_resolution_url(self):
        html = """
        <img alt="Blu-ray"
             src="https://m.media-amazon.com/images/I/51L3D0GdqXL.jpg"
             data-old-hires="https://m.media-amazon.com/images/I/91HkT3V28EL.*SL1500*.jpg"
             id="landingImage">
        """
        url = ProductGeneratorGUI.extract_product_image_url(
            html, "https://www.amazon.de/gp/product/B002UCREGO"
        )
        self.assertEqual(
            url,
            "https://m.media-amazon.com/images/I/91HkT3V28EL._SL1500_.jpg",
        )

    def test_amazon_dynamic_image_uses_largest_available_variant(self):
        html = """
        <img id="landingImage" data-a-dynamic-image="{
          &quot;https://m.media-amazon.com/images/I/book.*SY342*.jpg&quot;:[342,208],
          &quot;https://m.media-amazon.com/images/I/book.*SY522*.jpg&quot;:[522,318]
        }">
        """
        url = ProductGeneratorGUI.extract_product_image_url(
            html, "https://www.amazon.de/dp/B07QV2QMT3"
        )
        self.assertIn("SY522", url)

    def test_penguin_book_cover_markup_is_supported(self):
        html = """
        <img src="/resource/responsive-image/4503010/220/7/book.jpg.webp"
             alt="Die LET THEM Theorie" class="scaled_m04 prh-w-full">
        """
        url = ProductGeneratorGUI.extract_product_image_url(
            html, "https://www.penguin.de/ean/9783442180653"
        )
        self.assertEqual(
            url,
            "https://www.penguin.de/resource/responsive-image/"
            "4503010/220/7/book.jpg.webp",
        )

    def test_abebooks_isbn_cover_is_supported(self):
        html = (
            '<img src="https://pictures.abebooks.com/isbn/'
            '9798830537308-de.jpg">'
        )
        url = ProductGeneratorGUI.extract_product_image_url(
            html, "https://www.zvab.com/products/isbn/9798830537308"
        )
        self.assertEqual(
            url, "https://pictures.abebooks.com/isbn/9798830537308-de.jpg"
        )

    def test_amazon_search_builds_canonical_product_result(self):
        gui = ProductGeneratorGUI.__new__(ProductGeneratorGUI)
        search_html = """
        <div role="listitem" data-asin="B01GSWFOA4"
             data-component-type="s-search-result">
          <h2 aria-label="FANTEC Qb-X2US3R Gehäuse"></h2>
        </div>
        """
        product_html = """
        <span id="productTitle">FANTEC Qb-X2US3R Gehäuse</span>
        <div id="feature-bullets"><ul>
          <li><span>Externes Gehäuse für zwei SATA-Festplatten.</span></li>
        </ul></div>
        """
        with patch.object(
            gui, 'fetch_url', side_effect=[search_html, product_html]
        ):
            results = gui.search_amazon("Fantec Gehäuse")
        self.assertEqual(results[0][0], "FANTEC Qb-X2US3R Gehäuse")
        self.assertEqual(results[0][2], "https://www.amazon.de/dp/B01GSWFOA4")

    def test_amazon_product_url_is_routed_directly(self):
        gui = ProductGeneratorGUI.__new__(ProductGeneratorGUI)
        provider_name, provider = gui.provider_for_url(
            "https://www.amazon.de/Produktname/dp/B07Q9QLH55"
        )
        self.assertEqual(provider_name, "Amazon-Link")
        # Der Wrapper ruft search_amazon auf und weicht bei einer Blockade auf
        # die Vergleichsportale aus, statt ohne Treffer aufzugeben.
        self.assertEqual(
            provider.__func__,
            gui.search_amazon_url_with_fallback.__func__,
        )

    def test_truncated_amazon_title_uses_unique_suggestion_completion(self):
        gui = ProductGeneratorGUI.__new__(ProductGeneratorGUI)
        suggestions = [(
            "marantz m cr612 melody x netzwerk cd receiver",
            "Vorschlag",
            "https://suggestqueries.google.com/",
        )]
        with patch.object(
            gui, "search_web_suggestions", return_value=suggestions
        ):
            repaired = gui.repair_truncated_amazon_title(
                "Marantz M-CR612 Melody X Netzwerk Receiv"
            )
        self.assertEqual(
            repaired,
            "Marantz M-CR612 Melody X Netzwerk Receiver",
        )

    def test_ambiguous_title_completion_keeps_original(self):
        title = "Produkt Receiv"
        repaired = ProductGeneratorGUI.complete_truncated_title(
            title, ["Produkt Receiver", "Produkt Receiving"]
        )
        self.assertEqual(repaired, title)

    def test_asin_is_read_from_the_product_segment_only(self):
        asin = ProductGeneratorGUI.amazon_asin
        # Der Slug endet auf ein zehn Zeichen langes Wortende direkt vor dem
        # Schraegstrich; es darf nicht als ASIN gelesen werden.
        self.assertEqual(
            asin(
                "https://www.amazon.de/QB-X2US3R-Festplattengeh%C3%A4use-"
                "Festplatten-SUPERSPEED-temperaturgeregelt/dp/B01GSWFOA4"
            ),
            "B01GSWFOA4",
        )
        self.assertEqual(
            asin("https://www.amazon.de/dp/B01GSWFOA4?ref=x&th=1"),
            "B01GSWFOA4",
        )
        self.assertEqual(
            asin("https://www.amazon.de/gp/product/B01GSWFOA4/"),
            "B01GSWFOA4",
        )
        self.assertEqual(asin("B01GSWFOA4"), "B01GSWFOA4")
        # Ohne Produktsegment wird nicht geraten.
        self.assertEqual(asin("https://www.amazon.de/s?k=festplatte"), "")
        self.assertEqual(asin("Samsung Galaxy S23"), "")

    def test_amazon_urls_without_asin_become_a_real_query(self):
        query = ProductGeneratorGUI.amazon_search_query
        self.assertEqual(
            query("https://www.amazon.de/s?k=Fantec+QB-X2US3R"),
            "Fantec QB-X2US3R",
        )
        self.assertEqual(
            query("https://www.amazon.de/Fantec-QB-X2US3R-Gehaeuse/b/12345"),
            "Fantec QB X2US3R Gehaeuse",
        )
        self.assertEqual(query("Fantec QB-X2US3R"), "Fantec QB-X2US3R")

    def test_failed_searches_are_never_cached(self):
        found = [("Titel", "Text", "https://example.test/p")]
        seconds = ProductGeneratorGUI.cache_seconds_for
        self.assertEqual(seconds([], ["Amazon blockiert"]), 0)
        self.assertEqual(seconds([], []), 0)
        self.assertGreater(seconds(found, []), seconds(found, ["Idealo 503"]))
        self.assertGreater(seconds(found, ["Idealo 503"]), 0)

    def test_a_blocked_search_does_not_hide_a_later_retry(self):
        key = ("regressionstest", ())
        found = [("Titel", "Text", "https://example.test/p")]
        try:
            ProductGeneratorGUI._cache_store(key, [], ["blockiert"])
            self.assertIsNone(ProductGeneratorGUI._cache_lookup(key))
            ProductGeneratorGUI._cache_store(key, found, [])
            self.assertEqual(
                ProductGeneratorGUI._cache_lookup(key), (found, [])
            )
            # Ein spaeterer Fehlschlag darf keinen veralteten Treffer stehen
            # lassen.
            ProductGeneratorGUI._cache_store(key, [], ["blockiert"])
            self.assertIsNone(ProductGeneratorGUI._cache_lookup(key))
        finally:
            ProductGeneratorGUI._search_cache.pop(key, None)

    def test_model_number_is_derived_from_a_product_slug(self):
        model = ProductGeneratorGUI.model_query_from_slug
        self.assertEqual(
            model(
                "https://www.amazon.de/QB-X2US3R-Festplattengeh%C3%A4use-"
                "Festplatten-SUPERSPEED-temperaturgeregelt/dp/B01GSWFOA4"
            ),
            "QB-X2US3R",
        )
        self.assertEqual(
            model("https://www.amazon.de/Samsung-Galaxy-S23-Smartphone/dp/X"),
            "Samsung-Galaxy-S23",
        )
        self.assertEqual(model("https://www.amazon.de/dp/B01GSWFOA4"), "")

    def test_blocked_amazon_link_falls_back_to_other_sources(self):
        gui = ProductGeneratorGUI.__new__(ProductGeneratorGUI)
        gui.language = "de"
        url = (
            "https://www.amazon.de/QB-X2US3R-Festplatten-"
            "temperaturgeregelt/dp/B01GSWFOA4"
        )
        found = ("Fantec QB-X2US3R, schwarz", "Online gefunden", "https://g.test/x")
        with patch.object(
            gui, 'search_amazon',
            side_effect=RuntimeError("Zugriff durch Amazon-Captcha blockiert"),
        ), patch.object(
            gui, 'search_geizhals', return_value=[found]
        ) as geizhals, patch.object(
            gui, 'search_idealo', return_value=[]
        ), patch.object(gui, 'search_wikipedia', return_value=[]):
            results = ProductGeneratorGUI.search_amazon_url_with_fallback(
                gui, url
            )
        # Die Modellnummer aus dem Slug, nicht die nur Amazon bekannte ASIN.
        geizhals.assert_called_once_with("QB-X2US3R")
        self.assertEqual(results, [found])

    def test_navigation_and_script_links_are_not_products(self):
        is_product = ProductGeneratorGUI.is_product_page_link
        base = "https://geizhals.de"
        self.assertTrue(
            is_product("/fantec-qb-x2us3r-schwarz-1826-a1471139.html", base)
        )
        self.assertFalse(is_product("javascript:;", base))
        self.assertFalse(is_product("?fs=QB-X2US3R&hloc=de&cat=gehhd", base))
        self.assertFalse(is_product("https://geizhals.de/?fs=X&mfc=5872", base))
        self.assertFalse(is_product("#top", base))

    def test_known_amazon_fragment_is_repaired_without_network(self):
        gui = ProductGeneratorGUI.__new__(ProductGeneratorGUI)
        with patch.object(
            gui,
            "search_web_suggestions",
            side_effect=RuntimeError("offline"),
        ):
            repaired = gui.repair_truncated_amazon_title(
                "Marantz M-CR612 Melody X Netzwerk Receiv"
            )
        self.assertEqual(
            repaired,
            "Marantz M-CR612 Melody X Netzwerk Receiver",
        )
        self.assertIn(
            "Netzwerk Receiver",
            gui.repair_known_fragments_in_text(
                "Zum Verkauf: Netzwerk Receiv"
            ),
        )

    def test_generic_product_url_extracts_open_graph_data(self):
        gui = ProductGeneratorGUI.__new__(ProductGeneratorGUI)
        html = """
        <meta property="og:title" content="Hersteller Produkt X">
        <meta name="description" content="Eine ausführliche Beschreibung">
        """
        with patch.object(gui, "fetch_url", return_value=html):
            results = gui.search_direct_product_url(
                "https://manufacturer.example/product-x"
            )
        self.assertEqual(results[0][0], "Hersteller Produkt X")
        self.assertIn("ausführliche Beschreibung", results[0][1])

    def test_idealo_url_falls_back_to_other_product_sources(self):
        gui = ProductGeneratorGUI.__new__(ProductGeneratorGUI)
        fallback = [(
            "Marantz Melody X M-CR612",
            "Technische Daten",
            "https://geizhals.de/marantz-melody-x-m-cr612-v55384.html",
        )]
        with (
            patch.object(gui, "search_idealo", side_effect=RuntimeError("503")),
            patch.object(gui, "search_geizhals", return_value=fallback),
            patch.object(gui, "search_amazon", return_value=[]),
            patch.object(gui, "search_wikipedia", return_value=[]),
        ):
            results = gui.search_comparison_url_with_fallback(
                "https://idealo.de/preisvergleich/OffersOfProduct/"
                "6543906_-melody-x-m-cr612-marantz.html"
            )
        self.assertEqual(results, fallback)
        self.assertEqual(
            gui.product_name_from_url(
                "https://idealo.de/preisvergleich/OffersOfProduct/"
                "6543906_-melody-x-m-cr612-marantz.html"
            ),
            "melody x m cr612 marantz",
        )

    def test_amazon_sponsored_results_are_excluded(self):
        gui = ProductGeneratorGUI.__new__(ProductGeneratorGUI)
        search_html = """
        <div data-asin="B000000001" data-component-type="s-search-result">
          <span class="puis-sponsored-label-text">Gesponsert</span>
          <h2 aria-label="Gesponserte Anzeige – Falsches Buch"></h2>
        </div>
        <div data-asin="B000000002" data-component-type="s-search-result">
          <h2 aria-label="Das richtige Buch"></h2>
        </div>
        """
        with patch.object(gui, 'fetch_url', return_value=search_html):
            results = gui.search_amazon("Buchtitel")
        self.assertEqual([item[0] for item in results], ["Das richtige Buch"])

    def test_sponsored_and_offer_titles_are_globally_rejected(self):
        self.assertTrue(ProductGeneratorGUI.is_unwanted_search_result(
            "Gesponserte Anzeige – Fremdes Buch"
        ))
        self.assertTrue(ProductGeneratorGUI.is_unwanted_search_result("1 Angebot"))
        self.assertFalse(ProductGeneratorGUI.is_unwanted_search_result(
            "Die LET THEM Theorie"
        ))

    def test_wikipedia_search_returns_google_pixel_models(self):
        gui = ProductGeneratorGUI.__new__(ProductGeneratorGUI)
        gui.language = "de"
        response = """{
          "query": {"pages": [
            {
              "index": 1,
              "title": "Google Pixel",
              "extract": "Pixel ist eine Gerätereihe von Google.",
              "fullurl": "https://de.wikipedia.org/wiki/Google_Pixel"
            },
            {
              "index": 2,
              "title": "Pixel 8",
              "extract": "Google Pixel 8 und Pixel 8 Pro sind Smartphones.",
              "fullurl": "https://de.wikipedia.org/wiki/Pixel_8"
            },
            {
              "index": 3,
              "title": "Google Lens",
              "extract": "Eine App von Google.",
              "fullurl": "https://de.wikipedia.org/wiki/Google_Lens"
            }
          ]}
        }"""
        with patch.object(gui, 'fetch_url', return_value=response):
            results = gui.search_wikipedia("Google Pixel")
        self.assertEqual(
            [result[0] for result in results],
            ["Google Pixel", "Google Pixel 8"],
        )
        self.assertIn("wikipedia.org/wiki/Pixel_8", results[1][2])

    def test_wikipedia_variant_query_keeps_family_page(self):
        gui = ProductGeneratorGUI.__new__(ProductGeneratorGUI)
        gui.language = "de"
        response = """{
          "query": {"pages": [{
            "index": 1,
            "title": "Pixel 10",
            "extract": "Google Pixel 10, Pixel 10 Pro und Pixel 10 Pro XL.",
            "fullurl": "https://de.wikipedia.org/wiki/Pixel_10"
          }]}
        }"""
        with patch.object(gui, 'fetch_url', return_value=response):
            results = gui.search_wikipedia("Google Pixel 10 Pro")
        self.assertEqual(results[0][0], "Google Pixel 10 Pro")
        self.assertEqual(results[1][0], "Google Pixel 10")

    def test_block_pages_are_reported_instead_of_parsed(self):
        gui = ProductGeneratorGUI.__new__(ProductGeneratorGUI)
        with patch.object(
            gui,
            'fetch_url',
            return_value="Enable JavaScript and cookies to continue",
        ):
            with self.assertRaisesRegex(RuntimeError, "Cloudflare"):
                gui.search_search_page("https://example.test", "https://example.test")

    def test_geizhals_navigation_links_are_not_products(self):
        gui = ProductGeneratorGUI.__new__(ProductGeneratorGUI)
        html = """
        <h2><a href="/instagram">Geizhals auf Instagram</a></h2>
        <h2><a href="/help">Bitte beachte die Hinweise zum Versand</a></h2>
        <h2><a href="/book">Die LET THEM Theorie</a></h2>
        """
        with patch.object(gui, "fetch_url", return_value=html):
            results = gui.search_search_page(
                "https://geizhals.de/?fs=isbn", "https://geizhals.de"
            )
        self.assertEqual([item[0] for item in results], ["Die LET THEM Theorie"])

    def test_english_book_draft_translates_controlled_metadata(self):
        draft = ProductGenerator.build_sales_draft(
            "Die LET THEM Theorie",
            "\n".join([
                "Autor: Robbins, Mel",
                "Verlag: Goldmann",
                "Erscheinungsdatum: April 2025",
                "Sprache: Deutsch",
                "Einband: Taschenbuch",
                "Anzahl der Seiten: 364",
            ]),
            "en",
        )
        self.assertIn("Book description and details", draft)
        self.assertIn("### Book details", draft)
        self.assertIn("* Author: Robbins, Mel", draft)
        self.assertIn("* Publisher: Goldmann", draft)
        self.assertIn("* Publication date: April 2025", draft)
        self.assertIn("* Language: German", draft)
        self.assertIn("* Binding: Paperback", draft)
        self.assertIn("* Pages: 364", draft)
        self.assertIn("### Included", draft)
        self.assertIn("Please remove or complete", draft)
        self.assertNotIn("### Buchdetails", draft)
        self.assertNotIn("Nicht Zutreffendes", draft)

    def test_open_library_isbn_returns_structured_book(self):
        gui = ProductGeneratorGUI.__new__(ProductGeneratorGUI)
        response = json.dumps({
            "docs": [{
                "key": "/works/OL123W",
                "title": "Beispielbuch",
                "author_name": ["Erika Beispiel"],
                "publisher": ["Testverlag"],
                "first_publish_year": 2025,
                "number_of_pages_median": 240,
            }]
        })
        with patch.object(gui, "fetch_url", return_value=response):
            results = gui.search_open_library("978-3442180653")
        self.assertEqual(results[0][0], "Beispielbuch")
        self.assertIn("Autor: Erika Beispiel", results[0][1])
        self.assertIn("Anzahl der Seiten: 240", results[0][1])
        self.assertEqual(
            results[0][2], "https://openlibrary.org/works/OL123W"
        )

    def test_google_books_isbn_returns_structured_book(self):
        gui = ProductGeneratorGUI.__new__(ProductGeneratorGUI)
        response = json.dumps({
            "items": [{
                "id": "book-id",
                "volumeInfo": {
                    "title": "Beispielbuch",
                    "subtitle": "Ein Test",
                    "authors": ["Erika Beispiel"],
                    "publisher": "Testverlag",
                    "publishedDate": "2025",
                    "pageCount": 240,
                    "language": "de",
                    "infoLink": "https://books.google.test/book-id",
                },
            }]
        })
        with patch.object(gui, "fetch_url", return_value=response):
            results = gui.search_google_books("9783442180653")
        self.assertEqual(results[0][0], "Beispielbuch: Ein Test")
        self.assertIn("Verlag: Testverlag", results[0][1])
        self.assertEqual(
            results[0][2], "https://books.google.test/book-id"
        )

    def test_result_quality_prefers_exact_titles_and_identifiers(self):
        self.assertEqual(
            ProductGeneratorGUI.match_quality(
                "Samsung Galaxy S23", "Samsung Galaxy S23"
            ),
            "exakt",
        )
        self.assertEqual(
            ProductGeneratorGUI.match_quality(
                "978-3442180653", "Die LET THEM Theorie"
            ),
            "exakt",
        )


if __name__ == "__main__":
    unittest.main()


class UrlAndMarkupTests(unittest.TestCase):
    """Von CodeQL gemeldete Muster: Teilzeichenketten statt Hostnamen."""

    def test_a_domain_in_the_query_string_is_not_a_host_match(self):
        matches = ProductGeneratorGUI.host_has_label
        self.assertTrue(matches("https://www.amazon.de/dp/B01", "amazon"))
        self.assertTrue(matches("https://amazon.co.uk/dp/B01", "amazon"))
        # Frueher trafen diese drei faelschlich zu.
        self.assertFalse(matches("https://fremd.test/?x=amazon.de", "amazon"))
        self.assertFalse(matches("https://amazon.de.fremd.test/x", "amazon"))
        self.assertFalse(matches("https://keinamazon.de/x", "amazon"))

    def test_exact_domains_reject_lookalikes(self):
        exact = ProductGeneratorGUI.host_is
        self.assertTrue(exact("https://d-nb.info/123", "d-nb.info"))
        self.assertFalse(exact("https://d-nb.info.fremd.test/", "d-nb.info"))
        self.assertFalse(exact("https://fremd.test/?u=d-nb.info", "d-nb.info"))

    def test_markup_is_removed_even_when_regexes_would_fail(self):
        clean = ProductGeneratorGUI.clean_html_text
        # Ein > im Attributwert beendet das Tag fuer <[^>]+> zu frueh.
        self.assertEqual(clean('<a title="a>b">Text</a>'), "Text")
        self.assertEqual(clean("<script>code()</script>Sichtbar"), "Sichtbar")
        self.assertEqual(clean("<p>Eins</p><p>Zwei</p>"), "Eins Zwei")

    def test_an_unfinished_tag_leaves_no_markup_behind(self):
        """Abgeschnittenes Markup darf kein Tag hinterlassen.

        Ob HTMLParser das Bruchstueck verwirft oder als Text ausgibt, haengt
        vom CPython-Stand ab. Festgehalten wird deshalb nur, worauf es
        ankommt: der sichtbare Text bleibt, ein Tag entsteht nicht.
        """
        result = ProductGeneratorGUI.clean_html_text("Rest <div foo=")
        self.assertIn("Rest", result)
        self.assertNotIn("<div", result)
        self.assertNotIn("<p", ProductGeneratorGUI.clean_html_text("A <p"))

    def test_source_names_follow_the_host(self):
        name = ProductGeneratorGUI.source_name
        self.assertEqual(name("https://www.amazon.de/dp/B01"), "Amazon")
        self.assertEqual(name("https://d-nb.info/123"), "DNB")
        self.assertEqual(
            name("https://suggestqueries.google.com/complete"),
            "Web-Vorschlag",
        )
        self.assertEqual(name("https://fremd.test/?x=amazon.de"), "fremd.test")



class RetiredPlatformTests(unittest.TestCase):
    """Eine entfernte Plattform darf gespeicherte Beiträge nicht lahmlegen."""

    def test_drafts_of_unknown_platforms_are_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            store = ListingStore(Path(folder) / "listings.db")
            try:
                product_id = store.upsert_product("Testprodukt", "1")
                store.save_draft(product_id, "kleinanzeigen", "Titel", "Text")
                # Entwurf einer Plattform, die es nicht mehr gibt.
                store.save_draft(product_id, "shpock", "Alt", "Alter Text")
                stored = store.load_drafts(product_id)
                self.assertIn("shpock", stored)
                bekannt = {
                    key: value for key, value in stored.items()
                    if key in PLATFORM_PROFILES
                }
                self.assertNotIn("shpock", bekannt)
                # Jede verbliebene Plattform ist auffindbar.
                for key in bekannt:
                    self.assertIn(key, PLATFORM_PROFILES)
            finally:
                store.close()


class BuybackTests(unittest.TestCase):
    """Ankaufspreise werden verlinkt, nicht abgerufen."""

    @staticmethod
    def controller(variant, typed=""):
        gui = ProductGeneratorGUI.__new__(ProductGeneratorGUI)
        gui.selected_variant = variant
        gui.search_var = SimpleNamespace(get=lambda: typed)
        return gui

    def test_the_identifier_comes_from_isbn_ean_or_input(self):
        self.assertEqual(
            self.controller({"isbn": "978-3-442-31810-6"}).product_identifier(),
            "9783442318106",
        )
        self.assertEqual(
            self.controller({"ean": "4250199300182"}).product_identifier(),
            "4250199300182",
        )
        # Ohne Kennung am Produkt zaehlt die Eingabe, sofern sie eine ist.
        self.assertEqual(
            self.controller({}, "9783442318106").product_identifier(),
            "9783442318106",
        )
        self.assertEqual(
            self.controller({}, "Fantec QB-X2US3R").product_identifier(), ""
        )

    def test_no_service_is_contacted(self):
        """Der Quelltext darf die Dienste nur oeffnen, nie abrufen."""
        source = inspect.getsource(ProductGeneratorGUI.open_buyback_service)
        self.assertIn("webbrowser.open", source)
        for forbidden in ("urlopen", "fetch_url", "fetch_binary", "Request("):
            self.assertNotIn(forbidden, source)

    def test_service_templates_are_well_formed(self):
        for name, template, needs in BUYBACK_SERVICES:
            self.assertTrue(name)
            self.assertTrue(template.startswith("https://"), template)
            self.assertIn(needs, ("identifier", "query", "none"))
            # Vorlagen mit Platzhalter muessen ihn genau einmal fuehren.
            self.assertEqual(
                template.count("{value}"), 0 if needs == "none" else 1
            )

    def test_the_identifier_search_uses_the_verified_pattern(self):
        gui = self.controller({"isbn": "978-3-442-31810-6"})
        gui.language = "de"
        gui.status_var = SimpleNamespace(set=lambda text: None)
        template = dict(
            (name, tpl) for name, tpl, _ in BUYBACK_SERVICES
        )["momox"]
        geoeffnet = []
        with patch("product_generator_gui.webbrowser.open", geoeffnet.append):
            gui.open_buyback_service(template, "identifier", "momox")
        self.assertEqual(
            geoeffnet, ["https://www.momox.de/offer/9783442318106"]
        )

    def test_the_text_search_falls_back_to_the_product_name(self):
        gui = self.controller({"name": "XBox One"})
        gui.language = "de"
        gui.status_var = SimpleNamespace(set=lambda text: None)
        template = dict(
            (name, tpl) for name, tpl, _ in BUYBACK_SERVICES
        )["medimops"]
        geoeffnet = []
        with patch("product_generator_gui.webbrowser.open", geoeffnet.append):
            gui.open_buyback_service(template, "query", "medimops")
        self.assertEqual(geoeffnet, [
            "https://www.medimops.de/produkte-C0/"
            "?fcIsSearch=1&searchparam=XBox+One"
        ])


class BuybackUrlTests(unittest.TestCase):
    """Die belegten Adressmuster der drei Ankaufsdienste."""

    @staticmethod
    def gui(variant):
        gui = ProductGeneratorGUI.__new__(ProductGeneratorGUI)
        gui.selected_variant = variant
        gui.search_var = SimpleNamespace(get=lambda: "")
        gui.language = "de"
        gui.status_var = SimpleNamespace(set=lambda text: None)
        return gui

    def open_all(self, variant):
        gui = self.gui(variant)
        adressen = {}
        with patch("product_generator_gui.webbrowser.open") as opener:
            for name, template, needs in BUYBACK_SERVICES:
                if needs == "identifier" and not gui.product_identifier():
                    continue
                opener.reset_mock()
                gui.open_buyback_service(template, needs, name)
                adressen[name] = opener.call_args[0][0]
        return adressen

    def test_every_service_gets_its_own_pattern(self):
        adressen = self.open_all({"name": "XBox One"})
        self.assertEqual(
            adressen["medimops"],
            "https://www.medimops.de/produkte-C0/"
            "?fcIsSearch=1&searchparam=XBox+One",
        )
        # rebuy: Verkaufssuche, nicht Kaufsuche - gefragt ist der Ankaufspreis.
        self.assertEqual(
            adressen["rebuy"],
            "https://rebuy.de/verkaufen/suche?query=XBox+One",
        )
        self.assertNotIn("momox", adressen)   # ohne Kennung nicht aufrufbar

    def test_identifiers_reach_momox_directly(self):
        adressen = self.open_all({"name": "Buch", "isbn": "9781788992664"})
        self.assertEqual(
            adressen["momox"], "https://www.momox.de/offer/9781788992664"
        )

    def test_spaces_are_encoded_for_query_strings(self):
        """In einer Query bedeuten + und %20 beide ein Leerzeichen."""
        adressen = self.open_all({"name": "Google Pixel 9 Pro"})
        for name, url in adressen.items():
            self.assertNotIn(" ", url, name)
            self.assertIn("Google+Pixel+9+Pro", url, name)


class LegalClauseMigrationTests(unittest.TestCase):
    """Eine unveränderte alte Vorgabe wird gehoben, eigener Text nicht."""

    def test_an_untouched_previous_default_is_updated(self):
        for veraltet in SUPERSEDED_CLAUSES:
            self.assertEqual(
                ProductGeneratorGUI.current_legal_clause(veraltet),
                WARRANTY_CLAUSE,
            )

    def test_a_self_written_clause_is_kept(self):
        eigen = "Verkauf wie besichtigt, Abholung nach Absprache."
        self.assertEqual(
            ProductGeneratorGUI.current_legal_clause(eigen), eigen
        )

    def test_an_empty_or_missing_clause_falls_back(self):
        for leer in ("", "   ", None):
            self.assertEqual(
                ProductGeneratorGUI.current_legal_clause(leer),
                WARRANTY_CLAUSE,
            )

    def test_the_default_carries_the_mandatory_carve_outs(self):
        """Ausschluesse, die das Gesetz ohnehin nicht zulaesst, benennen.

        Arglistiges Verschweigen ist zwar vorsaetzliches Handeln und damit
        bereits erfasst; es wird trotzdem ausdruecklich genannt, weil ein
        vielfach verwendeter Textbaustein als vorformulierte Bedingung gelten
        kann und dann die Transparenz zaehlt.
        """
        self.assertIn("arglistig verschwiegener Mängel", WARRANTY_CLAUSE)
        self.assertIn("Vorsatz", WARRANTY_CLAUSE)
        self.assertIn("grobe Fahrlässigkeit", WARRANTY_CLAUSE)
        self.assertIn("Leben, Körper oder Gesundheit", WARRANTY_CLAUSE)


class ProductLinkTests(unittest.TestCase):
    """Ein Produktlink soll die verlinkte Seite lesen, nicht danach suchen."""

    def test_both_geizhals_url_forms_count_as_product_pages(self):
        gui = ProductGeneratorGUI.__new__(ProductGeneratorGUI)
        seiten = {
            "https://geizhals.de/fantec-qb-x2us3r-schwarz-1826-a1471139.html":
                "neue Form mit -a",
            "https://geizhals.de/google-pixel-10-pro-xl-v209142.html":
                "alte Form mit -v",
        }
        for url, form in seiten.items():
            with patch.object(
                gui, 'fetch_url', return_value="<html></html>"
            ) as holen, patch.object(
                gui, 'extract_comparison_product',
                return_value=("Titel", "Daten"),
            ):
                ergebnis = ProductGeneratorGUI._search_geizhals_once(gui, url)
            # Die Seite selbst wurde geholt, nicht eine Trefferliste.
            holen.assert_called_once_with(url)
            self.assertEqual(ergebnis, [("Titel", "Daten", url)], form)

    def test_a_slug_without_a_model_number_stops_at_generic_words(self):
        model = ProductGeneratorGUI.model_query_from_slug
        self.assertEqual(
            model(
                "https://amazon.de/Google-Pixel-Android-Smartphone-Dreifach-"
                "R%C3%BCckkamerasystem-Actua-Display/dp/B0D7TZ5YDV"
            ),
            "Google Pixel",
        )
        self.assertEqual(
            model("https://www.amazon.de/Bose-QuietComfort-Kopfhoerer/dp/Y"),
            "Bose QuietComfort",
        )
        # Eine Modellnummer schlaegt die Wortliste weiterhin.
        self.assertEqual(
            model("https://www.amazon.de/Samsung-Galaxy-S23-Smartphone/dp/X"),
            "Samsung-Galaxy-S23",
        )

    def test_a_substituted_search_is_disclosed(self):
        gui = ProductGeneratorGUI.__new__(ProductGeneratorGUI)
        gui.language = "de"
        treffer = ("Google Pixel 10", "Daten", "https://geizhals.de/x-v1.html")
        with patch.object(
            gui, 'search_amazon', side_effect=RuntimeError("blockiert")
        ), patch.object(
            gui, 'search_geizhals', return_value=[treffer]
        ), patch.object(gui, 'search_idealo', return_value=[]), \
                patch.object(gui, 'search_wikipedia', return_value=[]):
            ProductGeneratorGUI.search_amazon_url_with_fallback(
                gui, "https://amazon.de/Google-Pixel-Android/dp/B0D7TZ5YDV"
            )
        hinweis = gui._link_fallback_note
        # Grund und Ersatzbegriff muessen benannt sein.
        self.assertIn("blockiert", hinweis)
        self.assertIn("Google Pixel", hinweis)


class EditorBasicsTests(unittest.TestCase):
    """Rückgängig, Tastenkürzel und gemerkte Fenstergröße."""

    @unittest.skipUnless(DISPLAY_AVAILABLE, "kein Display verfügbar")
    def test_undo_restores_typing_but_not_a_platform_switch(self):
        root = tk.Tk()
        try:
            root.withdraw()
            editor = tk.Text(root, undo=True, maxundo=-1)
            stub = SimpleNamespace(preview_text=editor)
            ProductGeneratorGUI.replace_preview_text(stub, "Erste Fassung")
            editor.insert(tk.END, " ergänzt")
            editor.edit_undo()
            self.assertEqual(
                editor.get("1.0", tk.END).strip(), "Erste Fassung"
            )
            # Ein programmgesteuerter Wechsel darf nicht rueckgaengig zu
            # machen sein - sonst erschiene der Entwurf der vorigen Plattform.
            ProductGeneratorGUI.replace_preview_text(stub, "Andere Plattform")
            with self.assertRaises(tk.TclError):
                editor.edit_undo()
        finally:
            root.destroy()

    @unittest.skipUnless(DISPLAY_AVAILABLE, "kein Display verfügbar")
    def test_offscreen_geometries_are_rejected(self):
        root = tk.Tk()
        try:
            root.withdraw()
            gueltig = TabbedProductGeneratorGUI.geometry_is_on_screen
            self.assertTrue(gueltig(root, "1400x950+100+50"))
            # Der Rand eines maximierten Fensters unter Windows.
            self.assertTrue(gueltig(root, "1200x900+-8+-8"))
            # Ein abgezogener zweiter Bildschirm darf das Fenster nicht
            # unerreichbar machen.
            self.assertFalse(gueltig(root, "1200x900+99999+99999"))
            self.assertFalse(gueltig(root, "100x80+0+0"))
            self.assertFalse(gueltig(root, ""))
            self.assertFalse(gueltig(root, "kaputt"))
        finally:
            root.destroy()


class ListingManagerTests(unittest.TestCase):
    """Gespeicherte Beiträge müssen auffindbar und verwaltbar sein."""

    def store(self, folder):
        store = ListingStore(Path(folder) / "listings.db")
        erster = store.upsert_product("Fantec QB-X2US3R", "4250199300182")
        store.save_draft(erster, "kleinanzeigen", "Titel", "Text")
        store.save_draft(erster, "ebay", "Titel", "Text")
        store.add_fact(erster, "Farbe", "schwarz", "Geizhals")
        zweiter = store.upsert_product("Marantz M-CR612", "")
        return store, erster, zweiter

    def test_listings_are_reported_with_their_scope(self):
        with tempfile.TemporaryDirectory() as folder:
            store, erster, _ = self.store(folder)
            try:
                eintraege = {e['id']: e for e in store.products()}
                self.assertEqual(len(eintraege), 2)
                self.assertEqual(eintraege[erster]['draft_count'], 2)
                self.assertEqual(eintraege[erster]['fact_count'], 1)
                self.assertEqual(eintraege[erster]['own_image_count'], 0)
                # Zuletzt geaenderter Beitrag zuerst. Der Zeitstempel hat
                # Sekundengenauigkeit, deshalb wird er ausdruecklich gesetzt
                # statt auf die Ausfuehrungsdauer zu bauen.
                store.connection.execute(
                    "UPDATE products SET updated_at=? WHERE id=?",
                    (2_000_000_000, erster),
                )
                store.connection.commit()
                self.assertEqual(
                    [e['name'] for e in store.products()][0],
                    "Fantec QB-X2US3R",
                )
            finally:
                store.close()

    def test_renaming_keeps_the_identifier(self):
        with tempfile.TemporaryDirectory() as folder:
            store, erster, _ = self.store(folder)
            try:
                self.assertTrue(store.rename_product(erster, " Neuer Name "))
                eintrag = next(
                    e for e in store.products() if e['id'] == erster
                )
                self.assertEqual(eintrag['name'], "Neuer Name")
                self.assertEqual(eintrag['identifier'], "4250199300182")
                # Ein leerer Name darf den Beitrag nicht namenlos machen.
                self.assertFalse(store.rename_product(erster, "   "))
                self.assertEqual(
                    next(e for e in store.products()
                         if e['id'] == erster)['name'],
                    "Neuer Name",
                )
            finally:
                store.close()

    def test_deleting_removes_everything_that_belongs_to_it(self):
        with tempfile.TemporaryDirectory() as folder:
            store, erster, zweiter = self.store(folder)
            try:
                store.save_draft(erster, "kleinanzeigen", "Neu", "Anders")
                store.delete_product(erster)
                self.assertEqual(
                    [e['id'] for e in store.products()], [zweiter]
                )
                for tabelle in ('facts', 'drafts', 'draft_versions'):
                    verbleibend = store.connection.execute(
                        f"SELECT COUNT(*) FROM {tabelle} WHERE product_id=?",
                        (erster,),
                    ).fetchone()[0]
                    self.assertEqual(verbleibend, 0, tabelle)
            finally:
                store.close()


class SnippetTests(unittest.TestCase):
    """Textbausteine für wiederkehrende Angaben."""

    def test_broken_entries_fall_back_to_the_defaults(self):
        normalize = ProductGeneratorGUI.normalize_snippets
        vorgabe = [s['name'] for s in normalize(None, 'de')]
        self.assertEqual(vorgabe[0], "Versand")
        # Unbrauchbare Eintraege duerfen nicht als Baustein durchgehen.
        self.assertEqual(
            [s['name'] for s in normalize(
                [{'name': '', 'text': 'x'}, {'kaputt': 1}, 'text'], 'de'
            )],
            vorgabe,
        )
        # Ein einziger gueltiger Eintrag ersetzt die Vorgabe vollstaendig.
        eigene = normalize(
            [{'name': ' Eigener ', 'text': ' Mein Text '}], 'de'
        )
        self.assertEqual(eigene, [{'name': 'Eigener', 'text': 'Mein Text'}])

    def test_defaults_follow_the_interface_language(self):
        normalize = ProductGeneratorGUI.normalize_snippets
        self.assertEqual(normalize(None, 'en')[0]['name'], "Shipping")
        # Unbekannte Sprache faellt auf Deutsch zurueck.
        self.assertEqual(normalize(None, 'fr')[0]['name'], "Versand")

    @unittest.skipUnless(DISPLAY_AVAILABLE, "kein Display verfügbar")
    def test_a_snippet_becomes_its_own_paragraph(self):
        root = tk.Tk()
        try:
            root.withdraw()
            editor = tk.Text(root, undo=True)
            stub = SimpleNamespace(
                preview_text=editor,
                render_live_preview=lambda: None,
                update_listing_counters=lambda: None,
            )
            editor.insert('1.0', "Erster Absatz.")
            editor.mark_set(tk.INSERT, tk.END)
            ProductGeneratorGUI.insert_snippet(stub, "Versand als Paket.")
            inhalt = editor.get('1.0', tk.END).strip()
            # Leerzeile dazwischen, damit nichts mitten im Satz landet.
            self.assertEqual(
                inhalt, "Erster Absatz.\n\nVersand als Paket."
            )
            # Ein leerer Baustein aendert nichts.
            ProductGeneratorGUI.insert_snippet(stub, "")
            self.assertEqual(editor.get('1.0', tk.END).strip(), inhalt)
        finally:
            root.destroy()
