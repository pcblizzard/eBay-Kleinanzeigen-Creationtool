import tempfile
import unittest
import unicodedata
from unittest.mock import Mock, patch
from pathlib import Path

from product_generator_gui import (
    ProductGenerator,
    ProductGeneratorGUI,
    WARRANTY_CLAUSE,
)


class ProductGeneratorTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.generator = ProductGenerator(
            products_file="products.json", output_dir=self.temp_dir.name
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_required_clause_is_always_german_and_at_the_end(self):
        variant = {
            "name": "Testprodukt",
            "description": {"de": "Deutsch", "en": "English"},
        }
        listing = self.generator.generate_listing(
            variant, "en", description_override="Reviewed description"
        )
        self.assertIn("Reviewed description", listing)
        self.assertTrue(listing.rstrip().endswith(WARRANTY_CLAUSE))

    def test_source_url_is_included(self):
        variant = {
            "name": "Testprodukt",
            "description": "Beschreibung",
            "source_url": "https://example.test/product",
        }
        listing = self.generator.generate_listing(variant)
        self.assertIn("Quelle: https://example.test/product", listing)

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
        self.assertIn("[sehr gutem / gutem / gebrauchtem]", draft)
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
        self.assertNotIn("[Weiteres Zubehör]", draft)

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
        self.assertTrue(all("suggestqueries.google.com" in item[2] for item in results))

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


if __name__ == "__main__":
    unittest.main()
