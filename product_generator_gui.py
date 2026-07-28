#!/usr/bin/env python3
"""
eBay Kleinanzeigen Produktdatei Generator
Erstellt automatische Produktbeschreibungen mit Gewährleistungsklausel
GUI-Version mit Dateiauswahl
"""

import json
import io
import hashlib
import locale
import os
import secrets
import shutil
import sys
import base64
from pathlib import Path
from datetime import datetime
import difflib
import copy
import html as html_lib
from html.parser import HTMLParser
import ipaddress
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, filedialog, messagebox
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
import webbrowser
import urllib.error
import xml.etree.ElementTree as ET

from listing_store import (
    COMPACT_DESCRIPTION_LIMIT,
    ListingStore,
    PLATFORM_PROFILES,
    safe_filename,
)
from ebay_listing import (
    DEFAULT_LOCATION_KEY,
    EbayError,
    EbayListingClient,
    ListingDraft,
    authorization_code,
    condition_code,
    consent_url,
    sku_for,
)

try:
    from PIL import Image, ImageOps, ImageTk
except ImportError:
    Image = None
    ImageOps = None
    ImageTk = None

# Konstante für die Gewährleistungsklausel
WARRANTY_CLAUSE = """Privatverkauf, keine Gewährleistung, keine Garantie, keine Rücknahme.
Verkauf von privat unter Ausschluss der Sachmängelhaftung. Die Haftung für Vorsatz, grobe Fahrlässigkeit sowie für Schäden aus Verletzung von Leben, Körper oder Gesundheit bleibt unberührt."""

# Frühere Vorgaben des Pflichttextes. Wer ihn nie geändert hat, trägt die
# damalige Fassung in seiner Konfiguration und bekäme sonst die Verbesserung
# nie zu sehen. Nur exakte Übereinstimmungen werden gehoben – ein selbst
# geschriebener Text bleibt unangetastet.
SUPERSEDED_CLAUSES = (
    "Privatverkauf. Die Ware wird unter Ausschluss der Sachmängelhaftung "
    "nach § 475 BGB verkauft. Ausgeschlossen ist jede Gewährleistung für "
    "Sachmängel. Die Haftung für arglistig verschwiegene Mängel sowie für "
    "Schäden aus der Verletzung von Leben, Körper oder Gesundheit bleibt "
    "unberührt.",
    "Privatverkauf, keine Gewährleistung, keine Rücknahme.\n"
    "Verkauf von privat unter Ausschluss der Sachmängelhaftung. Die Haftung "
    "für Vorsatz, grobe Fahrlässigkeit sowie für Schäden aus Verletzung von "
    "Leben, Körper oder Gesundheit bleibt unberührt. Keine Rücknahme, keine "
    "Garantie.",
)

MODULE_DIR = Path(__file__).resolve().parent
APPLICATION_NAME = "eBay-Kleinanzeigen-Creationtool"

# Eigene Fotos: Grenzen der Plattformen und Vorgaben fuer die Aufbereitung.
OWN_IMAGE_SUFFIXES = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tif',
                      '.tiff', '.heic', '.heif')
PLATFORM_IMAGE_LIMITS = {
    'kleinanzeigen': 20,
    'ebay': 24,
    'ebay_detailed': 24,
    'ebay_mobile': 24,
}
# Ankaufsdienste nennen, was sie fuer ein Buch oder Medium zahlen wuerden -
# eine Untergrenze fuer die eigene Preisfindung. Es gibt dafuer keine
# offiziellen Schnittstellen, deshalb wird nur die Seite geoeffnet und die
# Kennung bereitgelegt; abgerufen wird nichts.
#
# Bewusst die Einstiegsseiten statt geratener Suchadressen: momox und medimops
# beantworten automatisierte Abrufe mit HTTP 403, ihre Parameter liessen sich
# nicht pruefen. Ein direkter Suchlink kann ergaenzt werden, sobald das Muster
# belegt ist.
# (Name, Adressvorlage, benoetigte Angabe)
#   identifier – nur mit ISBN oder EAN aufrufbar
#   query      – Freitext; nimmt die Kennung, sonst den Produktnamen
#   none       – Einstiegsseite, Muster nicht belegt
BUYBACK_SERVICES = (
    ('momox', 'https://www.momox.de/offer/{value}', 'identifier'),
    (
        'medimops',
        'https://www.medimops.de/produkte-C0/?fcIsSearch=1&searchparam={value}',
        'query',
    ),
    # Bewusst die Verkaufssuche: gefragt ist, was rebuy zahlt, nicht was ein
    # Kauf dort kostet. Sie hat einen eigenen Pfad und einen eigenen
    # Parameternamen - /verkaufen/suche?query= statt /kaufen/suchen?q=.
    ('rebuy', 'https://rebuy.de/verkaufen/suche?query={value}', 'query'),
)

OWN_IMAGE_MAX_EDGE = 2000
OWN_IMAGE_MAX_BYTES = 12 * 1024 * 1024
OWN_IMAGE_QUALITY = 88


class _TextExtractor(HTMLParser):
    """Gewinnt den sichtbaren Text aus fremdem Markup.

    Inhalte von ``script`` und ``style`` werden verworfen; Zeichenverweise löst
    der Parser selbst auf. Gegenüber ``re.sub(r'<[^>]+>', …)`` ist das robust
    gegen Anführungszeichen in Attributen und unvollständige Tags.
    """

    SKIPPED = frozenset({'script', 'style', 'template'})

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._parts = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIPPED:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in self.SKIPPED and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if not self._skip_depth:
            self._parts.append(data)

    @classmethod
    def text_of(cls, value):
        parser = cls()
        try:
            parser.feed(str(value or ''))
            parser.close()
        except Exception:
            # Unbrauchbares Markup liefert lieber den Rohtext als nichts.
            return str(value or '')
        return ' '.join(parser._parts)


def restrict_to_owner(path):
    """Beschränkt eine Datei auf den Eigentümer.

    Konfiguration, Sitzung und Sicherheitsprotokoll enthalten zwar keine
    Zugangsdaten, aber persönliche Angaben wie Postleitzahl und Entwürfe.
    Ohne diesen Schritt entstehen sie unter Linux und macOS je nach umask
    für alle lesbar.
    """
    try:
        os.chmod(path, 0o600)
    except OSError:
        # Unter Windows regeln das die Zugriffslisten des Benutzerprofils.
        pass


def user_data_dir():
    """Benutzerbezogenes Datenverzeichnis der Anwendung."""
    root = os.environ.get('LOCALAPPDATA') or (Path.home() / '.local' / 'share')
    return Path(root) / APPLICATION_NAME


def prepare_own_image(source_path, target_path, max_edge=OWN_IMAGE_MAX_EDGE):
    """Bereitet ein eigenes Foto fuer den Upload auf.

    Entfernt saemtliche EXIF-Daten, dreht das Bild nach seiner
    Orientierungsangabe und verkleinert es auf eine vertretbare Kantenlaenge.

    Das Entfernen der EXIF-Daten ist der wichtigste Schritt: Handyfotos
    enthalten GPS-Koordinaten. Wer den Artikel zu Hause fotografiert,
    veroeffentlicht sonst mit dem Bild seine Wohnadresse.
    """
    if Image is None:
        # Ohne Pillow wird unveraendert kopiert; der Aufrufer warnt davor.
        shutil.copy2(source_path, target_path)
        return False
    with Image.open(source_path) as opened:
        image = ImageOps.exif_transpose(opened)
        target = Path(target_path)
        if target.suffix.lower() in ('.jpg', '.jpeg') and image.mode not in (
            'RGB', 'L'
        ):
            image = image.convert('RGB')
        if max_edge and max(image.size) > max_edge:
            image.thumbnail((max_edge, max_edge), Image.LANCZOS)
        # Ein frisches Bild traegt kein info-Dictionary und damit keine
        # Metadaten; paste kopiert nur die Bildpunkte.
        clean = Image.new(image.mode, image.size)
        clean.paste(image)
        if target.suffix.lower() in ('.jpg', '.jpeg'):
            clean.save(target, quality=OWN_IMAGE_QUALITY, optimize=True)
        else:
            clean.save(target)
    return True


def default_products_file():
    """Findet die Produktdatenbank auch im installierten Konsolenskript.

    Gesucht wird neben dem Modul, in den mitinstallierten Paketdaten und
    zuletzt im benutzerbezogenen Datenverzeichnis.
    """
    candidates = (
        MODULE_DIR / "products.json",
        Path(sys.prefix) / "share" / "ebay-creationtool" / "products.json",
        user_data_dir() / "products.json",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


def default_output_dir():
    """Standard-Ausgabeordner; im installierten Paket unterhalb des Nutzers."""
    project_output = MODULE_DIR / "product_listings"
    if os.access(MODULE_DIR, os.W_OK):
        return project_output
    return user_data_dir() / "product_listings"


SECRET_SERVICE = "eBay-Kleinanzeigen-Creationtool"
SECRET_PLACEHOLDER = "****************"
SESSION_AUTOSAVE_MS = 15 * 1000
RETIRED_TAB_GRACE_MS = 60 * 1000
MAX_TEXT_RESPONSE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_RESPONSE_BYTES = 15 * 1024 * 1024

TRANSLATIONS = {
    "de": {
        "title": "eBay Kleinanzeigen - Produktbeschreibungs-Generator",
        "search_label": "Produktname, ISBN oder EAN/GTIN eingeben:",
        "search_frame": "1. Produktsuche",
        "variant_frame": "2. Variante auswählen",
        "preview_frame": "3. Beschreibung prüfen und bearbeiten",
        "editor_label": "Bearbeiten",
        "live_preview_label": "Live-Vorschau",
        "no_product_image": "Kein Produktbild verfügbar",
        "previous_image": "◀",
        "next_image": "▶",
        "save_image": "Bild speichern…",
        "own_images_frame": "Eigene Fotos",
        "add_own_images": "Hinzufügen…",
        "own_images_title": "Eigene Produktfotos auswählen",
        "own_images_count": "{count} von {limit} Fotos ({platform})",
        "own_images_limit": "Grenze erreicht: {platform} erlaubt {limit} Fotos.",
        "own_images_hint": (
            "Beim Export werden Standortdaten (GPS) entfernt, die Drehung "
            "korrigiert und die Größe angepasst. Der Upload erfolgt weiterhin "
            "von Hand auf der jeweiligen Plattform."
        ),
        "own_images_no_pillow": (
            "Pillow fehlt: Fotos werden unverändert kopiert, "
            "einschließlich ihrer Standortdaten."
        ),
        "own_images_replace_note": (
            "Eigene Fotos ersetzen im Export die Herstellerbilder."
        ),
        "ebay_publish": "🛒 Bei eBay einstellen…",
        "ebay_publish_title": "Angebot bei eBay einstellen",
        "ebay_consent_frame": "1. Zugriff auf dein eBay-Konto",
        "ebay_consent_missing": "Noch keine Einwilligung erteilt.",
        "ebay_consent_present": "Einwilligung liegt vor.",
        "ebay_consent_start": "Zugriff im Browser erteilen…",
        "ebay_consent_paste": "Adresse nach der Zustimmung hier einfügen:",
        "ebay_consent_save": "Einwilligung speichern",
        "ebay_consent_saved": "Einwilligung gespeichert.",
        "ebay_consent_revoke": "Einwilligung löschen",
        "ebay_consent_revoked": "Einwilligung gelöscht.",
        "ebay_consent_revoke_confirm": (
            "Die gespeicherte eBay-Anmeldung wirklich löschen?\n\n"
            "Client-ID und Client-Secret bleiben erhalten; für ein neues "
            "Angebot muss der Zugriff erneut erteilt werden.\n\n"
            "Der Zugriff lässt sich zusätzlich im eBay-Konto unter "
            "„Kontoeinstellungen → Anwendungen“ dauerhaft widerrufen."
        ),
        "ebay_consent_hint": (
            "Der Browser öffnet die eBay-Seite. Nach dem Zustimmen leitet eBay "
            "auf deine RuName-Adresse weiter — kopiere die vollständige Adresse "
            "aus der Adresszeile hierher."
        ),
        "ebay_runame_label": "RuName (Redirect-URL-Name):",
        "ebay_policies_frame": "2. Richtlinien und Standort",
        "ebay_policy_fulfillment": "Versand:",
        "ebay_policy_payment": "Zahlung:",
        "ebay_policy_return": "Rücknahme:",
        "ebay_policies_load": "Richtlinien laden",
        "ebay_postal_code": "PLZ:",
        "ebay_country": "Land:",
        "ebay_offer_frame": "3. Angebot",
        "ebay_quantity": "Menge:",
        "ebay_check": "Angaben prüfen",
        "ebay_publish_action": "Angebot anlegen und veröffentlichen",
        "ebay_publish_missing": "Es fehlen noch:",
        "dialog_close": "Schließen",
        "ebay_ready": "Alle Pflichtangaben vorhanden.",
        "ebay_confirm": (
            "Damit entsteht ein öffentliches, kostenpflichtiges eBay-Angebot "
            "unter deinem Konto.\n\nTitel: {title}\nPreis: {price} EUR\n"
            "Kategorie: {category}\nFotos: {images}\nUmgebung: {environment}"
            "\n\nJetzt wirklich veröffentlichen?"
        ),
        "ebay_working": "Wird an eBay übertragen…",
        "ebay_published": "Angebot veröffentlicht. Angebotsnummer:",
        "ebay_no_credentials": (
            "Client-ID, Client-Secret und RuName werden benötigt. "
            "Client-ID und Secret unter Einstellungen → Marktplatz-APIs."
        ),
        "ebay_no_category": (
            "Es ist noch keine eBay-Kategorie gewählt. Wähle sie im "
            "eBay-Prüfbereich aus."
        ),
        "save_image_title": "Produktbild speichern",
        "image_saved": "Produktbild gespeichert:",
        "legal_frame": "Fester Hinweis (wird immer angehängt)",
        "options_frame": "Einstellungen",
        "path_label": "Speicherpfad:",
        "change_path": "📁 Speicherpfad ändern",
        "save_button": "💾 Speichern",
        "close_button": "❌ Beenden",
        "new_tab": "＋ Neuer Beitrag",
        "close_tab": "✕ Tab schließen",
        "tab_default": "Beitrag",
        "language_label": "Sprache:",
        "default_path_button": "Standard-Speicherort wählen",
        "status_ready": "Bereit",
        "no_selection": "Bitte wählen Sie zuerst eine Produktvariante aus!",
        "saved_success": "Datei erfolgreich gespeichert:",
        "font_size_label": "Schriftgröße:",
        "menu_settings": "Einstellungen",
        "menu_file": "Datei",
        "menu_new": "Neu",
        "menu_open": "Öffnen…",
        "menu_save": "Speichern",
        "menu_exit": "Beenden",
        "open_file_title": "Vorhandenen Verkaufsbeitrag öffnen",
        "menu_open_settings": "Einstellungen öffnen…",
        "menu_change_save_path": "Speicherpfad ändern",
        "menu_default_save_path": "Standard-Speicherort wählen",
        "save_error": "Fehler beim Speichern:",
        "products_not_found": "Keine Produkte gefunden für:",
        "no_search_term": "Bitte Produktnamen eingeben",
        "variants_found": "Variante(n) gefunden",
        "selected_variant": "Variante ausgewählt:",
        "online_results": "Online-Ergebnisse gefunden",
        "default_save_path_description": "Standard-Speicherort auswählen:",
        "online_searching": "Suche online...",
        "details_loading": "Produktdetails werden geladen...",
        "details_unavailable": "Keine technischen Produktdetails abrufbar",
        "online_search_failed": "Online-Suche fehlgeschlagen",
        "provider_frame": "Online-Quellen (experimentell)",
        "provider_web_suggestions": "Globale Web-Vorschläge",
        "provider_wikipedia": "Wikipedia-Livesuche",
        "provider_amazon": "Amazon.de",
        "provider_geizhals": "Geizhals",
        "provider_idealo": "Idealo",
        "provider_ebay": "eBay.de (offizielle API)",
        "provider_kleinanzeigen_agent": "Kleinanzeigen Agent",
        "marketplace_api_frame": "Marktplatz-APIs",
        "kleinanzeigen_api_key": "Kleinanzeigen-Agent API-Key:",
        "ebay_client_id": "eBay Client-ID (App-ID):",
        "ebay_client_secret": "eBay Client-Secret (Cert-ID):",
        "secret_hint": "Leer lassen, um bereits gespeicherte Zugangsdaten beizubehalten.",
        "secret_store_error": "Der sichere Betriebssystem-Schlüsselspeicher ist nicht verfügbar.",
        "secret_saved_status": "Gespeichert",
        "secret_missing_status": "Nicht eingerichtet",
        "secret_test": "Verbindung testen",
        "secret_delete": "Zugangsdaten löschen",
        "secret_delete_confirm": "Gespeicherte Zugangsdaten wirklich löschen?",
        "secret_test_success": "Verbindung erfolgreich getestet.",
        "secret_test_failed": "Verbindungstest fehlgeschlagen:",
        "ebay_environment": "eBay-Umgebung:",
        "ebay_production": "Production (echte Daten)",
        "ebay_sandbox": "Sandbox (Testdaten)",
        "session_frame": "Datenschutz und Sitzung",
        "session_restore": "Offene Tabs und Entwürfe wiederherstellen",
        "session_clear_on_exit": "Sitzungsdatei beim Beenden löschen",
        "default_save_path_notice": "Standardpfad gespeichert",
        "config_load_error": "Fehler beim Laden der Konfiguration",
        "config_save_error": "Fehler beim Speichern der Konfiguration",
        "security_log_error": "Sicherheitsprotokoll nicht beschreibbar",
        "settings_saved": "Einstellungen gespeichert",
        "legal_edit_label": "Privatverkaufs-Hinweis:",
        "legal_reset": "Standardtext wiederherstellen",
        "legal_warning_title": "Rechtlichen Hinweis geändert",
        "legal_warning": (
            "Der Privatverkaufs-Hinweis wurde verändert. Der geänderte "
            "Wortlaut kann rechtlich unvollständig oder ungeeignet sein. "
            "Die Anwendung kann keine Rechtssicherheit garantieren.\n\n"
            "Geänderten Text trotzdem übernehmen?"
        ),
        "copy_button": "📋 Beitrag kopieren",
        "copied_success": "Beitrag in die Zwischenablage kopiert",
        "export_button": "💾 Beitrag speichern",
        "open_in_new_tab": "In neuem Tab öffnen",
        "ebay_check_frame": "eBay-Datenprüfung (noch keine Veröffentlichung)",
        "ebay_category": "Kategorie:",
        "ebay_category_loading": "Passende eBay-Kategorien werden geladen…",
        "ebay_category_unavailable": "Keine eBay-Kategorie verfügbar",
        "ebay_aspect": "Artikelmerkmal",
        "ebay_value": "Wert",
        "ebay_requirement": "Vorgabe",
        "ebay_status": "Status",
        "ebay_required": "Pflicht",
        "ebay_recommended": "Empfohlen",
        "ebay_optional": "Optional",
        "ebay_missing": "Fehlt",
        "ebay_complete": "Vorhanden",
        "ebay_apply_value": "Wert übernehmen",
        "ebay_check_ready": "Produktdaten für eBay vollständig",
        "ebay_check_incomplete": "Noch fehlende eBay-Pflichtangaben:",
        "ebay_check_hint": (
            "Kategorie auswählen; Pflichtmerkmale werden anschließend geladen."
        ),
        "ebay_sandbox_taxonomy_hint": (
            "Kategorie-Vorschläge sind in der eBay-Sandbox nur Testdaten. "
            "Für echte Vorschläge Production verwenden."
        ),
        "assistant_frame": "Inserat-Assistent",
        "platform_label": "Plattform-Entwurf:",
        "listing_title_label": "Anzeigentitel:",
        "characters": "Zeichen",
        "condition_label": "Zustand:",
        "condition_values": (
            "Bitte wählen|Neu|Wie neu|Sehr gut|Gut|Gebraucht|"
            "Defekt / Ersatzteil"
        ),
        "scope_label": "Lieferumfang:",
        "asking_price_label": "Wunschpreis (€):",
        # Bei Kleinanzeigen ueblich: Verhandlungsbasis oder Festpreis.
        "price_type_values": "VB|Festpreis|Zu verschenken",
        "section_condition": "Zustand",
        "section_scope": "Lieferumfang",
        "section_price": "Preisvorstellung",
        "price_basis_label": "Preisgrundlage:",
        "price_active": "Aktive Vergleichsangebote",
        "price_sold": "Tatsächlich verkaufte Angebote",
        "apply_assistant": "Angaben in Entwürfe übernehmen",
        "fact_conflicts": "Widersprüche prüfen",
        "buyback_check": "💶 Ankaufspreis prüfen",
        "buyback_copied": "In die Zwischenablage gelegt:",
        "buyback_opened": "Ankaufspreis geöffnet bei",
        "no_fact_conflicts": "Keine unbestätigten Datenkonflikte vorhanden.",
        "confirm_fact": "Ausgewählten Wert bestätigen",
        "completeness_ready": "Entwurf vollständig prüfbar",
        "completeness_missing": "Noch zu prüfen:",
        "export_package": "📦 Produktordner exportieren",
        "export_success": "Produktordner erfolgreich erstellt:",
        "export_images_failed": "Nicht abrufbare Produktbilder:",
        "limit_exceeded": "Zeichenlimit überschritten",
        "field_title": "Titel",
        "field_description": "Beschreibung",
        "price_active_notice": (
            "Preisempfehlung basiert auf aktiven Vergleichsangeboten, "
            "nicht auf abgeschlossenen Verkäufen."
        ),
        "database_label": "Lokale Produktdatenbank:",
    },
    "en": {
        "title": "eBay Classifieds - Product Description Generator",
        "search_label": "Enter product name, ISBN or EAN/GTIN:",
        "search_frame": "1. Product search",
        "variant_frame": "2. Select variant",
        "preview_frame": "3. Review and edit description",
        "editor_label": "Edit",
        "live_preview_label": "Live preview",
        "no_product_image": "No product image available",
        "previous_image": "◀",
        "next_image": "▶",
        "save_image": "Save image…",
        "own_images_frame": "Own photos",
        "add_own_images": "Add…",
        "own_images_title": "Select your own product photos",
        "own_images_count": "{count} of {limit} photos ({platform})",
        "own_images_limit": "Limit reached: {platform} allows {limit} photos.",
        "own_images_hint": (
            "On export, location data (GPS) is removed, rotation is corrected "
            "and the size is adjusted. Uploading still happens manually on "
            "the platform itself."
        ),
        "own_images_no_pillow": (
            "Pillow is missing: photos are copied unchanged, "
            "including their location data."
        ),
        "own_images_replace_note": (
            "Own photos replace the manufacturer images in the export."
        ),
        "ebay_publish": "🛒 List on eBay…",
        "ebay_publish_title": "List the offer on eBay",
        "ebay_consent_frame": "1. Access to your eBay account",
        "ebay_consent_missing": "No consent granted yet.",
        "ebay_consent_present": "Consent is in place.",
        "ebay_consent_start": "Grant access in the browser…",
        "ebay_consent_paste": "Paste the address after consenting:",
        "ebay_consent_save": "Save consent",
        "ebay_consent_saved": "Consent saved.",
        "ebay_consent_revoke": "Delete consent",
        "ebay_consent_revoked": "Consent deleted.",
        "ebay_consent_revoke_confirm": (
            "Really delete the stored eBay sign-in?\n\n"
            "Client ID and client secret are kept; listing again requires "
            "granting access once more.\n\n"
            "You can also revoke access permanently in your eBay account "
            "under “Account settings → Applications”."
        ),
        "ebay_consent_hint": (
            "The browser opens the eBay page. After you consent, eBay "
            "redirects to your RuName address — copy the complete address "
            "from the address bar to here."
        ),
        "ebay_runame_label": "RuName (redirect URL name):",
        "ebay_policies_frame": "2. Policies and location",
        "ebay_policy_fulfillment": "Shipping:",
        "ebay_policy_payment": "Payment:",
        "ebay_policy_return": "Returns:",
        "ebay_policies_load": "Load policies",
        "ebay_postal_code": "Postal code:",
        "ebay_country": "Country:",
        "ebay_offer_frame": "3. Offer",
        "ebay_quantity": "Quantity:",
        "ebay_check": "Check details",
        "ebay_publish_action": "Create and publish offer",
        "ebay_publish_missing": "Still missing:",
        "dialog_close": "Close",
        "ebay_ready": "All required details are present.",
        "ebay_confirm": (
            "This creates a public eBay listing under your account that may "
            "incur fees.\n\nTitle: {title}\nPrice: {price} EUR\n"
            "Category: {category}\nPhotos: {images}\nEnvironment: {environment}"
            "\n\nReally publish now?"
        ),
        "ebay_working": "Sending to eBay…",
        "ebay_published": "Offer published. Listing number:",
        "ebay_no_credentials": (
            "Client ID, client secret and RuName are required. "
            "Client ID and secret under Settings → Marketplace APIs."
        ),
        "ebay_no_category": (
            "No eBay category selected yet. Pick one in the eBay check area."
        ),
        "save_image_title": "Save product image",
        "image_saved": "Product image saved:",
        "legal_frame": "Mandatory notice (always appended)",
        "options_frame": "Settings",
        "path_label": "Save path:",
        "change_path": "📁 Change save path",
        "save_button": "💾 Save",
        "close_button": "❌ Close",
        "new_tab": "＋ New listing",
        "close_tab": "✕ Close tab",
        "tab_default": "Listing",
        "language_label": "Language:",
        "default_path_button": "Select default save location",
        "status_ready": "Ready",
        "no_selection": "Please select a product variant first!",
        "saved_success": "File saved successfully:",
        "font_size_label": "Font size:",
        "menu_settings": "Options",
        "menu_file": "File",
        "menu_new": "New",
        "menu_open": "Open…",
        "menu_save": "Save",
        "menu_exit": "Exit",
        "open_file_title": "Open existing listing",
        "menu_open_settings": "Open settings…",
        "menu_change_save_path": "Change save path",
        "menu_default_save_path": "Select default save location",
        "save_error": "Error saving file:",
        "products_not_found": "No products found for:",
        "no_search_term": "Please enter a product name",
        "variants_found": "variant(s) found",
        "selected_variant": "Variant selected:",
        "online_results": "Online results found",
        "default_save_path_description": "Choose the default save location:",
        "online_searching": "Searching online...",
        "details_loading": "Loading product details...",
        "details_unavailable": "No technical product details available",
        "online_search_failed": "Online search failed",
        "provider_frame": "Online sources (experimental)",
        "provider_web_suggestions": "Global web suggestions",
        "provider_wikipedia": "Wikipedia live search",
        "provider_amazon": "Amazon.de",
        "provider_geizhals": "Geizhals",
        "provider_idealo": "Idealo",
        "provider_ebay": "eBay.de (official API)",
        "provider_kleinanzeigen_agent": "Kleinanzeigen Agent",
        "marketplace_api_frame": "Marketplace APIs",
        "kleinanzeigen_api_key": "Kleinanzeigen Agent API key:",
        "ebay_client_id": "eBay client ID (App ID):",
        "ebay_client_secret": "eBay client secret (Cert ID):",
        "secret_hint": "Leave blank to keep credentials that are already stored.",
        "secret_store_error": "The secure operating-system credential store is unavailable.",
        "secret_saved_status": "Stored",
        "secret_missing_status": "Not configured",
        "secret_test": "Test connection",
        "secret_delete": "Delete credentials",
        "secret_delete_confirm": "Really delete the stored credentials?",
        "secret_test_success": "Connection tested successfully.",
        "secret_test_failed": "Connection test failed:",
        "ebay_environment": "eBay environment:",
        "ebay_production": "Production (live data)",
        "ebay_sandbox": "Sandbox (test data)",
        "session_frame": "Privacy and session",
        "session_restore": "Restore open tabs and drafts",
        "session_clear_on_exit": "Delete session file when exiting",
        "default_save_path_notice": "Default path saved",
        "config_load_error": "Error loading configuration",
        "config_save_error": "Error saving configuration",
        "security_log_error": "Security log is not writable",
        "settings_saved": "Settings saved",
        "legal_edit_label": "Private-sale notice:",
        "legal_reset": "Restore default text",
        "legal_warning_title": "Legal notice changed",
        "legal_warning": (
            "The private-sale notice has been changed. The modified wording "
            "may be legally incomplete or unsuitable. The application cannot "
            "guarantee legal validity.\n\nApply the modified text anyway?"
        ),
        "copy_button": "📋 Copy listing",
        "copied_success": "Listing copied to clipboard",
        "export_button": "💾 Save listing",
        "open_in_new_tab": "Open in new tab",
        "ebay_check_frame": "eBay data check (not publishing yet)",
        "ebay_category": "Category:",
        "ebay_category_loading": "Loading matching eBay categories…",
        "ebay_category_unavailable": "No eBay category available",
        "ebay_aspect": "Item specific",
        "ebay_value": "Value",
        "ebay_requirement": "Requirement",
        "ebay_status": "Status",
        "ebay_required": "Required",
        "ebay_recommended": "Recommended",
        "ebay_optional": "Optional",
        "ebay_missing": "Missing",
        "ebay_complete": "Present",
        "ebay_apply_value": "Apply value",
        "ebay_check_ready": "Product data is complete for eBay",
        "ebay_check_incomplete": "Missing required eBay item specifics:",
        "ebay_check_hint": (
            "Select a category; required item specifics will then be loaded."
        ),
        "ebay_sandbox_taxonomy_hint": (
            "eBay Sandbox category suggestions contain test data only. "
            "Use Production for real suggestions."
        ),
        "assistant_frame": "Listing assistant",
        "platform_label": "Platform draft:",
        "listing_title_label": "Listing title:",
        "characters": "characters",
        "condition_label": "Condition:",
        "condition_values": (
            "Please select|New|Like new|Very good|Good|Used|"
            "For parts / not working"
        ),
        "scope_label": "Included items:",
        "asking_price_label": "Asking price (€):",
        "price_type_values": "Negotiable|Fixed price|Free to a good home",
        "section_condition": "Condition",
        "section_scope": "Included",
        "section_price": "Asking price",
        "price_basis_label": "Price basis:",
        "price_active": "Active comparison listings",
        "price_sold": "Actually sold listings",
        "apply_assistant": "Apply details to drafts",
        "fact_conflicts": "Review conflicts",
        "no_fact_conflicts": "There are no unconfirmed data conflicts.",
        "confirm_fact": "Confirm selected value",
        "completeness_ready": "Draft is ready for review",
        "completeness_missing": "Still to review:",
        "export_package": "📦 Export product folder",
        "export_success": "Product folder created:",
        "export_images_failed": "Product images that could not be fetched:",
        "limit_exceeded": "Character limit exceeded",
        "field_title": "Title",
        "field_description": "Description",
        "price_active_notice": (
            "The price suggestion is based on active comparison listings, "
            "not completed sales."
        ),
        "database_label": "Local product database:",
    }
}


class ProductGenerator:
    """Backend für Produktverwaltung"""
    
    def __init__(self, products_file=None, output_dir=None):
        # Absolute Vorgaben: das installierte Konsolenskript startet in einem
        # beliebigen Arbeitsverzeichnis und fände relative Pfade sonst nie.
        self.products_file = Path(products_file or default_products_file())
        self.output_dir = Path(output_dir or default_output_dir())
        self.products = []
        
        # Output-Verzeichnis erstellen
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Produkte laden
        self.load_products()
    
    def load_products(self):
        """Lädt Produkte aus JSON-Datei"""
        try:
            with open(self.products_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                self.products = data.get('products', [])
        except FileNotFoundError:
            self.products = []
        except json.JSONDecodeError:
            self.products = []
    
    def search_products(self, search_term):
        """Sucht Produkte nach Name, Beschreibung oder Gruppen-ID"""
        results = []
        search_term_lower = search_term.lower().strip()
        search_terms = [term for term in re.split(r'\s+', search_term_lower) if term]
        requested_model_terms = {
            term for term in search_terms if any(char.isdigit() for char in term)
        }
        
        for product_group in self.products:
            group_name = product_group.get('id', '').replace('_', ' ').lower()
            for variant in product_group.get('variants', []):
                variant_name = variant['name'].lower()
                identity_text = f"{variant_name} {group_name}"
                description = variant.get('description', '')
                if isinstance(description, dict):
                    description_text = ' '.join(description.values()).lower()
                else:
                    description_text = str(description).lower()

                score = 0.0
                is_match = False
                matched_terms = set()

                # Modellnummern sind Identifikatoren: S21 darf nicht allein
                # wegen "Samsung Galaxy" als S26 interpretiert werden.
                if requested_model_terms and not all(
                    term in identity_text for term in requested_model_terms
                ):
                    continue

                if search_term_lower in variant_name or search_term_lower in description_text or search_term_lower in group_name:
                    score += 2.0
                    is_match = True

                for term in search_terms:
                    if term in variant_name:
                        score += 1.0
                        matched_terms.add(term)
                    if term in description_text:
                        score += 0.5
                        matched_terms.add(term)
                    if term in group_name:
                        score += 0.8
                        matched_terms.add(term)

                if not is_match and search_terms:
                    coverage = len(matched_terms) / len(search_terms)
                    is_match = coverage >= 0.6

                if not is_match:
                    ratio = difflib.SequenceMatcher(None, search_term_lower, variant_name).ratio()
                    fuzzy_threshold = 0.45 if len(search_terms) == 1 else 0.72
                    if ratio > fuzzy_threshold:
                        score += ratio
                        is_match = True

                if is_match:
                    results.append({
                        'group_id': product_group['id'],
                        'variant': variant,
                        'score': score
                    })

        if results:
            results.sort(key=lambda x: x['score'], reverse=True)
        
        return results
    
    @staticmethod
    def build_sales_draft(product_name, raw_description, language="de"):
        """Formt gefundene Fakten zu einem prüfbaren Verkaufsbeitrag."""
        product_name = unicodedata.normalize('NFC', str(product_name))
        raw_description = unicodedata.normalize(
            'NFC', str(raw_description or '')
        ).strip()
        raw_lines = []
        for line in raw_description.splitlines():
            clean = re.sub(r'^\s*[•*\-]\s*', '', line).strip()
            if clean and clean not in raw_lines:
                raw_lines.append(clean)
        if language == 'en':
            raw_lines = [
                ProductGenerator.translate_fact_label(line)
                for line in raw_lines
            ]

        category_text = f"{product_name} {raw_description}"
        is_book = bool(re.search(
            r'\b(buch|roman|sachbuch|taschenbuch|hardcover|paperback|'
            r'gebundene(?:s|r)?|isbn|autor(?:in)?|verlag|seiten|book|novel)\b',
            category_text,
            re.IGNORECASE,
        ))
        is_physical_media = bool(re.search(
            r'\b(cd|dvd|blu[\s-]?ray|bluray|vinyl|schallplatte|'
            r'audio[\s-]?cd|hörbuch|film|serie|musik[\s-]?album)\b',
            category_text,
            re.IGNORECASE,
        )) and not bool(re.search(
            r'\b(player|laufwerk|brenner|recorder|abspielgerät)\b',
            category_text,
            re.IGNORECASE,
        ))
        is_software = bool(re.search(
            r'\b(software|spiel|game|windows|office|playstation|xbox|'
            r'nintendo|lizenz|edition)\b',
            category_text,
            re.IGNORECASE,
        ))
        is_phone = bool(re.search(
            r'\b(smartphone|galaxy|iphone|pixel|handy)\b',
            category_text,
            re.IGNORECASE,
        ))

        if language == "en":
            title_suffix = (
                "Book description and details"
                if is_book else "Media description and details"
                if is_physical_media else "Product description and technical details"
            )
            kind = (
                "book" if is_book else "disc" if is_physical_media
                else "software" if is_software
                else "smartphone" if is_phone else "product"
            )
            intro = (
                f"For sale is a {product_name}. The {kind} details found online "
                "are listed below and should be checked against the exact item."
            )
            details_heading = (
                "### Book details" if is_book else "### Media details"
                if is_physical_media else "### Technical details"
            )
            if is_book:
                contents = f"* {product_name}"
                footer = (
                    "The book is in **[new / like new / very good / good / "
                    "read]** condition.\n\n"
                    "The cover and pages show **[no / slight / visible]** signs "
                    "of use. Markings or notes are **[not present / present: ...]**.\n\n"
                    "Feel free to contact me with any questions."
                )
            elif is_physical_media:
                contents = (
                    f"* {product_name}\n"
                    "* [Original case]\n"
                    "* [Booklet / insert]\n"
                    "* [Additional discs]"
                )
                footer = (
                    "The disc and case are in **[new / like new / very good / "
                    "good / used]** condition.\n\n"
                    "Scratches are **[none / slight / visible]**. "
                    "Playback was **[tested successfully / not tested]**.\n\n"
                    "Feel free to contact me with any questions."
                )
            else:
                contents = (
                    f"* {product_name}\n"
                    "* [Original packaging / data carrier]\n"
                    "* [Cable, power supply or other accessories]"
                )
                footer = (
                    "The item is in **[new / like new / very good / good / "
                    "used]** condition and **[is sealed in its original "
                    "packaging / works perfectly / has the following "
                    "defects: ...]**.\n\n"
                    "Signs of use are **[none / slight / clearly visible]**.\n\n"
                    "Feel free to contact me with any questions."
                )
            contents_heading = "### Included"
            review_hint = "*(Please remove or complete anything that does not apply.)*"
        else:
            title_suffix = (
                "Buchbeschreibung und Details"
                if is_book else "Medienbeschreibung und Details"
                if is_physical_media else "Produktbeschreibung und technische Details"
            )
            kind = (
                "Buch" if is_book else "Datenträger" if is_physical_media
                else "Software" if is_software
                else "Smartphone" if is_phone else "Produkt"
            )
            kind_reference = (
                "diesem Buch" if is_book else "diesem Datenträger"
                if is_physical_media else "dieser Software" if is_software
                else "diesem Smartphone" if is_phone else "diesem Produkt"
            )
            sale_sentence = (
                f"Zum Verkauf steht das Buch „{product_name}“."
                if is_book else f"Zum Verkauf steht „{product_name}“ auf Datenträger."
                if is_physical_media else f"Zum Verkauf steht die Software „{product_name}“."
                if is_software else f"Zum Verkauf steht ein {product_name}."
            )
            intro = (
                f"{sale_sentence} Die online gefundenen "
                f"Angaben zu {kind_reference} sind nachfolgend zusammengefasst "
                "und sollten mit dem tatsächlich angebotenen Artikel abgeglichen werden."
            )
            details_heading = (
                "### Buchdetails" if is_book else "### Medienangaben"
                if is_physical_media else "### Technische Daten"
            )
            if is_book:
                contents = f"* {product_name}"
            elif is_physical_media:
                contents = (
                    f"* {product_name}\n"
                    "* [Originalhülle]\n"
                    "* [Booklet / Einleger]\n"
                    "* [Weitere Discs]"
                )
            elif is_software:
                contents = (
                    f"* {product_name}\n"
                    "* [Datenträger / Lizenzschlüssel]\n"
                    "* [Originalverpackung / Anleitung]\n"
                    "* [Weiterer Lieferumfang]"
                )
            else:
                contents = (
                    f"* {product_name}\n"
                    "* [Originalverpackung]\n"
                    "* [Ladekabel / Netzteil]\n"
                    "* [Weiteres Zubehör]"
                )
            if is_book:
                footer = (
                    "Das Buch befindet sich in **[neuem / neuwertigem / sehr "
                    "gutem / gutem / gelesenem]** Zustand.\n\n"
                    "Einband und Seiten weisen **[keine / leichte / sichtbare]** "
                    "Gebrauchsspuren auf. Markierungen oder Notizen sind "
                    "**[nicht vorhanden / vorhanden: ...]**.\n\n"
                    "Bei Fragen einfach melden."
                )
            elif is_physical_media:
                footer = (
                    "Datenträger und Hülle befinden sich in **[neuem / "
                    "neuwertigem / sehr gutem / gutem / gebrauchtem]** "
                    "Zustand.\n\n"
                    "Kratzer sind **[keine vorhanden / leicht vorhanden / "
                    "sichtbar vorhanden]**. Die Wiedergabe wurde "
                    "**[erfolgreich getestet / nicht getestet]**.\n\n"
                    "Bei Fragen einfach melden."
                )
            else:
                footer = (
                    "Der Artikel befindet sich in **[neuem / neuwertigem / "
                    "sehr gutem / gutem / gebrauchtem]** Zustand und "
                    "**[ist ungeöffnet originalverpackt / funktioniert "
                    "einwandfrei / hat folgende Einschränkungen: ...]**.\n\n"
                    "Gebrauchsspuren sind **[keine vorhanden / leicht "
                    "vorhanden / deutlich vorhanden]**.\n\n"
                    "Bei Fragen einfach melden."
                )
            contents_heading = "### Lieferumfang"
            review_hint = "*(Nicht Zutreffendes bitte entfernen oder ergänzen.)*"

        facts = '\n'.join(f"* {line}" for line in raw_lines)
        if not facts:
            if language == "en":
                facts = (
                    "* [Add author, edition, publisher, ISBN and page count]"
                    if is_book else "* [Add format, edition, running time, "
                    "region and number of discs]"
                    if is_physical_media else "* [Add technical details]"
                )
            else:
                facts = (
                    "* [Autor, Ausgabe, Verlag, ISBN und Seitenzahl ergänzen]"
                    if is_book else "* [Format, Ausgabe, Laufzeit, Region und "
                    "Anzahl der Datenträger ergänzen]"
                    if is_physical_media else "* [Technische Angaben ergänzen]"
                )

        return (
            f"**{product_name} – {title_suffix}**\n\n"
            f"{intro}\n\n"
            f"{details_heading}\n\n{facts}\n\n"
            f"{contents_heading}\n\n{contents}\n\n"
            f"{review_hint}\n\n"
            f"{footer}"
        )

    @staticmethod
    def translate_fact_label(line):
        """Übersetzt bekannte strukturierte Metadatenfelder ins Englische."""
        if ':' not in line:
            return line
        label, value = line.split(':', 1)
        translations = {
            'Autor': 'Author',
            'Ausgabe': 'Edition',
            'Erscheinungsort': 'Place of publication',
            'Verlag': 'Publisher',
            'Erscheinungsdatum': 'Publication date',
            'Sprache': 'Language',
            'Umfang': 'Extent',
            'Ausstattung': 'Features',
            'Format': 'Format',
            'Einband': 'Binding',
            'Anzahl der Seiten': 'Pages',
            'Herausgeber': 'Editor',
            'Produktübersicht': 'Product overview',
        }
        translated_label = translations.get(label.strip(), label.strip())
        translated_value = value.strip()
        value_translations = {
            'Deutsch': 'German',
            'Englisch': 'English',
            'Taschenbuch': 'Paperback',
            'Gebundene Ausgabe': 'Hardcover',
            'Illustrationen': 'Illustrations',
        }
        translated_value = value_translations.get(
            translated_value, translated_value
        )
        return f"{translated_label}: {translated_value}"
    
    def save_listing(self, listing, product_name, output_dir=None):
        """Speichert die Liste als Textdatei, ohne vorhandene zu überschreiben.

        Gemeinsamer Schreibpfad für den Backend-Export und die Oberfläche.
        """
        directory = Path(output_dir or self.output_dir)
        directory.mkdir(parents=True, exist_ok=True)
        stem = safe_filename(product_name)
        filepath = directory / f"{stem}.txt"

        counter = 1
        while filepath.exists():
            filepath = directory / f"{stem}_{counter}.txt"
            counter += 1

        filepath.write_text(listing, encoding='utf-8')
        return filepath


class ProductGeneratorGUI:
    """GUI für den Produktgenerator"""

    # Tab-übergreifend geteilt; Zugriffe laufen aus Netzwerk-Threads.
    _search_cache = {}
    _search_cache_lock = threading.Lock()
    _search_cache_ttl = 15 * 60
    # Lief eine Quelle auf einen Fehler, wird kurz gehalten und bald erneut
    # gefragt; ein leeres Ergebnis wird gar nicht gespeichert.
    _search_cache_partial_ttl = 90
    _search_cache_max_entries = 128
    
    def __init__(
        self, root, embedded=False, close_callback=None, title_callback=None,
        language_callback=None, variant_open_callback=None
    ):
        self.root = root
        self.embedded = embedded
        self.close_callback = close_callback
        self.title_callback = title_callback
        self.language_callback = language_callback
        self.variant_open_callback = variant_open_callback
        self._closed = False
        if not embedded:
            self.root.geometry("900x750")
            self.root.resizable(True, True)
        self.root.tk.call('tk', 'scaling', self.detect_dpi_scaling())
        
        # Backend
        self.generator = ProductGenerator()
        
        self.config_file = Path.home() / ".eBayCreationToolConfig.json"
        self.default_language = self.detect_system_language()
        config = self.load_config()
        self.language = config.get('language', self.default_language)
        self.font_size = config.get('font_size', 10)
        self.provider_settings = config.get(
            'providers',
            {
                'wikipedia': True, 'amazon': True, 'geizhals': True,
                'idealo': True, 'ebay': False,
                'kleinanzeigen_agent': False,
            },
        )
        self._ebay_access_token = None
        self._ebay_access_token_expires = 0
        self._ebay_result_metadata = {}
        self._market_result_metadata = {}
        self.ebay_ru_name = str(config.get('ebay_ru_name', ''))
        self.ebay_postal_code = str(config.get('ebay_postal_code', ''))
        self.ebay_country = str(config.get('ebay_country', 'DE'))
        self.ebay_policy_ids = {}
        self._ebay_policy_entries = {}
        self.ebay_environment = config.get('ebay_environment', 'production')
        if self.ebay_environment not in ('production', 'sandbox'):
            self.ebay_environment = 'production'
        self.restore_session_enabled = bool(
            config.get('restore_session', True)
        )
        self.clear_session_on_exit = bool(
            config.get('clear_session_on_exit', False)
        )
        self.legal_clause = self.current_legal_clause(
            config.get('legal_clause')
        )
        project_output = str(default_output_dir())
        self.save_path = config.get('save_path', project_output)
        if not os.path.exists(self.save_path):
            Path(project_output).mkdir(parents=True, exist_ok=True)
            self.save_path = project_output
        
        self.selected_variant = None
        self.opened_file_path = None
        self.search_results = []
        self._search_after_id = None
        self._search_generation = 0
        self._ebay_metadata_generation = 0
        self.ebay_categories = []
        self.ebay_aspects = []
        self.ebay_aspect_values = {}
        self.listing_store = ListingStore(user_data_dir() / 'listings.db')
        self.product_record_id = ''
        self.current_platform = 'kleinanzeigen'
        self.platform_drafts = {}
        self._switching_platform = False
        
        self.style = ttk.Style()
        theme = 'vista' if 'vista' in self.style.theme_names() else 'clam'
        self.style.theme_use(theme)
        self.root.configure(background='#f5f5f5')
        self.set_font_size(self.font_size)
        
        if not embedded:
            self.create_menu()
        self.setup_ui()

    @staticmethod
    @staticmethod
    def current_legal_clause(saved):
        """Hebt eine unveränderte frühere Vorgabe auf die aktuelle Fassung.

        Der Pflichttext wird mitgespeichert, sobald irgendeine Einstellung
        gesichert wird. Ohne diesen Abgleich bliebe jede bestehende
        Installation dauerhaft auf der alten Fassung stehen – auch wer den
        Text nie angefasst hat.
        """
        clause = str(saved or '').strip()
        if not clause or clause in SUPERSEDED_CLAUSES:
            return WARRANTY_CLAUSE
        return clause

    @staticmethod
    def detect_system_language():
        """Ermittelt die Oberflächensprache ohne das entfernte
        ``locale.getdefaultlocale``."""
        try:
            language = locale.getlocale()[0] or ''
        except (TypeError, ValueError):
            language = ''
        if not language:
            for name in ('LC_ALL', 'LC_MESSAGES', 'LANG', 'LANGUAGE'):
                language = os.environ.get(name, '')
                if language:
                    break
        return 'en' if language.casefold().startswith('en') else 'de'

    def detect_dpi_scaling(self):
        try:
            dpi = self.root.winfo_fpixels('1i')
            return max(min(dpi / 72, 2.5), 1.0)
        except Exception:
            return 1.0

    def set_font_size(self, size):
        self.font_size = max(8, min(18, int(size)))
        self.default_font = tkfont.Font(family='Segoe UI', size=self.font_size)
        self.menu_font = tkfont.Font(family='Segoe UI', size=self.font_size)
        self.text_font = tkfont.Font(family='Courier', size=max(10, self.font_size))
        self.style.configure('.', font=self.default_font)

    def create_menu(self):
        trans = TRANSLATIONS[self.language]
        # Ohne tearoff=0 liegt unter Windows ein unsichtbarer Tearoff-Eintrag
        # auf Index 0; jedes entryconfig(0, label=…) scheitert dann.
        self.menubar = tk.Menu(self.root, tearoff=0)
        self.settings_menu = tk.Menu(self.menubar, tearoff=0)
        self.settings_menu.add_command(
            label=trans['menu_change_save_path'],
            command=self.change_save_path
        )
        self.settings_menu.add_command(
            label=trans['menu_default_save_path'],
            command=self.set_default_save_path
        )
        self.menubar.add_cascade(label=trans['menu_settings'], menu=self.settings_menu)
        self.root.config(menu=self.menubar)

    def load_config(self):
        try:
            if self.config_file.exists():
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception:
            print(TRANSLATIONS['de']['config_load_error'])
        return {}

    def save_config(self):
        try:
            config = {
                'language': self.language,
                'font_size': self.font_size,
                'save_path': self.save_path,
                'legal_clause': self.legal_clause,
                'ebay_environment': self.ebay_environment,
                'ebay_ru_name': self.ebay_ru_name,
                'ebay_postal_code': self.ebay_postal_code,
                'ebay_country': self.ebay_country,
                'restore_session': self.restore_session_enabled,
                'clear_session_on_exit': self.clear_session_on_exit,
                'providers': {
                    name: bool(variable.get())
                    for name, variable in getattr(self, 'provider_vars', {}).items()
                } or self.provider_settings,
            }
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            restrict_to_owner(self.config_file)
        except Exception:
            print(TRANSLATIONS['de']['config_save_error'])

    @staticmethod
    def get_secret(name):
        """Liest Zugangsdaten ausschließlich aus dem Betriebssystem-Keyring."""
        environment_names = {
            'kleinanzeigen_api_key': 'KLAZ_API_KEY',
            'ebay_client_id': 'EBAY_CLIENT_ID',
            'ebay_client_secret': 'EBAY_CLIENT_SECRET',
        }
        environment_value = os.environ.get(environment_names.get(name, ''), '')
        if environment_value:
            return environment_value
        try:
            keyring = ProductGeneratorGUI.secure_keyring()
            return keyring.get_password(SECRET_SERVICE, name) or ''
        except Exception:
            return ''

    @staticmethod
    def secure_keyring():
        """Liefert nur einen echten Betriebssystem-Keyring, nie Plaintext."""
        import keyring
        backend = keyring.get_keyring()
        module = backend.__class__.__module__.casefold()
        class_name = backend.__class__.__name__.casefold()
        unsafe_markers = ('null', 'fail', 'plaintext', 'unencrypted')
        if any(marker in module or marker in class_name for marker in unsafe_markers):
            raise RuntimeError(
                f"Unsicheres Keyring-Backend: {backend.__class__.__name__}"
            )
        if os.name == 'nt' and 'keyring.backends.windows' not in module:
            raise RuntimeError(
                f"Kein Windows Credential Locker: {backend.__class__.__name__}"
            )
        return keyring

    @staticmethod
    def set_secret(name, value):
        """Speichert ein Secret verschlüsselt über den System-Keyring."""
        if not value:
            return
        keyring = ProductGeneratorGUI.secure_keyring()
        keyring.set_password(SECRET_SERVICE, name, value)

    @staticmethod
    def entered_secret(value):
        """Gibt nur tatsächlich neu eingegebene Zugangsdaten zurück."""
        normalized = str(value or '').strip()
        if not normalized or normalized == SECRET_PLACEHOLDER:
            return ''
        return normalized

    @staticmethod
    def delete_secret(name):
        keyring = ProductGeneratorGUI.secure_keyring()
        try:
            keyring.delete_password(SECRET_SERVICE, name)
        except keyring.errors.PasswordDeleteError:
            pass

    @staticmethod
    def audit_security_event(action, provider='', outcome='success'):
        """Schreibt ausschließlich Metadaten, niemals Secrets oder Antworten."""
        record = {
            'timestamp': datetime.now().astimezone().isoformat(timespec='seconds'),
            'action': re.sub(r'[^a-z0-9_.-]', '_', action.casefold())[:64],
            'provider': re.sub(r'[^a-z0-9_.-]', '_', provider.casefold())[:32],
            'outcome': 'success' if outcome == 'success' else 'failed',
        }
        path = Path.home() / ".eBayCreationToolSecurity.log"
        try:
            with open(path, 'a', encoding='utf-8') as handle:
                handle.write(json.dumps(record, ensure_ascii=True) + '\n')
            restrict_to_owner(path)
        except OSError:
            # Ein nicht schreibbares Protokoll darf die auslösende Aktion
            # (Verbindungstest, Secret-Löschung) nicht abbrechen.
            print(TRANSLATIONS['de']['security_log_error'])
    
    def setup_ui(self):
        """Erstellt die Benutzeroberfläche"""
        trans = TRANSLATIONS[self.language]
        
        if not self.embedded:
            self.root.title(trans['title'])
        
        # ===== Frame 1: Produktsuche =====
        self.search_frame = ttk.LabelFrame(self.root, text=trans['search_frame'], padding=10)
        self.search_frame.pack(fill=tk.X, padx=10, pady=10)
        
        self.search_label_widget = ttk.Label(self.search_frame, text=trans['search_label'])
        self.search_label_widget.pack(anchor=tk.W)
        self.search_var = tk.StringVar()
        self.search_var.trace_add('write', self.on_search_changed)
        
        search_entry = ttk.Entry(self.search_frame, textvariable=self.search_var, width=60)
        search_entry.pack(fill=tk.X, pady=5)
        search_entry.focus()
        
        # ===== Frame 2: Variantenauswahl =====
        self.variant_frame = ttk.LabelFrame(self.root, text=trans['variant_frame'], padding=10)
        self.variant_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        scrollbar = ttk.Scrollbar(self.variant_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.variant_listbox = tk.Listbox(
            self.variant_frame,
            yscrollcommand=scrollbar.set,
            font=("Arial", self.font_size),
            height=8
        )
        self.variant_listbox.pack(fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.variant_listbox.yview)
        
        self.variant_listbox.bind('<<ListboxSelect>>', self.on_variant_selected)
        self.variant_listbox.bind('<Button-3>', self.show_variant_context_menu)
        self.variant_context_menu = tk.Menu(self.variant_listbox, tearoff=0)
        self.variant_context_menu.add_command(
            label=trans['open_in_new_tab'],
            command=self.open_selected_variant_in_new_tab,
        )

        # ===== eBay-Kategorie und Artikelmerkmale =====
        self.ebay_check_frame = ttk.LabelFrame(
            self.root, text=trans['ebay_check_frame'], padding=8
        )
        self.ebay_check_frame.pack(fill=tk.X, padx=10, pady=(0, 10))
        category_row = ttk.Frame(self.ebay_check_frame)
        category_row.pack(fill=tk.X)
        self.ebay_category_label = ttk.Label(
            category_row, text=trans['ebay_category']
        )
        self.ebay_category_label.pack(side=tk.LEFT)
        self.ebay_category_var = tk.StringVar()
        self.ebay_category_combo = ttk.Combobox(
            category_row,
            textvariable=self.ebay_category_var,
            state='readonly',
            width=70,
        )
        self.ebay_category_combo.pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0)
        )
        self.ebay_category_combo.bind(
            '<<ComboboxSelected>>', self.on_ebay_category_selected
        )
        self.ebay_check_status_var = tk.StringVar(
            value=trans['ebay_check_hint']
        )
        self.ebay_check_status_label = ttk.Label(
            self.ebay_check_frame,
            textvariable=self.ebay_check_status_var,
            foreground='#555555',
        )
        self.ebay_check_status_label.pack(fill=tk.X, pady=(5, 4))

        aspect_area = ttk.Frame(self.ebay_check_frame)
        aspect_area.pack(fill=tk.X)
        self.ebay_aspect_tree = ttk.Treeview(
            aspect_area,
            columns=('aspect', 'value', 'requirement', 'status'),
            show='headings',
            height=4,
            selectmode='browse',
        )
        for column, label_key, width in (
            ('aspect', 'ebay_aspect', 220),
            ('value', 'ebay_value', 360),
            ('requirement', 'ebay_requirement', 100),
            ('status', 'ebay_status', 90),
        ):
            self.ebay_aspect_tree.heading(
                column, text=trans[label_key]
            )
            self.ebay_aspect_tree.column(
                column, width=width, minwidth=70,
                stretch=column in ('aspect', 'value')
            )
        aspect_scrollbar = ttk.Scrollbar(
            aspect_area, command=self.ebay_aspect_tree.yview
        )
        self.ebay_aspect_tree.configure(
            yscrollcommand=aspect_scrollbar.set
        )
        self.ebay_aspect_tree.pack(
            side=tk.LEFT, fill=tk.X, expand=True
        )
        aspect_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.ebay_aspect_tree.bind(
            '<<TreeviewSelect>>', self.on_ebay_aspect_selected
        )

        aspect_edit_row = ttk.Frame(self.ebay_check_frame)
        aspect_edit_row.pack(fill=tk.X, pady=(5, 0))
        self.ebay_aspect_value_var = tk.StringVar()
        self.ebay_aspect_value_combo = ttk.Combobox(
            aspect_edit_row,
            textvariable=self.ebay_aspect_value_var,
            state='normal',
        )
        self.ebay_aspect_value_combo.pack(
            side=tk.LEFT, fill=tk.X, expand=True
        )
        self.ebay_aspect_apply_button = ttk.Button(
            aspect_edit_row,
            text=trans['ebay_apply_value'],
            command=self.apply_ebay_aspect_value,
        )
        self.ebay_aspect_apply_button.pack(side=tk.LEFT, padx=(6, 0))
        self.ebay_check_frame.pack_forget()

        # ===== Plattformneutraler Inserat-Assistent =====
        self.assistant_frame = ttk.LabelFrame(
            self.root, text=trans['assistant_frame'], padding=8
        )
        self.assistant_frame.pack(fill=tk.X, padx=10, pady=(0, 10))
        assistant_fields = ttk.Frame(self.assistant_frame)
        assistant_fields.pack(fill=tk.X)
        self.condition_var = tk.StringVar(
            value=trans['condition_values'].split('|')[0]
        )
        self.scope_var = tk.StringVar()
        self.asking_price_var = tk.StringVar()
        self.price_type_var = tk.StringVar(
            value=trans['price_type_values'].split('|')[0]
        )
        self.price_basis_var = tk.StringVar(value='active')
        self.ebay_quantity_var = tk.StringVar(value='1')
        self.price_basis_display_var = tk.StringVar(
            value=trans['price_active']
        )
        for column in range(8):
            assistant_fields.columnconfigure(
                column, weight=1 if column in (1, 3) else 0
            )
        ttk.Label(
            assistant_fields, text=trans['condition_label']
        ).grid(row=0, column=0, sticky=tk.W, padx=(0, 4))
        self.condition_combo = ttk.Combobox(
            assistant_fields,
            textvariable=self.condition_var,
            values=trans['condition_values'].split('|'),
            state='readonly',
            width=18,
        )
        self.condition_combo.grid(row=0, column=1, sticky=tk.EW, padx=(0, 10))
        ttk.Label(
            assistant_fields, text=trans['scope_label']
        ).grid(row=0, column=2, sticky=tk.W, padx=(0, 4))
        self.scope_entry = ttk.Entry(
            assistant_fields, textvariable=self.scope_var
        )
        self.scope_entry.grid(row=0, column=3, sticky=tk.EW, padx=(0, 10))
        ttk.Label(
            assistant_fields, text=trans['asking_price_label']
        ).grid(row=0, column=4, sticky=tk.W, padx=(0, 4))
        self.asking_price_entry = ttk.Entry(
            assistant_fields, textvariable=self.asking_price_var, width=10
        )
        self.asking_price_entry.grid(row=0, column=5, sticky=tk.W)
        self.price_type_combo = ttk.Combobox(
            assistant_fields,
            textvariable=self.price_type_var,
            values=trans['price_type_values'].split('|'),
            state='readonly',
            width=14,
        )
        self.price_type_combo.grid(row=0, column=6, sticky=tk.W, padx=(4, 0))
        self.price_basis_combo = ttk.Combobox(
            assistant_fields,
            textvariable=self.price_basis_display_var,
            values=(trans['price_active'], trans['price_sold']),
            state='readonly',
            width=8,
        )
        self.price_basis_combo.grid(row=0, column=7, sticky=tk.E, padx=(8, 0))

        assistant_actions = ttk.Frame(self.assistant_frame)
        assistant_actions.pack(fill=tk.X, pady=(6, 0))
        self.apply_assistant_button = ttk.Button(
            assistant_actions,
            text=trans['apply_assistant'],
            command=self.apply_assistant_details,
        )
        self.apply_assistant_button.pack(side=tk.LEFT)
        self.fact_conflicts_button = ttk.Button(
            assistant_actions,
            text=trans['fact_conflicts'],
            command=self.open_fact_conflicts,
        )
        self.fact_conflicts_button.pack(side=tk.LEFT, padx=(6, 0))
        self.buyback_button = ttk.Button(
            assistant_actions,
            text=trans['buyback_check'],
            command=self.show_buyback_menu,
            state=tk.DISABLED,
        )
        self.buyback_button.pack(side=tk.LEFT, padx=(6, 0))
        self.completeness_var = tk.StringVar()
        ttk.Label(
            assistant_actions, textvariable=self.completeness_var,
            foreground='#555555',
        ).pack(side=tk.LEFT, padx=(12, 0))
        self.price_summary_var = tk.StringVar()
        ttk.Label(
            self.assistant_frame, textvariable=self.price_summary_var,
            foreground='#555555',
        ).pack(fill=tk.X, pady=(5, 0))
        self.price_basis_combo.bind(
            '<<ComboboxSelected>>',
            self.on_price_basis_changed,
        )
        for variable in (
            self.condition_var, self.scope_var, self.asking_price_var
        ):
            variable.trace_add(
                'write', lambda *args: self.update_listing_completeness()
            )
        self.assistant_frame.pack_forget()
        
        # ===== Frame 3: Vorschau =====
        self.preview_frame = ttk.LabelFrame(self.root, text=trans['preview_frame'], padding=10)
        self.preview_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        preview_panes = tk.PanedWindow(
            self.preview_frame, orient=tk.HORIZONTAL, sashwidth=6
        )
        preview_panes.pack(fill=tk.BOTH, expand=True)

        self.editor_frame = ttk.LabelFrame(
            preview_panes, text=trans['editor_label'], padding=5
        )
        self.rendered_frame = ttk.LabelFrame(
            preview_panes, text=trans['live_preview_label'], padding=5
        )
        preview_panes.add(self.editor_frame, stretch='always', minsize=300)
        preview_panes.add(self.rendered_frame, stretch='always', minsize=300)

        platform_row = ttk.Frame(self.editor_frame)
        platform_row.pack(fill=tk.X, pady=(0, 5))
        self.platform_label_widget = ttk.Label(
            platform_row, text=trans['platform_label']
        )
        self.platform_label_widget.pack(side=tk.LEFT)
        self.platform_var = tk.StringVar(
            value=PLATFORM_PROFILES[self.current_platform].label_de
        )
        self.platform_combo = ttk.Combobox(
            platform_row,
            textvariable=self.platform_var,
            values=tuple(
                profile.label_de for profile in PLATFORM_PROFILES.values()
            ),
            state='readonly',
            width=18,
        )
        self.platform_combo.pack(side=tk.LEFT, padx=(6, 10))
        self.platform_combo.bind(
            '<<ComboboxSelected>>', self.on_platform_changed
        )
        self.title_counter_var = tk.StringVar(value='0 / 65')
        ttk.Label(
            platform_row, textvariable=self.title_counter_var
        ).pack(side=tk.RIGHT)

        title_row = ttk.Frame(self.editor_frame)
        title_row.pack(fill=tk.X, pady=(0, 5))
        self.listing_title_label = ttk.Label(
            title_row, text=trans['listing_title_label']
        )
        self.listing_title_label.pack(side=tk.LEFT)
        self.platform_title_var = tk.StringVar()
        self.platform_title_var.trace_add(
            'write', self.on_platform_title_changed
        )
        self.platform_title_entry = ttk.Entry(
            title_row, textvariable=self.platform_title_var
        )
        self.platform_title_entry.pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0)
        )
        self.description_counter_var = tk.StringVar(value='0 / 4000')
        ttk.Label(
            self.editor_frame, textvariable=self.description_counter_var,
            anchor=tk.E,
        ).pack(fill=tk.X, pady=(0, 3))

        scrollbar_text = ttk.Scrollbar(self.editor_frame)
        scrollbar_text.pack(side=tk.RIGHT, fill=tk.Y)

        self.preview_text = tk.Text(
            self.editor_frame,
            yscrollcommand=scrollbar_text.set,
            font=("Courier", max(10, self.font_size)),
            height=10,
            width=55,
            wrap=tk.WORD,
        )
        self.preview_text.pack(fill=tk.BOTH, expand=True)
        scrollbar_text.config(command=self.preview_text.yview)
        self.preview_text.bind('<<Modified>>', self.on_draft_modified)
        self.preview_text.edit_modified(False)

        rendered_panes = tk.PanedWindow(
            self.rendered_frame,
            orient=tk.HORIZONTAL,
            sashwidth=5,
            background="#d9d9d9",
        )
        rendered_panes.pack(fill=tk.BOTH, expand=True)
        self.cover_panel = ttk.Frame(rendered_panes, width=250)
        self.rendered_text_panel = ttk.Frame(rendered_panes)
        rendered_panes.add(self.cover_panel, minsize=180, width=250)
        rendered_panes.add(
            self.rendered_text_panel, stretch='always', minsize=300
        )

        self.product_image_label = ttk.Label(
            self.cover_panel,
            text=trans['no_product_image'],
            anchor=tk.N,
        )
        self.product_image_label.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        self.image_controls = ttk.Frame(self.cover_panel)
        self.image_controls.pack(fill=tk.X, padx=6, pady=(0, 6))
        self.previous_image_button = ttk.Button(
            self.image_controls,
            text=trans['previous_image'],
            width=3,
            command=lambda: self.show_relative_product_image(-1),
            state=tk.DISABLED,
        )
        self.previous_image_button.pack(side=tk.LEFT)
        self.image_counter_var = tk.StringVar(value="0 / 0")
        self.image_counter_label = ttk.Label(
            self.image_controls,
            textvariable=self.image_counter_var,
            anchor=tk.CENTER,
        )
        self.image_counter_label.pack(side=tk.LEFT, expand=True, padx=4)
        self.next_image_button = ttk.Button(
            self.image_controls,
            text=trans['next_image'],
            width=3,
            command=lambda: self.show_relative_product_image(1),
            state=tk.DISABLED,
        )
        self.next_image_button.pack(side=tk.LEFT)
        self.save_image_button = ttk.Button(
            self.cover_panel,
            text=trans['save_image'],
            command=self.save_current_product_image,
            state=tk.DISABLED,
        )
        self.save_image_button.pack(fill=tk.X, padx=6, pady=(0, 6))
        # Eigene Fotos: bei Kleinanzeigen und eBay laedt man sie selbst hoch,
        # das Werkzeug bereitet sie nur vollstaendig vor.
        self.own_images_frame = ttk.LabelFrame(
            self.cover_panel, text=trans['own_images_frame'], padding=4
        )
        self.own_images_list = tk.Listbox(
            self.own_images_frame, height=4, exportselection=False,
            activestyle='none',
        )
        self.own_images_list.pack(fill=tk.BOTH, expand=True)
        self.own_images_list.bind(
            '<<ListboxSelect>>', self.on_own_image_selected
        )
        own_image_buttons = ttk.Frame(self.own_images_frame)
        own_image_buttons.pack(fill=tk.X, pady=(4, 0))
        self.add_own_images_button = ttk.Button(
            own_image_buttons, text=trans['add_own_images'],
            command=self.add_own_images,
        )
        self.add_own_images_button.pack(side=tk.LEFT)
        self.move_own_image_up_button = ttk.Button(
            own_image_buttons, text='▲', width=3,
            command=lambda: self.move_own_image(-1),
        )
        self.move_own_image_up_button.pack(side=tk.LEFT, padx=(4, 0))
        self.move_own_image_down_button = ttk.Button(
            own_image_buttons, text='▼', width=3,
            command=lambda: self.move_own_image(1),
        )
        self.move_own_image_down_button.pack(side=tk.LEFT, padx=(2, 0))
        self.remove_own_image_button = ttk.Button(
            own_image_buttons, text='✕', width=3,
            command=self.remove_own_image,
        )
        self.remove_own_image_button.pack(side=tk.LEFT, padx=(2, 0))
        self.own_images_hint_var = tk.StringVar()
        ttk.Label(
            self.own_images_frame, textvariable=self.own_images_hint_var,
            foreground='#555555', wraplength=220, justify=tk.LEFT,
        ).pack(fill=tk.X, pady=(4, 0))
        self.own_images = []

        # Die Bedienelemente werden zuerst vom unteren Rand reserviert.
        # Dadurch kann ein großes Bild sie in kleinen Fenstern nicht verdrängen.
        self.product_image_label.pack_forget()
        self.image_controls.pack_forget()
        self.save_image_button.pack_forget()
        self.own_images_frame.pack(
            side=tk.BOTTOM, fill=tk.X, padx=6, pady=(0, 6)
        )
        self.save_image_button.pack(
            side=tk.BOTTOM, fill=tk.X, padx=6, pady=(0, 6)
        )
        self.image_controls.pack(
            side=tk.BOTTOM, fill=tk.X, padx=6, pady=(0, 6)
        )
        self.product_image_label.pack(
            fill=tk.BOTH, expand=True, padx=6, pady=6
        )
        self._product_photo = None
        self._product_image_original = None
        self._product_image_urls = []
        self._product_image_index = -1
        self._product_image_current_url = ''
        self._cover_resize_after_id = None
        self._image_generation = 0
        self.cover_panel.bind('<Configure>', self.on_cover_panel_resized)

        rendered_scrollbar = ttk.Scrollbar(self.rendered_text_panel)
        rendered_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.rendered_preview = tk.Text(
            self.rendered_text_panel,
            yscrollcommand=rendered_scrollbar.set,
            font=("Segoe UI", max(10, self.font_size)),
            height=10,
            width=55,
            wrap=tk.WORD,
            background="#ffffff",
            state=tk.DISABLED,
        )
        self.rendered_preview.pack(fill=tk.BOTH, expand=True)
        rendered_scrollbar.config(command=self.rendered_preview.yview)
        self.rendered_preview.tag_configure(
            'title', font=("Segoe UI", max(12, self.font_size + 2), "bold")
        )
        self.rendered_preview.tag_configure(
            'heading', font=("Segoe UI", max(11, self.font_size + 1), "bold")
        )
        self.rendered_preview.tag_configure(
            'legal', foreground="#555555", font=("Segoe UI", max(9, self.font_size - 1))
        )

        self.legal_frame = ttk.LabelFrame(
            self.root, text=trans['legal_frame'], padding=8
        )
        self.legal_frame.pack(fill=tk.X, padx=10, pady=(0, 10))
        self.legal_text = tk.Text(
            self.legal_frame, height=4, wrap=tk.WORD,
            font=("Segoe UI", max(9, self.font_size - 1)),
            background="#eeeeee"
        )
        self.legal_text.pack(fill=tk.X)
        self.legal_text.insert("1.0", self.legal_clause)
        self.legal_text.config(state=tk.DISABLED)
        
        # ===== Frame 4: Aktionen =====
        self.options_frame = ttk.LabelFrame(self.root, text=trans['options_frame'], padding=10)
        self.options_frame.pack(fill=tk.BOTH, expand=False, padx=10, pady=10)
        
        path_info_frame = ttk.Frame(self.options_frame)
        path_info_frame.pack(fill=tk.X, pady=(0, 10))
        
        self.path_label_widget = ttk.Label(
            path_info_frame,
            text=trans['path_label'],
            font=("Segoe UI", 9, "bold"),
        )
        self.path_label_widget.pack(anchor=tk.W)
        self.path_label = ttk.Label(
            path_info_frame,
            text=self.save_path,
            foreground="#1a73e8",
            font=("Segoe UI", max(9, self.font_size - 1))
        )
        self.path_label.pack(anchor=tk.W, fill=tk.X)
        
        lang_frame = ttk.Frame(self.options_frame)
        lang_frame.pack(fill=tk.X, pady=(0, 10))
        
        self.language_label_widget = ttk.Label(lang_frame, text=trans['language_label'])
        self.language_label_widget.pack(side=tk.LEFT)
        self.language_var = tk.StringVar(value=self.language)
        self.language_menu = ttk.OptionMenu(
            lang_frame,
            self.language_var,
            self.language,
            "de",
            "en",
            command=self.on_language_changed
        )
        self.language_menu.pack(side=tk.LEFT, padx=10)

        self.provider_frame = ttk.LabelFrame(
            self.options_frame, text=trans['provider_frame'], padding=6
        )
        self.provider_frame.pack(fill=tk.X, pady=(0, 10))
        self.provider_vars = {}
        self.provider_buttons = {}
        for name, label_key in (
            ('web_suggestions', 'provider_web_suggestions'),
            ('wikipedia', 'provider_wikipedia'),
            ('amazon', 'provider_amazon'),
            ('geizhals', 'provider_geizhals'),
            ('idealo', 'provider_idealo'),
            ('ebay', 'provider_ebay'),
            ('kleinanzeigen_agent', 'provider_kleinanzeigen_agent'),
        ):
            variable = tk.BooleanVar(
                value=self.provider_settings.get(
                    name, name not in ('ebay', 'kleinanzeigen_agent')
                )
            )
            self.provider_vars[name] = variable
            label = trans[label_key]
            button = tk.Checkbutton(
                self.provider_frame,
                text=label,
                variable=variable,
                command=self.save_config,
                font=("Segoe UI", max(9, self.font_size)),
                foreground="#202124",
                background="#f5f5f5",
                activeforeground="#202124",
                activebackground="#f5f5f5",
                selectcolor="#ffffff",
                anchor=tk.W,
                borderwidth=0,
                highlightthickness=0,
            )
            button.pack(side=tk.LEFT, padx=(0, 14))
            self.provider_buttons[name] = (button, label_key)
        
        font_frame = ttk.Frame(self.options_frame)
        font_frame.pack(fill=tk.X, pady=(0, 10))
        self.font_size_label_widget = ttk.Label(font_frame, text=trans['font_size_label'])
        self.font_size_label_widget.pack(side=tk.LEFT)
        self.font_size_var = tk.IntVar(value=self.font_size)
        self.font_size_spinbox = ttk.Spinbox(
            font_frame,
            from_=8,
            to=18,
            increment=1,
            textvariable=self.font_size_var,
            width=5,
            command=self.on_font_size_changed
        )
        self.font_size_spinbox.pack(side=tk.LEFT, padx=10)
        self.font_size_spinbox.bind('<Return>', lambda e: self.on_font_size_changed())
        self.font_size_spinbox.bind('<FocusOut>', lambda e: self.on_font_size_changed())
        
        self.variant_listbox.config(font=("Arial", self.font_size))
        self.preview_text.config(font=("Courier", max(10, self.font_size)))
        
        button_frame = ttk.Frame(self.options_frame)
        button_frame.pack(fill=tk.X, pady=(5, 0))
        
        self.save_button = ttk.Button(
            button_frame,
            text=trans['save_button'],
            command=self.save_file
        )
        self.save_button.pack(side=tk.LEFT, padx=5)
        
        self.close_button = ttk.Button(
            button_frame,
            text=trans['close_tab'] if self.embedded else trans['close_button'],
            command=self.close_callback if self.close_callback else self.root.quit
        )
        self.close_button.pack(side=tk.RIGHT, padx=5)

        # Die Konfiguration wird im Tabbed-Modus nur über den separaten
        # Einstellungsdialog angezeigt.
        self.options_frame.pack_forget()
        
        self.status_var = tk.StringVar(value=trans['status_ready'])
        status_bar = ttk.Label(
            self.root,
            textvariable=self.status_var,
            background="#e8f0fe",
            foreground="#202124",
            relief=tk.SUNKEN,
            anchor=tk.W,
            padding=6
        )
        status_bar.pack(fill=tk.X, side=tk.BOTTOM, padx=5, pady=5)
    
    def on_search_changed(self, *args):
        """Wird aufgerufen, wenn der Suchtext sich ändert"""
        trans = TRANSLATIONS[self.language]
        search_term = self.search_var.get().strip()
        if self.title_callback:
            self.title_callback(search_term)
        self._search_generation += 1
        generation = self._search_generation
        if self._search_after_id is not None:
            try:
                self.root.after_cancel(self._search_after_id)
            except tk.TclError:
                pass
            self._search_after_id = None
        
        self.variant_listbox.delete(0, tk.END)
        self.selected_variant = None
        self.preview_text.config(state=tk.NORMAL)
        self.preview_text.delete(1.0, tk.END)
        self.reset_product_image()
        self.reset_ebay_data_check()
        
        if not search_term:
            self.status_var.set(trans['no_search_term'])
            self.search_results = []
            return
        
        self.search_results = self.generator.search_products(search_term)
        
        if not self.search_results:
            self.status_var.set(f"{trans['products_not_found']} '{search_term}'")
        else:
            # Lokale Treffer sofort anzeigen; die Online-Suche ergänzt sie.
            for result in self.search_results:
                self.variant_listbox.insert(
                    tk.END, self.result_display_label(result)
                )
            self.status_var.set(
                f"{len(self.search_results)} {trans['variants_found']}"
            )
            self.select_result_index(0)

        # Die globale Suche läuft grundsätzlich immer. Lokale Treffer dürfen
        # Amazon, Wikipedia, Geizhals oder Idealo nicht mehr verhindern.
        self._search_after_id = self.root.after(
            450,
            lambda wanted=search_term, request_id=generation:
                self._start_scheduled_online_search(wanted, request_id)
        )
    
    def on_variant_selected(self, *args):
        """Wird aufgerufen, wenn eine Variante ausgewählt wird"""
        selection = self.variant_listbox.curselection()
        
        if not selection:
            return
        
        idx = selection[0]
        if 0 <= idx < len(self.search_results):
            self.selected_variant = self.search_results[idx]['variant']
            if self.host_has_label(
                self.selected_variant.get('source_url', ''), 'amazon'
            ):
                self.selected_variant['name'] = (
                    self.complete_known_title_fragment(
                        self.selected_variant.get('name', '')
                    )
                )
            if self.title_callback:
                self.title_callback(self.selected_variant['name'])
            
            # Vorschau generieren
            description = self.selected_variant.get('description', '')
            if isinstance(description, dict):
                description = description.get(self.language, description.get('de', ''))
            is_placeholder = str(description).startswith((
                'Amazon-Suchergebnis:',
                'Online gefunden:',
                'Web-Suchvorschlag',
            ))
            display_description = (
                description
                if is_placeholder
                else self.generator.build_sales_draft(
                    self.selected_variant['name'],
                    description,
                    self.language,
                )
            )

            self.preview_text.config(state=tk.NORMAL)
            self.preview_text.delete(1.0, tk.END)
            self.preview_text.insert(1.0, display_description)

            source_url = self.selected_variant.get('source_url', '')
            if (
                source_url
                and not self.host_is(source_url, 'suggestqueries.google.com')
            ):
                self.load_product_image_async(
                    self.selected_variant, source_url
                )
            if (
                self.host_is(source_url, 'amazon.de')
                and str(description).startswith('Amazon-Suchergebnis:')
            ):
                self.load_amazon_details_async(self.selected_variant)
            elif (
                self.host_is(source_url, 'geizhals.de', 'idealo.de')
                and str(description).startswith('Online gefunden:')
            ):
                self.load_comparison_details_async(self.selected_variant)
            elif (
                self.host_is(source_url, 'suggestqueries.google.com')
                and str(description).startswith('Web-Suchvorschlag')
            ):
                self.load_suggestion_details_async(self.selected_variant)

            if self.provider_settings.get('ebay') or (
                hasattr(self, 'provider_vars')
                and self.provider_vars.get('ebay')
                and self.provider_vars['ebay'].get()
            ):
                self.start_ebay_data_check(self.selected_variant)
            
            trans = TRANSLATIONS[self.language]
            self.initialize_listing_assistant(
                self.selected_variant, display_description
            )
            self.status_var.set(f"{trans['selected_variant']} {self.selected_variant['name']}")

    def show_variant_context_menu(self, event):
        """Wählt den Treffer unter dem Mauszeiger und zeigt sein Kontextmenü."""
        if not self.variant_listbox.size():
            return
        index = self.variant_listbox.nearest(event.y)
        if not (0 <= index < len(self.search_results)):
            return
        self.variant_listbox.selection_clear(0, tk.END)
        self.variant_listbox.selection_set(index)
        self.variant_listbox.activate(index)
        self.on_variant_selected()
        self.variant_context_menu.tk_popup(event.x_root, event.y_root)

    def open_selected_variant_in_new_tab(self):
        selection = self.variant_listbox.curselection()
        if (
            not selection
            or not self.variant_open_callback
            or selection[0] >= len(self.search_results)
        ):
            return
        self.variant_open_callback(
            copy.deepcopy(self.search_results[selection[0]]),
            self.search_var.get().strip(),
        )

    def select_result_index(self, index):
        """Wählt einen Treffer und erzeugt sofort dessen Live-Entwurf."""
        if not (0 <= index < len(self.search_results)):
            return
        self.variant_listbox.selection_clear(0, tk.END)
        self.variant_listbox.selection_set(index)
        self.variant_listbox.activate(index)
        self.variant_listbox.see(index)
        self.on_variant_selected()

    @staticmethod
    def fit_platform_title(title, limit):
        title = re.sub(r'\s+', ' ', str(title)).strip()
        if len(title) <= limit:
            return title
        shortened = title[:limit + 1].rsplit(' ', 1)[0].rstrip(' -–,:')
        return shortened or title[:limit]

    @staticmethod
    def platform_body_from_draft(draft):
        value = str(draft or '').strip()
        return re.sub(
            r'^\*\*.+?\*\*\s*', '', value, count=1, flags=re.DOTALL
        ).lstrip()

    @staticmethod
    def extract_structured_facts(description):
        facts = []
        for line in str(description or '').splitlines():
            clean = re.sub(r'^\s*[•*\-]\s*', '', line).strip()
            match = re.match(r'^([^:]{2,50}):\s*(.+)$', clean)
            if match:
                key = re.sub(r'\s+', ' ', match.group(1)).strip()
                value = re.sub(r'\s+', ' ', match.group(2)).strip()
                if key and value:
                    facts.append((key, value))
        return facts

    def initialize_listing_assistant(self, variant, draft):
        """Öffnet/erstellt die zentrale Produktakte für den Treffer."""
        identifier = ''
        for key in ('ean', 'isbn', 'gtin', 'ebay_item_id'):
            value = variant.get(key)
            if isinstance(value, (list, tuple)):
                value = value[0] if value else ''
            if value:
                identifier = str(value)
                break
        product_id = self.listing_store.upsert_product(
            variant.get('name', 'Produkt'),
            identifier=identifier,
            source_url=variant.get('source_url', ''),
            state=None,
        )
        new_product = product_id != self.product_record_id
        self.product_record_id = product_id
        source = self.source_name(variant.get('source_url', ''))
        description = variant.get('description', '')
        if isinstance(description, dict):
            description = description.get(
                self.language, description.get('de', '')
            )
        for key, value in self.extract_structured_facts(description):
            self.listing_store.add_fact(
                product_id, key, value, source,
                variant.get('source_url', ''),
            )
        for key, value in (
            variant.get('ebay_aspect_values') or {}
        ).items():
            self.listing_store.add_fact(
                product_id, key, value, 'eBay',
                variant.get('source_url', ''),
            )
        price = variant.get('comparison_price')
        if price and not variant.get('_price_recorded'):
            self.listing_store.add_price(
                product_id, source or 'Online', price,
                condition=variant.get('comparison_condition', ''),
                kind='active', shipping=variant.get('comparison_shipping'),
                source_url=variant.get('source_url', ''),
            )
            variant['_price_recorded'] = True
        if new_product:
            stored_state = self.listing_store.product_state(product_id)
            stored = self.listing_store.load_drafts(product_id)
            self.platform_drafts = {
                key: {
                    'title': value['title'],
                    # Gespeicherte Pflichttexte bleiben im Export, nicht Editor.
                    'description': self.strip_generated_legal(
                        value['description']
                    ),
                }
                for key, value in stored.items()
                # Entwürfe einer nicht mehr geführten Plattform bleiben in der
                # Datenbank, dürfen aber nicht in die Oberfläche gelangen: der
                # Zugriff auf PLATFORM_PROFILES würde sonst fehlschlagen.
                if key in PLATFORM_PROFILES
            }
            for key, profile in PLATFORM_PROFILES.items():
                if key not in self.platform_drafts:
                    body = self.platform_body_from_draft(draft)
                    if profile.description_limit <= COMPACT_DESCRIPTION_LIMIT:
                        body = self.compact_draft(body, key)
                    else:
                        body = self.fit_platform_body(
                            body, profile.description_limit
                        )
                    self.platform_drafts[key] = {
                        'title': self.fit_platform_title(
                            variant.get('name', 'Produkt'),
                            profile.title_limit,
                        ),
                        'description': body,
                    }
            self.current_platform = 'kleinanzeigen'
            self.platform_var.set(
                PLATFORM_PROFILES[self.current_platform].label_de
            )
            self.load_platform_draft(self.current_platform)
            self.condition_var.set(
                variant.get('listing_condition')
                or stored_state.get('condition')
                or TRANSLATIONS[self.language][
                    'condition_values'
                ].split('|')[0]
            )
            self.scope_var.set(
                variant.get('listing_scope')
                or stored_state.get('scope', '')
            )
            self.asking_price_var.set(
                str(
                    variant.get('asking_price')
                    or stored_state.get('asking_price', '')
                )
            )
            self.price_type_var.set(
                variant.get('price_type')
                or stored_state.get('price_type')
                or TRANSLATIONS[self.language][
                    'price_type_values'
                ].split('|')[0]
            )
            self.price_basis_var.set(
                stored_state.get('price_basis', 'active')
            )
            self.price_basis_display_var.set(
                TRANSLATIONS[self.language][
                    'price_sold' if self.price_basis_var.get() == 'sold'
                    else 'price_active'
                ]
            )
        else:
            # Nachgeladene Details aktualisieren nur einen noch unveränderten
            # automatisch erzeugten Entwurf.
            current = self.platform_drafts.get(self.current_platform, {})
            current_text = current.get('description', '')
            if (
                not current_text
                or current_text == draft
                or current_text.startswith((
                    'Amazon-Suchergebnis:',
                    'Online gefunden:',
                    'Web-Suchvorschlag',
                ))
            ):
                current['description'] = self.platform_body_from_draft(draft)
                self.load_platform_draft(self.current_platform)
        self.assistant_frame.pack(
            fill=tk.X, padx=10, pady=(0, 10),
            before=self.preview_frame,
        )
        self.update_listing_counters()
        self.update_price_summary()
        self.update_listing_completeness()
        self.refresh_own_images()

    def strip_generated_legal(self, text):
        marker = '\n\n---\n\n' + self.legal_clause
        value = str(text)
        if marker in value:
            return value.split(marker, 1)[0].rstrip()
        return value

    def compact_draft(self, body, platform='ebay_mobile'):
        """Verdichtet einen Entwurf auf ganze Absätze, ohne Neues zu erfinden.

        Für knappe Profile wie die mobile eBay-Vorschau mit 800 Zeichen
        reicht bloßes Abschneiden nicht.
        """
        limit = PLATFORM_PROFILES[platform].description_limit
        legal = self.legal_clause.strip()
        available = max(80, limit - len(legal) - 10)
        paragraphs = []
        used = 0
        for paragraph in re.split(r'\n\s*\n', str(body).strip()):
            plain = paragraph.strip()
            if not plain:
                continue
            addition = len(plain) + (2 if paragraphs else 0)
            if used + addition > available:
                break
            paragraphs.append(plain)
            used += addition
        return '\n\n'.join(paragraphs)

    def fit_platform_body(self, body, description_limit):
        """Kürzt nur an Absatz-/Zeilengrenzen und erfindet keine Inhalte."""
        available = max(
            100, description_limit - len(self.legal_clause.strip()) - 10
        )
        value = str(body).strip()
        if len(value) <= available:
            return value
        selected = []
        used = 0
        for block in re.split(r'(\n\s*\n|\n)', value):
            if not block:
                continue
            addition = len(block)
            if used + addition > available:
                # Abbrechen statt überspringen: ein übersprungener Block würde
                # den Text in der Mitte auftrennen und Inhalte verfälschen.
                break
            selected.append(block)
            used += addition
        return ''.join(selected).strip()

    def on_platform_changed(self, event=None):
        if self._switching_platform:
            return
        selected_label = self.platform_var.get()
        wanted = next(
            (
                key for key, profile in PLATFORM_PROFILES.items()
                if profile.label_de == selected_label
            ),
            '',
        )
        if not wanted:
            return
        self.save_visible_platform_draft()
        self.current_platform = wanted
        self.load_platform_draft(wanted)
        # Die Bildgrenze haengt an der Plattform.
        self.refresh_own_images()

    def save_visible_platform_draft(self):
        if (
            not hasattr(self, 'preview_text')
            or not self.product_record_id
        ):
            return
        self.platform_drafts[self.current_platform] = {
            'title': self.platform_title_var.get().strip(),
            'description': self.preview_text.get(
                '1.0', tk.END
            ).strip(),
        }

    def load_platform_draft(self, platform):
        draft = self.platform_drafts.get(platform)
        if not draft:
            return
        self._switching_platform = True
        self.platform_title_var.set(draft['title'])
        self.preview_text.delete('1.0', tk.END)
        self.preview_text.insert('1.0', draft['description'])
        self.preview_text.edit_modified(False)
        self._switching_platform = False
        self.render_live_preview()
        self.update_listing_counters()

    def on_platform_title_changed(self, *args):
        if not self._switching_platform:
            self.update_listing_counters()

    def full_platform_description(self, body=None):
        if body is None:
            body = self.preview_text.get('1.0', tk.END).strip()
        return f"{str(body).rstrip()}\n\n---\n\n{self.legal_clause.strip()}"

    def update_listing_counters(self):
        if not hasattr(self, 'platform_title_var'):
            return
        profile = PLATFORM_PROFILES[self.current_platform]
        title_length = len(self.platform_title_var.get())
        description_length = len(self.full_platform_description())
        self.title_counter_var.set(
            f"{title_length} / {profile.title_limit}"
        )
        self.description_counter_var.set(
            f"{description_length} / {profile.description_limit} "
            f"{TRANSLATIONS[self.language]['characters']}"
        )

    def platform_limit_errors(self, platform=None, title=None, body=None):
        platform = platform or self.current_platform
        profile = PLATFORM_PROFILES[platform]
        title = (
            self.platform_title_var.get().strip()
            if title is None else str(title).strip()
        )
        body = (
            self.preview_text.get('1.0', tk.END).strip()
            if body is None else str(body).strip()
        )
        trans = TRANSLATIONS[self.language]
        errors = []
        if not title:
            errors.append(trans['field_title'])
        if len(title) > profile.title_limit:
            errors.append(
                f"{trans['field_title']} {len(title)}/{profile.title_limit}"
            )
        full = self.full_platform_description(body)
        if len(full) > profile.description_limit:
            errors.append(
                f"{trans['field_description']} "
                f"{len(full)}/{profile.description_limit}"
            )
        return errors

    @staticmethod
    def parse_price(value):
        """Liest deutsche und englische Preisschreibweisen.

        Erkennt „1.234,56“, „1,234.56“, „1234,56“ und „1234.56“. Gibt bei
        unlesbaren Eingaben ``None`` zurück.
        """
        text = re.sub(r'[^\d.,-]', '', str(value or '')).strip()
        if not text:
            return None
        last_comma = text.rfind(',')
        last_dot = text.rfind('.')
        if last_comma > last_dot:
            # Komma steht hinten: es ist das Dezimaltrennzeichen.
            text = text.replace('.', '').replace(',', '.')
        elif last_dot > last_comma:
            text = text.replace(',', '')
        else:
            text = text.replace(',', '').replace('.', '')
        try:
            return float(text)
        except ValueError:
            return None

    @staticmethod
    def format_price(amount):
        """Formatiert einen Betrag in deutscher Schreibweise."""
        return f"{amount:,.2f}".replace(',', '\x00').replace(
            '.', ','
        ).replace('\x00', '.')

    @staticmethod
    def scope_items(scope):
        """Zerlegt eine Lieferumfangs-Eingabe in einzelne Stichpunkte."""
        parts = re.split(r'[;\n]|,(?![^(]*\))', str(scope or ''))
        items = []
        for part in parts:
            clean = part.strip().strip('*•- ').strip()
            clean = re.sub(r'^\[(.*)\]$', r'\1', clean).strip()
            if clean and clean not in items:
                items.append(clean)
        return items

    @staticmethod
    def section_body(body, *titles):
        """Findet einen ``### Titel``-Abschnitt samt seinem Inhalt."""
        names = '|'.join(re.escape(title) for title in titles)
        return re.compile(
            rf'(?m)^###[ \t]+(?:{names})[ \t]*$\n(?P<content>.*?)'
            r'(?=^###[ \t]|\Z)',
            re.DOTALL,
        ).search(body)

    @classmethod
    def replace_section(cls, body, titles, content):
        """Ersetzt den Inhalt eines Abschnitts; ``None`` wenn er fehlt."""
        match = cls.section_body(body, *titles)
        if not match:
            return None
        replacement = f"### {titles[0]}\n\n{content.strip()}\n\n"
        return body[:match.start()] + replacement + body[match.end():]

    @classmethod
    def drop_section(cls, body, *titles):
        match = cls.section_body(body, *titles)
        if not match:
            return body
        return body[:match.start()] + body[match.end():]

    @staticmethod
    def strip_condition_placeholders(body):
        """Entfernt die Auswahlsätze zum Zustand.

        Sobald der Zustand konkret angegeben ist, stehen sie doppelt im Text.
        Erkennbar sind sie am fett gesetzten ``**[…]``; die eckigen Klammern
        im Lieferumfang sind bewusst nicht fett und bleiben erhalten.
        """
        paragraphs = re.split(r'\n\s*\n', body)
        return '\n\n'.join(
            paragraph for paragraph in paragraphs if '**[' not in paragraph
        )

    @staticmethod
    def strip_review_hint(body):
        """Nimmt den Prüfhinweis weg, wenn keine Platzhalter mehr da sind."""
        if '[' in re.sub(r'^\s*\*\(.*?\)\*\s*$', '', body, flags=re.M):
            return body
        return re.sub(
            r'(?m)^\s*\*\((?:Nicht Zutreffendes|Please remove)[^\n]*\)\*\s*$\n?',
            '', body,
        )

    @staticmethod
    def split_closing(body):
        """Trennt den Schlusssatz ab, damit er nicht in einen Abschnitt rutscht.

        Ohne diese Trennung wuerde der letzte ``###``-Abschnitt den Satz beim
        naechsten Uebernehmen mitverschlucken.
        """
        match = re.search(
            r'\n\s*\n((?:Bei Fragen|Feel free)[^\n]*)\s*$', body
        )
        if match:
            return body[:match.start()].rstrip(), match.group(1).strip()
        return body.rstrip(), ''

    def assistant_price_text(self, amount):
        """Formatiert den Preis inklusive VB- beziehungsweise Festpreis-Zusatz."""
        price_type = self.price_type_var.get().strip()
        free = TRANSLATIONS[self.language]['price_type_values'].split('|')[2]
        if price_type == free:
            return price_type
        suffix = f" {price_type}" if price_type else ''
        formatted = (
            f"{amount:,.2f}" if self.language == 'en'
            else self.format_price(amount)
        )
        return f"{formatted} €{suffix}"

    def apply_assistant_details(self):
        if not self.selected_variant or not self.product_record_id:
            return
        self.save_visible_platform_draft()
        trans = TRANSLATIONS[self.language]
        unselected = trans['condition_values'].split('|')[0]
        condition = self.condition_var.get().strip()
        if condition == unselected:
            condition = ''
        scope = self.scope_var.get().strip()
        price = self.asking_price_var.get().strip()
        price_text = ''
        if price:
            amount = self.parse_price(price)
            if amount is None:
                messagebox.showwarning(
                    trans['assistant_frame'],
                    trans['asking_price_label'],
                )
                return
            price_text = self.assistant_price_text(amount)

        items = self.scope_items(scope)
        for platform, draft in self.platform_drafts.items():
            body = self.merge_assistant_details(
                draft['description'], condition, items, price_text
            )
            if (
                PLATFORM_PROFILES[platform].description_limit
                <= COMPACT_DESCRIPTION_LIMIT
            ):
                body = self.compact_draft(body, platform)
            draft['description'] = body
            self.persist_platform_draft(platform, draft)
        self.selected_variant['listing_condition'] = condition
        self.selected_variant['listing_scope'] = scope
        self.selected_variant['asking_price'] = price
        self.selected_variant['price_type'] = self.price_type_var.get()
        self.listing_store.update_product_state(
            self.product_record_id,
            {
                'condition': condition,
                'scope': scope,
                'asking_price': price,
                'price_type': self.price_type_var.get(),
                'price_basis': self.price_basis_var.get(),
            },
        )
        self.load_platform_draft(self.current_platform)
        self.update_listing_completeness()

    def merge_assistant_details(self, body, condition, items, price_text):
        """Führt Assistenten-Angaben in die vorhandenen Abschnitte ein.

        Die Angaben landen bewusst nicht in einem eigenen Anhang: Zustand und
        Lieferumfang stehen bereits als Abschnitt im Entwurf und waeren sonst
        doppelt vorhanden.
        """
        trans = TRANSLATIONS[self.language]
        both = (TRANSLATIONS['de'], TRANSLATIONS['en'])
        condition_titles = [text['section_condition'] for text in both]
        scope_titles = [text['section_scope'] for text in both]
        price_titles = [text['section_price'] for text in both]
        # Frueher wurden alle Angaben an einen eigenen Abschnitt gehaengt;
        # bestehende Entwuerfe werden davon befreit.
        body = self.drop_section(
            body,
            'Angaben zum angebotenen Artikel',
            'Details of the offered item',
        )
        body, closing = self.split_closing(body)

        def apply_section(text, key, titles, content):
            updated = self.replace_section(
                text, [trans[key]] + titles, content
            )
            if updated is not None:
                return updated
            return f"{text.rstrip()}\n\n### {trans[key]}\n\n{content}"

        if condition:
            body = self.strip_condition_placeholders(body)
            body = apply_section(
                body, 'section_condition', condition_titles, condition
            )
        else:
            body = self.drop_section(body, *condition_titles)

        if items:
            body = apply_section(
                body, 'section_scope', scope_titles,
                '\n'.join(f"* {item}" for item in items),
            )

        if price_text:
            body = apply_section(
                body, 'section_price', price_titles, price_text
            )
        else:
            body = self.drop_section(body, *price_titles)

        body = self.strip_review_hint(body)
        if closing:
            body = f"{body.rstrip()}\n\n{closing}"
        return re.sub(r'\n{3,}', '\n\n', body).strip()

    def persist_platform_draft(self, platform, draft):
        self.listing_store.save_draft(
            self.product_record_id,
            platform,
            draft['title'],
            self.full_platform_description(draft['description']),
        )

    def update_listing_completeness(self):
        if not hasattr(self, 'completeness_var'):
            return
        trans = TRANSLATIONS[self.language]
        missing = []
        unselected = trans['condition_values'].split('|')[0]
        if self.condition_var.get() in ('', unselected):
            missing.append(trans['condition_label'].rstrip(':'))
        if not self.scope_var.get().strip():
            missing.append(trans['scope_label'].rstrip(':'))
        if not self.asking_price_var.get().strip():
            missing.append(trans['asking_price_label'].rstrip(':'))
        if self.product_record_id and self.listing_store.conflicts(
            self.product_record_id
        ):
            missing.append(trans['fact_conflicts'])
        errors = self.platform_limit_errors()
        missing.extend(errors)
        self.update_buyback_state()
        self.completeness_var.set(
            f"{trans['completeness_missing']} {', '.join(missing)}"
            if missing else trans['completeness_ready']
        )

    def update_price_summary(self):
        if not hasattr(self, 'price_summary_var'):
            return
        if not self.product_record_id:
            self.price_summary_var.set('')
            return
        kind = self.price_basis_var.get()
        summary = self.listing_store.price_summary(
            self.product_record_id, kind
        )
        trans = TRANSLATIONS[self.language]
        if not summary.get('count'):
            self.price_summary_var.set(
                trans['price_active_notice']
                if kind == 'active'
                else 'Keine verlässlichen verkauften Preise verfügbar.'
            )
            return
        label = (
            trans['price_active'] if kind == 'active'
            else trans['price_sold']
        )
        self.price_summary_var.set(
            f"{label}: {summary['count']} Treffer · "
            f"Median {summary['median']:.2f} € · "
            f"{summary['minimum']:.2f}–{summary['maximum']:.2f} €"
            + (
                f" · {trans['price_active_notice']}"
                if kind == 'active' else ''
            )
        )

    def on_price_basis_changed(self, event=None):
        trans = TRANSLATIONS[self.language]
        self.price_basis_var.set(
            'sold'
            if self.price_basis_display_var.get() == trans['price_sold']
            else 'active'
        )
        self.update_price_summary()

    def product_identifier(self):
        """Liefert ISBN oder EAN des Beitrags, sofern vorhanden."""
        variant = self.selected_variant or {}
        for key in ('isbn', 'ean', 'gtin'):
            value = variant.get(key)
            if isinstance(value, (list, tuple)):
                value = value[0] if value else ''
            if value:
                return re.sub(r'[^0-9Xx]', '', str(value)).upper()
        # Sonst die Eingabe selbst, falls dort eine Nummer steht.
        typed = re.sub(r'[^0-9Xx]', '', self.search_var.get()).upper()
        if self.normalize_isbn(self.search_var.get()) or len(typed) == 13:
            return typed
        return ''

    def buyback_query(self):
        """Freitext für Dienste ohne Kennungssuche: Kennung oder Name."""
        return self.product_identifier() or (
            self.selected_variant or {}
        ).get('name', '').strip()

    def update_buyback_state(self):
        """Nutzbar, sobald irgendetwas Nachschlagbares vorliegt."""
        if not hasattr(self, 'buyback_button'):
            return
        self.buyback_button.config(
            state=tk.NORMAL if self.buyback_query() else tk.DISABLED
        )

    def show_buyback_menu(self):
        identifier = self.product_identifier()
        if not self.buyback_query():
            return
        menu = tk.Menu(self.root, tearoff=0)
        for name, template, needs in BUYBACK_SERVICES:
            menu.add_command(
                label=name,
                # Ohne ISBN/EAN ist die Kennungssuche nicht aufrufbar.
                state=tk.NORMAL if (needs != 'identifier' or identifier)
                else tk.DISABLED,
                command=lambda t=template, n=needs, l=name:
                    self.open_buyback_service(t, n, l),
            )
        try:
            menu.tk_popup(
                self.buyback_button.winfo_rootx(),
                self.buyback_button.winfo_rooty()
                + self.buyback_button.winfo_height(),
            )
        finally:
            menu.grab_release()

    def open_buyback_service(self, template, needs, name=''):
        """Öffnet den Ankaufsdienst mit der passenden Adresse.

        Bewusst kein Abruf: die Dienste bieten keine offene Schnittstelle und
        weisen automatisierte Zugriffe mit HTTP 403 ab. Der Preis wird also
        gelesen, nicht ausgewertet.
        """
        trans = TRANSLATIONS[self.language]
        if needs == 'identifier':
            value = self.product_identifier()
            if not value:
                return
            webbrowser.open(template.format(value=urllib.parse.quote(value)))
            self.status_var.set(f"{trans['buyback_opened']} {name}")
            return
        if needs == 'query':
            value = self.buyback_query()
            if not value:
                return
            webbrowser.open(
                template.format(value=urllib.parse.quote_plus(value))
            )
            self.status_var.set(f"{trans['buyback_opened']} {name}")
            return
        # Ohne belegtes Adressmuster bleibt das Einfügen von Hand.
        value = self.buyback_query()
        if value:
            self.root.clipboard_clear()
            self.root.clipboard_append(value)
        webbrowser.open(template)
        self.status_var.set(f"{trans['buyback_copied']} {value}")

    def open_fact_conflicts(self):
        trans = TRANSLATIONS[self.language]
        conflicts = (
            self.listing_store.conflicts(self.product_record_id)
            if self.product_record_id else {}
        )
        if not conflicts:
            messagebox.showinfo(
                trans['fact_conflicts'], trans['no_fact_conflicts']
            )
            return
        window = tk.Toplevel(self.root)
        window.title(trans['fact_conflicts'])
        window.geometry('720x360')
        tree = ttk.Treeview(
            window, columns=('key', 'value', 'source'),
            show='headings', selectmode='browse'
        )
        for column, text, width in (
            ('key', 'Angabe', 180),
            ('value', 'Wert', 340),
            ('source', 'Quelle', 160),
        ):
            tree.heading(column, text=text)
            tree.column(column, width=width)
        for key, values in conflicts.items():
            for fact in values:
                tree.insert(
                    '', tk.END,
                    values=(key, fact['value'], fact['source'])
                )
        tree.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        def confirm():
            selection = tree.selection()
            if not selection:
                return
            key, value, _ = tree.item(
                selection[0], 'values'
            )
            self.listing_store.confirm_fact(
                self.product_record_id, key, value
            )
            window.destroy()
            self.update_listing_completeness()

        ttk.Button(
            window, text=trans['confirm_fact'], command=confirm
        ).pack(pady=(0, 8))

    def reset_ebay_data_check(self):
        """Leert die eBay-Prüfung, ohne andere Tab-Zustände zu verändern."""
        self._ebay_metadata_generation += 1
        self.ebay_categories = []
        self.ebay_aspects = []
        self.ebay_aspect_values = {}
        if not hasattr(self, 'ebay_check_frame'):
            return
        self.ebay_check_frame.pack_forget()
        self.ebay_category_var.set('')
        self.ebay_category_combo.configure(values=())
        for item in self.ebay_aspect_tree.get_children():
            self.ebay_aspect_tree.delete(item)
        self.ebay_aspect_value_var.set('')
        self.ebay_aspect_value_combo.configure(values=())
        self.ebay_check_status_var.set(
            TRANSLATIONS[self.language]['ebay_check_hint']
        )

    def start_ebay_data_check(self, variant):
        """Lädt Kategorie, Produktdetails und Merkmale im Hintergrund."""
        if not self.get_secret('ebay_client_id') or not self.get_secret(
            'ebay_client_secret'
        ):
            return
        self._ebay_metadata_generation += 1
        generation = self._ebay_metadata_generation
        trans = TRANSLATIONS[self.language]
        self.ebay_check_frame.pack(
            fill=tk.X, padx=10, pady=(0, 10),
            before=self.preview_frame,
        )
        self.ebay_check_status_var.set(trans['ebay_category_loading'])
        self.ebay_categories = []
        self.ebay_aspects = []
        self.ebay_aspect_values = dict(variant.get('ebay_aspect_values') or {})
        for item in self.ebay_aspect_tree.get_children():
            self.ebay_aspect_tree.delete(item)

        def worker():
            try:
                details = {}
                item_id = variant.get('ebay_item_id')
                if item_id:
                    try:
                        details = self.get_ebay_item_details(item_id)
                    except Exception:
                        details = {}
                category_id = (
                    details.get('category_id')
                    or variant.get('ebay_category_id')
                )
                categories = []
                if category_id:
                    categories.append({
                        'id': str(category_id),
                        'name': (
                            details.get('category_name')
                            or variant.get('ebay_category_name')
                            or str(category_id)
                        ),
                        'path': details.get('category_path', ''),
                    })
                if self.ebay_environment == 'production':
                    suggestions = self.get_ebay_category_suggestions(
                        variant.get('name', '')
                    )
                    known = {entry['id'] for entry in categories}
                    categories.extend(
                        entry for entry in suggestions
                        if entry['id'] not in known
                    )
                aspects = (
                    self.get_ebay_item_aspects(categories[0]['id'])
                    if categories else []
                )
            except Exception as exc:
                self.root.after(
                    0,
                    lambda error=str(exc): self.apply_ebay_data_error(
                        variant, generation, error
                    ),
                )
                return
            self.root.after(
                0,
                lambda: self.apply_ebay_data_check(
                    variant, generation, details, categories, aspects
                ),
            )

        threading.Thread(target=worker, daemon=True).start()

    def apply_ebay_data_error(self, variant, generation, error):
        if (
            self._closed or self.selected_variant is not variant
            or generation != self._ebay_metadata_generation
        ):
            return
        self.ebay_check_status_var.set(error)

    def apply_ebay_data_check(
        self, variant, generation, details, categories, aspects
    ):
        if (
            self._closed or self.selected_variant is not variant
            or generation != self._ebay_metadata_generation
        ):
            return
        self.ebay_categories = categories
        if details:
            variant.update({
                key: value for key, value in details.items()
                if value not in (None, '', [], {})
            })
            self.ebay_aspect_values.update(details.get('aspect_values') or {})
            variant['ebay_aspect_values'] = dict(self.ebay_aspect_values)
            if self.product_record_id:
                for key, value in self.ebay_aspect_values.items():
                    self.listing_store.add_fact(
                        self.product_record_id, key, value, 'eBay',
                        variant.get('source_url', ''),
                    )
            image_urls = details.get('image_urls') or []
            if image_urls:
                variant['image_urls'] = image_urls
                self.load_product_image_async(
                    variant, variant.get('source_url', '')
                )
            product_facts = details.get('ebay_product_facts') or []
            if product_facts and self.host_has_label(
                variant.get('source_url', ''), 'ebay'
            ):
                description = '\n'.join(product_facts)
                variant['description'] = {
                    'de': description, 'en': description
                }
                draft = self.generator.build_sales_draft(
                    variant['name'], description, self.language
                )
                self.preview_text.delete('1.0', tk.END)
                self.preview_text.insert('1.0', draft)
        labels = [self.ebay_category_display(entry) for entry in categories]
        self.ebay_category_combo.configure(values=labels)
        if labels:
            self.ebay_category_combo.current(0)
            variant['ebay_category_id'] = categories[0]['id']
            variant['ebay_category_name'] = categories[0]['name']
            self.populate_ebay_aspects(aspects)
        else:
            self.ebay_category_var.set('')
            self.ebay_check_status_var.set(
                TRANSLATIONS[self.language]['ebay_category_unavailable']
            )

    @staticmethod
    def ebay_category_display(category):
        path = category.get('path', '').strip()
        name = category.get('name', '').strip()
        category_id = category.get('id', '')
        return f"{path or name} ({category_id})"

    def on_ebay_category_selected(self, event=None):
        index = self.ebay_category_combo.current()
        if not (0 <= index < len(self.ebay_categories)):
            return
        category = self.ebay_categories[index]
        variant = self.selected_variant
        if variant is None:
            return
        variant['ebay_category_id'] = category['id']
        variant['ebay_category_name'] = category['name']
        self._ebay_metadata_generation += 1
        generation = self._ebay_metadata_generation
        self.ebay_check_status_var.set(
            TRANSLATIONS[self.language]['details_loading']
        )

        def worker():
            try:
                aspects = self.get_ebay_item_aspects(category['id'])
                error = ''
            except Exception as exc:
                aspects, error = [], str(exc)
            self.root.after(
                0,
                lambda: (
                    self.apply_ebay_data_error(variant, generation, error)
                    if error else self.apply_selected_ebay_aspects(
                        variant, generation, aspects
                    )
                ),
            )

        threading.Thread(target=worker, daemon=True).start()

    def apply_selected_ebay_aspects(self, variant, generation, aspects):
        if (
            self.selected_variant is not variant
            or generation != self._ebay_metadata_generation
        ):
            return
        self.populate_ebay_aspects(aspects)

    def populate_ebay_aspects(self, aspects):
        self.ebay_aspects = aspects
        for item in self.ebay_aspect_tree.get_children():
            self.ebay_aspect_tree.delete(item)
        trans = TRANSLATIONS[self.language]
        for index, aspect in enumerate(aspects):
            name = aspect['name']
            value = self.ebay_aspect_values.get(name, '')
            requirement = (
                trans['ebay_required'] if aspect['required']
                else trans['ebay_recommended'] if aspect['recommended']
                else trans['ebay_optional']
            )
            status = (
                trans['ebay_complete'] if value
                else trans['ebay_publish_missing'] if aspect['required'] else ''
            )
            self.ebay_aspect_tree.insert(
                '', tk.END, iid=str(index),
                values=(name, value, requirement, status),
            )
        self.update_ebay_completeness()

    def on_ebay_aspect_selected(self, event=None):
        selection = self.ebay_aspect_tree.selection()
        if not selection:
            return
        index = int(selection[0])
        if not (0 <= index < len(self.ebay_aspects)):
            return
        aspect = self.ebay_aspects[index]
        self.ebay_aspect_value_var.set(
            self.ebay_aspect_values.get(aspect['name'], '')
        )
        self.ebay_aspect_value_combo.configure(
            values=aspect.get('values', ())
        )

    def apply_ebay_aspect_value(self):
        selection = self.ebay_aspect_tree.selection()
        if not selection:
            return
        index = int(selection[0])
        if not (0 <= index < len(self.ebay_aspects)):
            return
        aspect = self.ebay_aspects[index]
        value = self.normalize_text(self.ebay_aspect_value_var.get())
        if value:
            self.ebay_aspect_values[aspect['name']] = value
        else:
            self.ebay_aspect_values.pop(aspect['name'], None)
        if self.selected_variant is not None:
            self.selected_variant['ebay_aspect_values'] = dict(
                self.ebay_aspect_values
            )
        self.populate_ebay_aspects(self.ebay_aspects)
        self.ebay_aspect_tree.selection_set(str(index))

    @staticmethod
    def missing_required_ebay_aspects(aspects, values):
        return [
            aspect['name'] for aspect in aspects
            if aspect.get('required') and not str(values.get(
                aspect['name'], ''
            )).strip()
        ]

    def update_ebay_completeness(self):
        trans = TRANSLATIONS[self.language]
        missing = self.missing_required_ebay_aspects(
            self.ebay_aspects, self.ebay_aspect_values
        )
        if missing:
            self.ebay_check_status_var.set(
                f"{trans['ebay_check_incomplete']} {', '.join(missing)}"
            )
        elif self.ebay_aspects:
            self.ebay_check_status_var.set(trans['ebay_check_ready'])
        elif self.ebay_environment == 'sandbox':
            self.ebay_check_status_var.set(
                trans['ebay_sandbox_taxonomy_hint']
            )
        else:
            self.ebay_check_status_var.set(trans['ebay_check_hint'])

    def on_draft_modified(self, *args):
        if not self.preview_text.edit_modified():
            return
        self.preview_text.edit_modified(False)
        if not self._switching_platform and self.product_record_id:
            self.save_visible_platform_draft()
            self.update_listing_counters()
            self.update_listing_completeness()
        self.render_live_preview()

    def render_live_preview(self):
        """Rendert den editierbaren Markdown-Entwurf als Verkaufsansicht."""
        if not hasattr(self, 'rendered_preview'):
            return
        draft = self.preview_text.get('1.0', tk.END).strip()
        self.rendered_preview.config(state=tk.NORMAL)
        self.rendered_preview.delete('1.0', tk.END)
        for line in draft.splitlines():
            stripped = line.strip()
            tag = None
            if stripped.startswith('### '):
                text = stripped[4:]
                tag = 'heading'
            elif stripped.startswith('**') and stripped.endswith('**'):
                text = stripped[2:-2]
                tag = 'title'
            elif re.match(r'^[*\-]\s+', stripped):
                text = "• " + re.sub(r'^[*\-]\s+', '', stripped)
            else:
                text = stripped
            text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
            text = re.sub(r'\*(.*?)\*', r'\1', text)
            self.rendered_preview.insert(
                tk.END, f"{text}\n", tag if tag else ()
            )
        if draft:
            self.rendered_preview.insert(tk.END, "\n──────────\n\n")
        self.rendered_preview.insert(
            tk.END, self.legal_clause, 'legal'
        )
        self.rendered_preview.config(state=tk.DISABLED)

    def reset_product_image(self):
        if not hasattr(self, 'product_image_label'):
            return
        self._image_generation += 1
        self._product_photo = None
        self._product_image_original = None
        self._product_image_urls = []
        self._product_image_index = -1
        self._product_image_current_url = ''
        self.image_counter_var.set("0 / 0")
        self.previous_image_button.config(state=tk.DISABLED)
        self.next_image_button.config(state=tk.DISABLED)
        self.save_image_button.config(state=tk.DISABLED)
        self.product_image_label.config(
            image='',
            text=TRANSLATIONS[self.language]['no_product_image'],
        )

    def load_product_image_async(self, variant, source_url):
        """Lädt das Cover der ausgewählten Produktseite ohne GUI-Blockade."""
        if Image is None or ImageTk is None:
            return
        self._image_generation += 1
        generation = self._image_generation
        self._product_photo = None
        self.product_image_label.config(
            image='', text=TRANSLATIONS[self.language]['no_product_image']
        )

        def worker():
            try:
                image_urls = list(variant.get('image_urls') or [])
                if variant.get('image_url'):
                    image_urls.insert(0, variant['image_url'])
                if not image_urls:
                    html = self.fetch_url(source_url)
                    image_urls = self.extract_product_image_urls(
                        html, source_url
                    )
                image_urls = list(dict.fromkeys(
                    url for url in image_urls if url
                ))[:20]
                if not image_urls:
                    return
                variant['image_urls'] = image_urls
                image = self.decode_product_image(
                    self.fetch_binary(image_urls[0])
                )
            except Exception:
                return
            self.root.after(
                0,
                lambda: self.apply_product_image(
                    variant, generation, image, image_urls, 0
                ),
            )

        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def decode_product_image(image_data):
        image = Image.open(io.BytesIO(image_data))
        if image.width * image.height > 50_000_000:
            raise ValueError("Produktbild überschreitet 50 Megapixel")
        if image.mode not in ('RGB', 'RGBA'):
            return image.convert('RGB')
        return image.copy()

    def apply_product_image(
        self, variant, generation, image, image_urls=None, image_index=0
    ):
        if (
            self._closed
            or self.selected_variant is not variant
            or generation != self._image_generation
        ):
            return
        self._product_image_original = image
        if image_urls is not None:
            self._product_image_urls = list(image_urls)
        self._product_image_index = image_index
        if self._product_image_urls:
            self._product_image_current_url = self._product_image_urls[
                image_index
            ]
        count = len(self._product_image_urls)
        self.image_counter_var.set(
            f"{image_index + 1} / {count}" if count else "0 / 0"
        )
        navigation_state = tk.NORMAL if count > 1 else tk.DISABLED
        self.previous_image_button.config(state=navigation_state)
        self.next_image_button.config(state=navigation_state)
        self.save_image_button.config(
            state=tk.NORMAL if self._product_image_current_url else tk.DISABLED
        )
        self.render_responsive_cover()

    def show_relative_product_image(self, offset):
        count = len(self._product_image_urls)
        if count < 2 or self.selected_variant is None:
            return
        target_index = (self._product_image_index + offset) % count
        target_url = self._product_image_urls[target_index]
        variant = self.selected_variant
        self._image_generation += 1
        generation = self._image_generation
        self.previous_image_button.config(state=tk.DISABLED)
        self.next_image_button.config(state=tk.DISABLED)

        def worker():
            try:
                image = self.decode_product_image(
                    self.fetch_binary(target_url)
                )
            except Exception:
                return
            self.root.after(
                0,
                lambda: self.apply_product_image(
                    variant, generation, image,
                    self._product_image_urls, target_index
                ),
            )

        threading.Thread(target=worker, daemon=True).start()

    def own_image_limit(self):
        """Bildgrenze der aktuell bearbeiteten Plattform."""
        return PLATFORM_IMAGE_LIMITS.get(self.current_platform, 20)

    def refresh_own_images(self):
        """Liest die eigenen Fotos aus der Produktakte in die Liste."""
        if not hasattr(self, 'own_images_list'):
            return
        trans = TRANSLATIONS[self.language]
        self.own_images = (
            self.listing_store.images(self.product_record_id, own_only=True)
            if self.product_record_id else []
        )
        selection = self.own_images_list.curselection()
        self.own_images_list.delete(0, tk.END)
        for position, image in enumerate(self.own_images, 1):
            name = Path(image['path']).name
            missing = '' if Path(image['path']).is_file() else ' ⚠'
            self.own_images_list.insert(tk.END, f"{position:02d}  {name}{missing}")
        if selection and selection[0] < len(self.own_images):
            self.own_images_list.selection_set(selection[0])
        limit = self.own_image_limit()
        hint = [
            trans['own_images_count'].format(
                count=len(self.own_images), limit=limit,
                platform=PLATFORM_PROFILES[self.current_platform].label_de,
            )
        ]
        if self.own_images:
            hint.append(trans['own_images_replace_note'])
        hint.append(
            trans['own_images_hint'] if Image is not None
            else trans['own_images_no_pillow']
        )
        self.own_images_hint_var.set(' '.join(hint))

    def on_own_image_selected(self, event=None):
        """Zeigt das ausgewählte eigene Foto in der Cover-Spalte."""
        selection = self.own_images_list.curselection()
        if not selection or selection[0] >= len(self.own_images):
            return
        path = Path(self.own_images[selection[0]]['path'])
        if not path.is_file() or Image is None:
            return
        try:
            with Image.open(path) as opened:
                image = ImageOps.exif_transpose(opened).copy()
        except Exception:
            return
        self._image_generation += 1
        self._product_image_original = image
        self._product_image_current_url = ''
        self.render_responsive_cover()

    def add_own_images(self):
        """Übernimmt eigene Fotos als Dateipfade in die Produktakte."""
        trans = TRANSLATIONS[self.language]
        if not self.product_record_id:
            messagebox.showwarning(
                trans['no_selection'], trans['no_selection']
            )
            return
        limit = self.own_image_limit()
        if len(self.own_images) >= limit:
            messagebox.showwarning(
                trans['own_images_frame'],
                trans['own_images_limit'].format(
                    limit=limit,
                    platform=PLATFORM_PROFILES[self.current_platform].label_de,
                ),
            )
            return
        patterns = ' '.join(f'*{suffix}' for suffix in OWN_IMAGE_SUFFIXES)
        selected = filedialog.askopenfilenames(
            title=trans['own_images_title'],
            initialdir=self.save_path,
            filetypes=[
                (trans['own_images_frame'], patterns),
                ("Alle Dateien", "*.*"),
            ],
        )
        free_slots = limit - len(self.own_images)
        for path in list(selected)[:free_slots]:
            self.listing_store.add_image(
                self.product_record_id, path, is_own=True
            )
        if len(selected) > free_slots:
            messagebox.showwarning(
                trans['own_images_frame'],
                trans['own_images_limit'].format(
                    limit=limit,
                    platform=PLATFORM_PROFILES[self.current_platform].label_de,
                ),
            )
        self.refresh_own_images()

    def move_own_image(self, offset):
        """Verschiebt ein Foto; das erste ist das Hauptbild."""
        selection = self.own_images_list.curselection()
        if not selection:
            return
        index = selection[0]
        target = index + offset
        if not (0 <= index < len(self.own_images)) or not (
            0 <= target < len(self.own_images)
        ):
            return
        order = [image['id'] for image in self.own_images]
        order[index], order[target] = order[target], order[index]
        self.listing_store.reorder_images(order)
        self.refresh_own_images()
        self.own_images_list.selection_clear(0, tk.END)
        self.own_images_list.selection_set(target)

    def remove_own_image(self):
        """Entfernt nur den Verweis, niemals die Originaldatei."""
        selection = self.own_images_list.curselection()
        if not selection or selection[0] >= len(self.own_images):
            return
        self.listing_store.remove_image(self.own_images[selection[0]]['id'])
        self.refresh_own_images()

    def save_current_product_image(self):
        url = self._product_image_current_url
        if not url:
            return
        extension = Path(urllib.parse.urlparse(url).path).suffix.lower()
        if extension not in ('.jpg', '.jpeg', '.png', '.webp'):
            extension = '.jpg'
        product_name = (
            self.selected_variant.get('name', 'Produktbild')
            if self.selected_variant else 'Produktbild'
        )
        safe_name = re.sub(r'[<>:"/\\|?*]+', '_', product_name).strip(' .')
        image_number = max(1, self._product_image_index + 1)
        filename = filedialog.asksaveasfilename(
            title=TRANSLATIONS[self.language]['save_image_title'],
            initialdir=self.save_path,
            initialfile=(
                f"{safe_name or 'Produktbild'}_{image_number:02d}{extension}"
            ),
            defaultextension=extension,
            filetypes=[
                ("Bilddateien", "*.jpg *.jpeg *.png *.webp"),
                ("Alle Dateien", "*.*"),
            ],
        )
        if not filename:
            return
        try:
            image_data = self.fetch_binary(url)
            Path(filename).write_bytes(image_data)
        except Exception as exc:
            messagebox.showerror(
                TRANSLATIONS[self.language]['save_image_title'], str(exc)
            )
            return
        self.status_var.set(
            f"{TRANSLATIONS[self.language]['image_saved']} {filename}"
        )

    def on_cover_panel_resized(self, event=None):
        """Skaliert das Bild verzögert mit der veränderbaren Cover-Spalte."""
        if self._cover_resize_after_id is not None:
            try:
                self.root.after_cancel(self._cover_resize_after_id)
            except tk.TclError:
                pass
        self._cover_resize_after_id = self.root.after(
            80, self.render_responsive_cover
        )

    def render_responsive_cover(self):
        self._cover_resize_after_id = None
        image = self._product_image_original
        if image is None or ImageTk is None:
            return
        available_width = max(80, self.cover_panel.winfo_width() - 16)
        available_height = max(
            60, self.product_image_label.winfo_height() - 16
        )
        width, height = image.size
        if width <= 0 or height <= 0:
            return
        scale = min(available_width / width, available_height / height)
        target = (
            max(1, round(width * scale)),
            max(1, round(height * scale)),
        )
        resized = image.resize(target, Image.Resampling.LANCZOS)
        photo = ImageTk.PhotoImage(resized)
        self._product_photo = photo
        self.product_image_label.config(image=photo, text='')

    @staticmethod
    def extract_product_image_url(html, page_url):
        def normalized_url(value):
            value = html_lib.unescape(value).replace('\\_', '_')
            if ProductGeneratorGUI.host_is(value, 'm.media-amazon.com'):
                value = re.sub(
                    r'\.\*([A-Z]{2}\d+)\*\.', r'._\1_.', value
                )
            return urllib.parse.urljoin(page_url, value)

        landing_tag = re.search(
            r'<img\b[^>]*(?:id=["\']landingImage["\']|'
            r'data-a-image-name=["\']landingImage["\'])[^>]*>',
            html,
            re.IGNORECASE | re.DOTALL,
        )
        if landing_tag:
            tag = landing_tag.group(0)
            high_resolution = re.search(
                r'data-old-hires=["\']([^"\']+)["\']',
                tag,
                re.IGNORECASE,
            )
            if high_resolution and high_resolution.group(1).strip():
                return normalized_url(high_resolution.group(1).strip())
            dynamic = re.search(
                r'data-a-dynamic-image=["\']([^"\']+)["\']',
                tag,
                re.IGNORECASE,
            )
            if dynamic:
                dynamic_value = html_lib.unescape(dynamic.group(1))
                candidates = re.findall(
                    r'https?://[^"\\\s]+?\.(?:jpg|jpeg|png|webp)',
                    dynamic_value,
                    re.IGNORECASE,
                )
                if candidates:
                    return normalized_url(max(
                        candidates,
                        key=lambda value: max(
                            [
                                int(number)
                                for number in re.findall(
                                    r'(?:SL|SX|SY)(\d+)', value
                                )
                            ] or [0]
                        ),
                    ))
            source = re.search(
                r'\bsrc=["\']([^"\']+)["\']', tag, re.IGNORECASE
            )
            if source:
                return normalized_url(source.group(1))

        patterns = (
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
            r'id=["\']landingImage["\'][^>]+data-old-hires=["\']([^"\']+)',
            r'id=["\']landingImage["\'][^>]+src=["\']([^"\']+)',
            r'<img[^>]+src=["\']([^"\']*responsive-image[^"\']+)["\'][^>]+'
            r'class=["\'][^"\']*scaled_m04',
            r'(https?://pictures\.abebooks\.com/isbn/'
            r'[^"\']+\.(?:jpg|jpeg|png|webp))',
        )
        for pattern in patterns:
            match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
            if match:
                return normalized_url(match.group(1))
        return ''

    @classmethod
    def extract_product_image_urls(cls, html, page_url):
        """Extrahiert Hauptbild und Amazon-Galeriebilder in hoher Auflösung."""
        primary = cls.extract_product_image_url(html, page_url)
        urls = [primary] if primary else []
        if not cls.host_has_label(page_url, 'amazon'):
            return urls

        decoded = html_lib.unescape(html).replace('\\/', '/')
        color_start = decoded.casefold().find('colorimages')
        gallery_section = (
            decoded[color_start:color_start + 300_000]
            if color_start >= 0 else decoded
        )
        candidates = []
        initial_label = re.search(
            r'["\']initial["\']\s*:', gallery_section, re.IGNORECASE
        )
        color_to_asin = re.search(
            r'["\']colorToAsin["\']\s*:', gallery_section, re.IGNORECASE
        )
        if initial_label and color_to_asin:
            array_start = gallery_section.find('[', initial_label.end())
            array_end = gallery_section.rfind(
                ']', array_start, color_to_asin.start()
            )
            try:
                gallery_items = json.loads(
                    gallery_section[array_start:array_end + 1]
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                gallery_items = []
            for item in gallery_items:
                if not isinstance(item, dict):
                    continue
                best_url = item.get('hiRes') or item.get('large')
                if not best_url and isinstance(item.get('main'), dict):
                    best_url = max(
                        item['main'],
                        key=lambda url: max(
                            [int(number) for number in re.findall(
                                r'(?:SL|SX|SY)(\d+)', url
                            )] or [0]
                        ),
                        default='',
                    )
                if best_url:
                    candidates.append(best_url)
        # Thumbnail, large und hiRes können unterschiedliche Amazon-IDs haben.
        # Pro Galerieobjekt zählt deshalb ausschließlich die beste Variante.
        if not candidates:
            for image_object in re.findall(
                r'\{[^{}]{0,8000}\}', gallery_section, re.DOTALL
            ):
                best_url = ''
                for field in ('hiRes', 'large', 'mainUrl'):
                    match = re.search(
                        rf'["\']{field}["\']\s*:\s*["\']'
                        r'(https?://m\.media-amazon\.com/images/I/'
                        r'[^"\']+?\.(?:jpg|jpeg|png|webp))',
                        image_object,
                        re.IGNORECASE,
                    )
                    if match:
                        best_url = match.group(1)
                        break
                if best_url:
                    candidates.append(best_url)
        if not candidates:
            candidates = re.findall(
                r'(https?://m\.media-amazon\.com/images/I/'
                r'[^"\'\s]+?\.(?:jpg|jpeg|png|webp))',
                gallery_section,
                re.IGNORECASE,
            )

        seen_image_ids = set()
        normalized_urls = []
        for url in (candidates or urls):
            url = html_lib.unescape(url).replace('\\_', '_')
            url = re.sub(r'\.\*([A-Z]{2}\d+)\*\.', r'._\1_.', url)
            url = re.sub(
                r'\._[^./]+_\.(?=(?:jpg|jpeg|png|webp)(?:$|\?))',
                '._SL1500_.',
                url,
                flags=re.IGNORECASE,
            )
            image_id = re.search(
                r'/images/I/([^./]+)', url, re.IGNORECASE
            )
            identity = image_id.group(1) if image_id else url.split('?', 1)[0]
            if identity in seen_image_ids:
                continue
            seen_image_ids.add(identity)
            normalized_urls.append(url)
            if len(normalized_urls) >= 20:
                break
        return normalized_urls

    def fetch_binary(self, url):
        self.validate_remote_url(url)
        headers = {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/138.0.0.0 Safari/537.36'
            ),
            'Accept': 'image/avif,image/webp,image/png,image/jpeg,*/*;q=0.8',
            'Referer': urllib.parse.urlsplit(url)._replace(
                path='/', query='', fragment=''
            ).geturl(),
        }
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=20) as response:
            self.validate_remote_url(response.geturl())
            declared = int(response.headers.get('Content-Length') or 0)
            if declared > MAX_IMAGE_RESPONSE_BYTES:
                raise ValueError("Produktbild ist zu groß")
            data = response.read(MAX_IMAGE_RESPONSE_BYTES + 1)
            if len(data) > MAX_IMAGE_RESPONSE_BYTES:
                raise ValueError("Produktbild ist zu groß")
            return data
    
    def change_save_path(self):
        """Öffnet Dialog zur Pfadauswahl"""
        trans = TRANSLATIONS[self.language]
        folder = filedialog.askdirectory(
            title=trans['change_path'],
            initialdir=self.save_path
        )
        
        if folder:
            self.save_path = folder
            self.path_label.config(text=self.save_path)
            self.status_var.set(f"{trans['path_label']} {self.save_path}")
            self.save_config()

    def set_default_save_path(self):
        trans = TRANSLATIONS[self.language]
        default_path = Path(__file__).resolve().parent / "product_listings"
        default_path.mkdir(parents=True, exist_ok=True)
        self.save_path = str(default_path)
        self.path_label.config(text=self.save_path)
        self.status_var.set(trans['default_save_path_notice'])
        self.save_config()

    def on_language_changed(self, value):
        if value in TRANSLATIONS:
            previous_language = self.language
            self.language = value
            self.save_config()
            self.update_ui_language(previous_language)
            if self.language_callback:
                self.language_callback(value)
            if self.selected_variant is not None:
                self.on_variant_selected()

    def on_font_size_changed(self):
        try:
            font_size = int(self.font_size_var.get())
            self.set_font_size(font_size)
            self.font_size_spinbox.config(textvariable=self.font_size_var)
            self.path_label.config(font=("Segoe UI", max(9, self.font_size - 1)))
            self.variant_listbox.config(font=("Arial", self.font_size))
            self.preview_text.config(font=("Courier", max(10, self.font_size)))
            self.rendered_preview.config(
                font=("Segoe UI", max(10, self.font_size))
            )
            self.legal_text.config(font=("Segoe UI", max(9, self.font_size - 1)))
            for button, _ in self.provider_buttons.values():
                button.config(font=("Segoe UI", max(9, self.font_size)))
            self.save_config()
        except Exception:
            pass

    def set_legal_clause(self, text):
        self.legal_clause = str(text).strip() or WARRANTY_CLAUSE
        self.legal_text.config(state=tk.NORMAL)
        self.legal_text.delete('1.0', tk.END)
        self.legal_text.insert('1.0', self.legal_clause)
        self.legal_text.config(state=tk.DISABLED)
        self.render_live_preview()

    def update_ui_language(self, previous_language=None):
        trans = TRANSLATIONS[self.language]
        previous_language = previous_language or self.language
        if not self.embedded:
            self.root.title(trans['title'])
        self.search_frame.config(text=trans['search_frame'])
        self.search_label_widget.config(text=trans['search_label'])
        self.variant_frame.config(text=trans['variant_frame'])
        self.ebay_check_frame.config(text=trans['ebay_check_frame'])
        self.ebay_category_label.config(text=trans['ebay_category'])
        self.ebay_aspect_apply_button.config(text=trans['ebay_apply_value'])
        for column, label_key in (
            ('aspect', 'ebay_aspect'),
            ('value', 'ebay_value'),
            ('requirement', 'ebay_requirement'),
            ('status', 'ebay_status'),
        ):
            self.ebay_aspect_tree.heading(column, text=trans[label_key])
        self.preview_frame.config(text=trans['preview_frame'])
        self.assistant_frame.config(text=trans['assistant_frame'])
        self.platform_label_widget.config(text=trans['platform_label'])
        self.listing_title_label.config(text=trans['listing_title_label'])
        self.apply_assistant_button.config(text=trans['apply_assistant'])
        self.fact_conflicts_button.config(text=trans['fact_conflicts'])
        self.buyback_button.config(text=trans['buyback_check'])
        self.condition_combo.configure(
            values=trans['condition_values'].split('|')
        )
        # Die gewaehlte Preisart wandert positionsgleich in die neue Sprache.
        previous_types = TRANSLATIONS[
            previous_language
        ]['price_type_values'].split('|')
        price_types = trans['price_type_values'].split('|')
        current_type = self.price_type_var.get()
        self.price_type_combo.configure(values=price_types)
        self.price_type_var.set(
            price_types[previous_types.index(current_type)]
            if current_type in previous_types else price_types[0]
        )
        self.price_basis_combo.configure(
            values=(trans['price_active'], trans['price_sold'])
        )
        self.price_basis_display_var.set(
            trans[
                'price_sold'
                if self.price_basis_var.get() == 'sold'
                else 'price_active'
            ]
        )
        self.editor_frame.config(text=trans['editor_label'])
        self.rendered_frame.config(text=trans['live_preview_label'])
        self.previous_image_button.config(text=trans['previous_image'])
        self.next_image_button.config(text=trans['next_image'])
        self.save_image_button.config(text=trans['save_image'])
        self.own_images_frame.config(text=trans['own_images_frame'])
        self.add_own_images_button.config(text=trans['add_own_images'])
        self.refresh_own_images()
        self.legal_frame.config(text=trans['legal_frame'])
        self.provider_frame.config(text=trans['provider_frame'])
        for button, label_key in self.provider_buttons.values():
            button.config(text=trans[label_key])
        self.variant_context_menu.entryconfig(
            0, label=trans['open_in_new_tab']
        )
        self.options_frame.config(text=trans['options_frame'])
        self.path_label_widget.config(text=trans['path_label'])
        self.language_label_widget.config(text=trans['language_label'])
        self.font_size_label_widget.config(text=trans['font_size_label'])
        self.save_button.config(text=trans['save_button'])
        self.close_button.config(
            text=trans['close_tab'] if self.embedded else trans['close_button']
        )
        self.status_var.set(trans['status_ready'])
        if not self.embedded:
            self.settings_menu.entryconfig(0, label=trans['menu_change_save_path'])
            self.settings_menu.entryconfig(1, label=trans['menu_default_save_path'])
            self.menubar.entryconfig(0, label=trans['menu_settings'])

    @classmethod
    def cache_seconds_for(cls, results, errors):
        """Bestimmt, wie lange ein Suchergebnis gelten darf.

        Ein Fehlschlag ist kein Ergebnis: eine blockierte oder gedrosselte
        Quelle wuerde sonst mitsamt ihrer Fehlermeldung fuer eine Viertelstunde
        festgeschrieben, und selbst ein Neustart liefert nur den leeren
        Eintrag aus der Datenbank zurueck. Unvollstaendige Laeufe halten
        deshalb kurz, leere ueberhaupt nicht.
        """
        if not results:
            return 0
        if errors:
            return cls._search_cache_partial_ttl
        return cls._search_cache_ttl

    @classmethod
    def _cache_lookup(cls, cache_key):
        """Liefert einen noch gültigen Cache-Eintrag oder ``None``."""
        with cls._search_cache_lock:
            entry = cls._search_cache.get(cache_key)
            if not entry:
                return None
            stored_at, results, errors, ttl = entry
            if time.monotonic() - stored_at >= ttl:
                cls._search_cache.pop(cache_key, None)
                return None
            return results, errors

    @classmethod
    def _cache_store(cls, cache_key, results, errors):
        """Speichert ein Ergebnis und entfernt abgelaufene sowie älteste."""
        ttl = cls.cache_seconds_for(results, errors)
        if not ttl:
            # Auch einen frueheren Treffer verwerfen, sonst bliebe ein
            # veraltetes Ergebnis stehen, obwohl gerade nichts gefunden wurde.
            with cls._search_cache_lock:
                cls._search_cache.pop(cache_key, None)
            return
        now = time.monotonic()
        with cls._search_cache_lock:
            cls._search_cache[cache_key] = (now, results, errors, ttl)
            for key, entry in list(cls._search_cache.items()):
                if now - entry[0] >= entry[3]:
                    cls._search_cache.pop(key, None)
            overflow = len(cls._search_cache) - cls._search_cache_max_entries
            if overflow > 0:
                oldest = sorted(
                    cls._search_cache.items(), key=lambda item: item[1][0]
                )[:overflow]
                for key, _entry in oldest:
                    cls._search_cache.pop(key, None)

    def _start_scheduled_online_search(self, search_term, request_id):
        self._search_after_id = None
        if not self.is_search_current(search_term, request_id):
            return
        self.run_online_search(search_term, request_id)

    def is_search_current(self, search_term, request_id):
        return (
            not getattr(self, '_closed', False)
            and request_id == self._search_generation
            and search_term == self.search_var.get().strip()
        )

    def run_online_search(self, search_term, request_id=None):
        trans = TRANSLATIONS[self.language]
        self.status_var.set(trans['online_searching'])
        if request_id is None:
            request_id = self._search_generation
        enabled = {
            name: bool(variable.get())
            for name, variable in self.provider_vars.items()
        }
        threading.Thread(
            target=self._online_search_thread,
            args=(search_term, enabled, request_id),
            daemon=True,
        ).start()

    def _online_search_thread(self, search_term, enabled, request_id):
        cache_key = (
            search_term.casefold().strip(),
            tuple(sorted(name for name, active in enabled.items() if active)),
        )
        cached = self._cache_lookup(cache_key)
        if cached:
            results, errors = cached
            self.root.after(
                0,
                lambda: self.apply_online_results(
                    search_term, request_id, results, errors
                ),
            )
            return
        persistent_key = 'search:' + hashlib.sha256(
            json.dumps(
                cache_key, ensure_ascii=False, sort_keys=True
            ).encode('utf-8')
        ).hexdigest()
        persistent = self.listing_store.cache_get(persistent_key)
        if persistent:
            results = [tuple(item) for item in persistent.get('results', [])]
            errors = list(persistent.get('errors', []))
            self._cache_store(cache_key, results, errors)
            self.root.after(
                0,
                lambda: self.apply_online_results(
                    search_term, request_id, results, errors
                ),
            )
            return
        descriptions = []
        errors = []
        providers = []
        direct_provider = self.provider_for_url(search_term)
        if direct_provider:
            providers.append(direct_provider)
        elif self.normalize_isbn(search_term):
            providers.append(
                ('Deutsche Nationalbibliothek', self.search_dnb_isbn)
            )
            providers.append(('ZVAB ISBN', self.search_zvab_isbn))
            providers.append(('Open Library', self.search_open_library))
            providers.append(('Google Books', self.search_google_books))
        if not direct_provider and enabled.get('web_suggestions'):
            providers.append(('Web', self.search_web_suggestions))
        if not direct_provider and enabled.get('wikipedia'):
            providers.append(('Wikipedia', self.search_wikipedia))
        if not direct_provider and enabled.get('amazon'):
            providers.append(('Amazon', self.search_amazon))
        if not direct_provider and enabled.get('geizhals'):
            providers.append(('Geizhals', self.search_geizhals))
        if not direct_provider and enabled.get('idealo'):
            providers.append(('Idealo', self.search_idealo))
        if not direct_provider and enabled.get('ebay'):
            providers.append(('eBay', self.search_ebay))
        if not direct_provider and enabled.get('kleinanzeigen_agent'):
            providers.append(
                ('Kleinanzeigen Agent', self.search_kleinanzeigen_agent)
            )
        if providers:
            with ThreadPoolExecutor(max_workers=len(providers)) as executor:
                pending = {
                    executor.submit(provider, search_term): provider_name
                    for provider_name, provider in providers
                }
                for future in as_completed(pending):
                    provider_name = pending[future]
                    try:
                        descriptions.extend(future.result())
                    except Exception as exc:
                        errors.append(f"{provider_name}: {exc}")

        unique_results = []
        if descriptions:
            descriptions.sort(
                key=lambda item: self.host_is(
                    item[2], 'suggestqueries.google.com'
                )
            )
            seen = set()
            for title, desc, source_url in descriptions:
                if self.is_unwanted_search_result(title, source_url):
                    continue
                identity = re.sub(r'\W+', ' ', title.lower()).strip()
                if identity and identity not in seen:
                    seen.add(identity)
                    unique_results.append((title, desc, source_url))
            query_words = re.findall(r'[\w-]+', search_term.lower())
            normalized_query = ' '.join(query_words)
            unique_results.sort(
                key=lambda item: (
                    (
                        self.host_is(item[2], 'd-nb.info')
                        or '/ean/' in item[2]
                        or (self.host_is(item[2], 'zvab.com')
                            and '/products/isbn/' in item[2])
                    ),
                    normalized_query in item[0].lower(),
                    sum(word in item[0].lower() for word in query_words),
                    difflib.SequenceMatcher(
                        None, normalized_query, item[0].lower()
                    ).ratio(),
                ),
                reverse=True,
            )
        self._cache_store(cache_key, unique_results, errors)
        persistent_ttl = self.cache_seconds_for(unique_results, errors)
        if persistent_ttl:
            self.listing_store.cache_put(
                persistent_key,
                {'results': unique_results, 'errors': errors},
                persistent_ttl,
            )
        else:
            # Sonst ueberlebt ein Fehlschlag den Neustart und verhindert
            # jeden neuen Versuch bis zum Ablauf.
            self.listing_store.cache_delete(persistent_key)
        self.root.after(
            0,
            lambda: self.apply_online_results(
                search_term, request_id, unique_results, errors
            ),
        )

    def provider_for_url(self, value):
        """Routet Produktlinks ausschließlich an den passenden Importer."""
        try:
            parsed = urllib.parse.urlparse(value.strip())
        except Exception:
            return None
        if parsed.scheme not in ('http', 'https') or not parsed.netloc:
            return None
        if self.host_has_label(value, 'amazon'):
            return ('Amazon-Link', self.search_amazon_url_with_fallback)
        if self.host_has_label(value, 'geizhals'):
            return ('Geizhals-Link', self.search_comparison_url_with_fallback)
        if self.host_has_label(value, 'idealo'):
            return ('Idealo-Link', self.search_comparison_url_with_fallback)
        return ('Produktlink', self.search_direct_product_url)

    @staticmethod
    def product_name_from_url(url):
        path = urllib.parse.unquote(
            urllib.parse.urlparse(url).path
        ).rstrip('/')
        slug = path.rsplit('/', 1)[-1]
        slug = re.sub(r'\.(?:html?|php)$', '', slug, flags=re.IGNORECASE)
        slug = re.sub(r'^\d+[_-]+', '', slug)
        slug = re.sub(r'-v\d+$', '', slug, flags=re.IGNORECASE)
        slug = re.sub(r'[_-]+', ' ', slug)
        return re.sub(r'\s+', ' ', slug).strip()

    @staticmethod
    def model_query_from_slug(url):
        """Leitet aus einem Produkt-Slug einen suchtauglichen Modellnamen ab.

        Der vollstaendige Slug ist als Suchbegriff zu lang und zu werblich.
        Genommen werden die fuehrenden Woerter bis einschliesslich des ersten
        Wortes mit einer Ziffer, denn dort steht die Modellnummer:
        ``QB-X2US3R-Festplattengehaeuse-…`` liefert ``QB-X2US3R``.
        """
        path = urllib.parse.unquote(urllib.parse.urlparse(str(url)).path)
        segments = [segment for segment in path.split('/') if segment]
        candidates = [
            segment for segment in segments
            if segment.lower() not in ('dp', 'gp', 'product', 'b', 'd', 'aw')
            and not segment.isdigit()
            and re.search(r'[^\W\d_]', segment)
            # Die ASIN selbst ist kein Modellname: sie kennt nur Amazon und
            # als Suchbegriff liefert sie anderswo garantiert nichts.
            and not re.fullmatch(r'[A-Z0-9]{10}', segment)
        ]
        if not candidates:
            return ''
        slug = re.sub(
            r'\.(?:html?|php)$', '', max(candidates, key=len),
            flags=re.IGNORECASE,
        )
        words = [word for word in re.split(r'[_-]+', slug) if word]
        if not words:
            return ''
        for index, word in enumerate(words):
            if re.search(r'\d', word) and re.search(r'[^\W\d_]', word):
                return '-'.join(words[:index + 1])
        return ' '.join(words[:4])

    def search_amazon_url_with_fallback(self, url):
        """Weicht bei blockierten Amazon-Produktseiten auf andere Quellen aus.

        Die ASIN kennt nur Amazon; die Modellnummer im Slug finden die
        Vergleichsportale dagegen zuverlaessig.
        """
        try:
            results = self.search_amazon(url)
            if results:
                return results
        except Exception:
            pass
        query = self.model_query_from_slug(url)
        if not query:
            return []
        alternatives = []
        for provider in (
            self.search_geizhals, self.search_idealo, self.search_wikipedia
        ):
            try:
                alternatives.extend(provider(query))
            except Exception:
                continue
        return self.merge_provider_results([alternatives])

    def search_comparison_url_with_fallback(self, url):
        """Nutzt bei blockierten Preisportalen alternative Produktquellen."""
        is_idealo = self.host_has_label(url, 'idealo')
        primary = self.search_idealo if is_idealo else self.search_geizhals
        try:
            results = primary(url)
            if results:
                return results
        except Exception:
            pass
        query = self.product_name_from_url(url)
        if not query:
            return []
        alternatives = []
        providers = (
            (self.search_geizhals,) if is_idealo else ()
        ) + (self.search_amazon, self.search_wikipedia)
        for provider in providers:
            try:
                alternatives.extend(provider(query))
            except Exception:
                continue
        return self.merge_provider_results([alternatives])

    def search_direct_product_url(self, url):
        """Importiert eine allgemeine Hersteller- oder Produktseite."""
        html = self.fetch_url(url)
        title = ''
        for pattern in (
            r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title',
            r'<h1[^>]*>(.*?)</h1>',
            r'<title[^>]*>(.*?)</title>',
        ):
            match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
            if match:
                title = self.clean_html_text(match.group(1))
                if title:
                    break
        facts = []
        for label, value in re.findall(
            r'<dt[^>]*>(.*?)</dt>\s*<dd[^>]*>(.*?)</dd>',
            html,
            re.IGNORECASE | re.DOTALL,
        ):
            clean_label = self.clean_html_text(label)
            clean_value = self.clean_html_text(value)
            if clean_label and clean_value:
                facts.append(f"{clean_label}: {clean_value}")
            if len(facts) >= 20:
                break
        if not facts:
            description = re.search(
                r'<meta[^>]+name=["\']description["\'][^>]+'
                r'content=["\']([^"\']+)',
                html,
                re.IGNORECASE,
            )
            if description:
                facts.append(self.clean_html_text(description.group(1)))
        return [(title, '\n'.join(facts), url)] if title else []

    def apply_online_results(self, search_term, request_id, results, errors):
        """Übernimmt ausschließlich Ergebnisse der neuesten Suche."""
        if not self.is_search_current(search_term, request_id):
            return
        if not results:
            # Bereits sichtbare lokale Treffer bleiben erhalten, auch wenn
            # einzelne Online-Anbieter gerade blockieren oder nichts liefern.
            if self.search_results:
                return
            details = (
                "; ".join(errors)
                if errors
                else TRANSLATIONS[self.language]['online_search_failed']
            )
            self.status_var.set(details)
            return
        normalized_results = [
            (
                self.complete_known_title_fragment(title)
                if self.host_has_label(source_url, 'amazon') else title,
                desc,
                source_url,
            )
            for title, desc, source_url in results
        ]
        online_results = [
            {
                'group_id': 'online',
                'variant': {
                    'name': title,
                    'description': {'de': desc, 'en': desc},
                    'source_url': source_url,
                    'sources': [self.source_name(source_url)],
                    'quality': self.match_quality(
                        search_term, title, source_url
                    ),
                    **copy.deepcopy(
                        self._ebay_result_metadata.get(source_url, {})
                    ),
                    **copy.deepcopy(
                        self._market_result_metadata.get(source_url, {})
                    ),
                },
            }
            for title, desc, source_url in normalized_results
        ]
        existing = {
            re.sub(
                r'\W+', ' ', result['variant']['name'].lower()
            ).strip(): result
            for result in self.search_results
        }
        for result in online_results:
            identity = re.sub(
                r'\W+', ' ', result['variant']['name'].lower()
            ).strip()
            previous = existing.get(identity)
            if previous:
                previous_variant = previous['variant']
                sources = previous_variant.setdefault(
                    'sources',
                    [self.source_name(previous_variant.get('source_url', ''))],
                )
                for source in result['variant']['sources']:
                    if source not in sources:
                        sources.append(source)
                continue
            self.search_results.append(result)
            existing[identity] = result
        self.populate_online_results()

    def load_amazon_details_async(self, variant):
        """Lädt die langsamere Amazon-Produktseite erst nach der Auswahl."""
        source_url = variant.get('source_url', '')

        def worker():
            try:
                title, description = self.extract_amazon_product(
                    self.fetch_url(source_url)
                )
                title = self.repair_truncated_amazon_title(title)
            except Exception:
                return
            self.root.after(
                0,
                lambda: self.apply_amazon_details(
                    variant, title, description
                ),
            )

        threading.Thread(target=worker, daemon=True).start()

    def apply_amazon_details(self, variant, title, description):
        if self.selected_variant is not variant or not description:
            return
        variant['name'] = title or variant['name']
        variant['description'] = {'de': description, 'en': description}
        sales_draft = self.generator.build_sales_draft(
            variant['name'], description, self.language
        )
        self.preview_text.delete(1.0, tk.END)
        self.preview_text.insert(1.0, sales_draft)
        self.initialize_listing_assistant(variant, sales_draft)
        self.status_var.set(
            f"{TRANSLATIONS[self.language]['selected_variant']} {variant['name']}"
        )

    def load_comparison_details_async(self, variant):
        """Lädt Geizhals-/Idealo-Details erst nach Auswahl eines Treffers."""
        source_url = variant.get('source_url', '')
        provider = (
            'geizhals' if self.host_has_label(source_url, 'geizhals')
            else 'idealo'
        )

        def worker():
            try:
                title, description = self.extract_comparison_product(
                    self.fetch_url(source_url), provider
                )
            except Exception:
                return
            self.root.after(
                0,
                lambda: self.apply_amazon_details(
                    variant, title, description
                ),
            )

        threading.Thread(target=worker, daemon=True).start()

    def load_suggestion_details_async(self, variant):
        """Löst einen Web-Vorschlag über echte Produktquellen auf."""
        requested_title = variant.get('name', '').strip()
        self.status_var.set(
            TRANSLATIONS[self.language]['details_loading']
        )

        def worker():
            candidates = []
            providers = (
                self.search_amazon,
                self.search_geizhals,
                self.search_idealo,
                self.search_wikipedia,
            )
            with ThreadPoolExecutor(max_workers=len(providers)) as executor:
                futures = [
                    executor.submit(provider, requested_title)
                    for provider in providers
                ]
                for future in as_completed(futures):
                    try:
                        candidates.extend(future.result())
                    except Exception:
                        continue

            normalized_requested = re.sub(
                r'\W+', ' ', requested_title.lower()
            ).strip()

            def candidate_score(candidate):
                title, _description, source_url = candidate
                normalized_title = re.sub(
                    r'\W+', ' ', title.lower()
                ).strip()
                exact_bonus = 100 if normalized_title == normalized_requested else 0
                source_bonus = (
                    30 if self.host_has_label(source_url, 'geizhals')
                    else 25 if self.host_has_label(source_url, 'idealo')
                    else 20 if self.host_has_label(source_url, 'amazon')
                    else 10
                )
                similarity = difflib.SequenceMatcher(
                    None, normalized_requested, normalized_title
                ).ratio() * 50
                return exact_bonus + source_bonus + similarity

            for title, description, source_url in sorted(
                candidates, key=candidate_score, reverse=True
            ):
                try:
                    if 'amazon.de/' in source_url:
                        detail_title, detail_text = self.extract_amazon_product(
                            self.fetch_url(source_url)
                        )
                    elif 'geizhals.de/' in source_url:
                        detail_title, detail_text = self.extract_comparison_product(
                            self.fetch_url(source_url), 'geizhals'
                        )
                    elif 'idealo.de/' in source_url:
                        detail_title, detail_text = self.extract_comparison_product(
                            self.fetch_url(source_url), 'idealo'
                        )
                    else:
                        detail_title, detail_text = title, description
                except Exception:
                    continue
                if detail_text and not detail_text.startswith(
                    ('Amazon-Suchergebnis:', 'Online gefunden:')
                ):
                    self.root.after(
                        0,
                        lambda resolved_title=detail_title or title,
                               resolved_text=detail_text,
                               resolved_url=source_url:
                            self.apply_resolved_suggestion(
                                variant,
                                resolved_title,
                                resolved_text,
                                resolved_url,
                            ),
                    )
                    return

            self.root.after(
                0,
                lambda: self.apply_unavailable_suggestion(variant),
            )

        threading.Thread(target=worker, daemon=True).start()

    def apply_resolved_suggestion(
        self, variant, title, description, source_url
    ):
        if self.selected_variant is not variant:
            return
        variant['name'] = title or variant['name']
        variant['description'] = {'de': description, 'en': description}
        variant['source_url'] = source_url
        self.load_product_image_async(variant, source_url)
        sales_draft = self.generator.build_sales_draft(
            variant['name'], description, self.language
        )
        self.preview_text.delete(1.0, tk.END)
        self.preview_text.insert(1.0, sales_draft)
        self.status_var.set(
            f"{TRANSLATIONS[self.language]['selected_variant']} "
            f"{variant['name']}"
        )

    def apply_unavailable_suggestion(self, variant):
        if self.selected_variant is variant:
            self.status_var.set(
                TRANSLATIONS[self.language]['details_unavailable']
            )

    def populate_online_results(self):
        trans = TRANSLATIONS[self.language]
        selected_variant = self.selected_variant
        self.variant_listbox.delete(0, tk.END)
        for result in self.search_results:
            self.variant_listbox.insert(
                tk.END, self.result_display_label(result)
            )
        self.status_var.set(f"{len(self.search_results)} {trans['online_results']}")
        selected_index = next(
            (
                index for index, result in enumerate(self.search_results)
                if result['variant'] is selected_variant
            ),
            0,
        )
        self.select_result_index(selected_index)

    @classmethod
    def source_name(cls, source_url):
        """Benennt die Herkunft anhand des Hostnamens."""
        host = cls.url_host(source_url)
        if cls.host_is(source_url, 'suggestqueries.google.com'):
            return 'Web-Vorschlag'
        if cls.host_is(source_url, 'd-nb.info'):
            return 'DNB'
        if cls.host_is(source_url, 'googleapis.com', 'books.google.com'):
            return 'Google Books'
        for label, name in (
            ('amazon', 'Amazon'),
            ('geizhals', 'Geizhals'),
            ('idealo', 'Idealo'),
            ('ebay', 'eBay'),
            ('kleinanzeigen', 'Kleinanzeigen'),
            ('wikipedia', 'Wikipedia'),
            ('zvab', 'ZVAB'),
            ('abebooks', 'ZVAB'),
            ('openlibrary', 'Open Library'),
        ):
            if cls.host_has_label(source_url, label):
                return name
        return host.removeprefix('www.') or 'Lokal'

    @staticmethod
    def match_quality(query, title, source_url=''):
        normalized_query = re.sub(r'\W+', ' ', query.lower()).strip()
        normalized_title = re.sub(r'\W+', ' ', title.lower()).strip()
        exact_identifier = bool(
            re.fullmatch(r'[0-9Xx-]{10,17}|B0[A-Z0-9]{8}', query.strip())
        )
        if exact_identifier or normalized_query == normalized_title:
            return 'exakt'
        ratio = difflib.SequenceMatcher(
            None, normalized_query, normalized_title
        ).ratio()
        coverage = (
            sum(word in normalized_title for word in normalized_query.split())
            / max(1, len(normalized_query.split()))
        )
        if ratio >= .82 or coverage == 1:
            return 'hoch'
        if ratio >= .58 or coverage >= .6:
            return 'mittel'
        return 'unsicher'

    def result_display_label(self, result):
        variant = result['variant']
        sources = variant.get('sources') or [
            self.source_name(variant.get('source_url', ''))
        ]
        quality = variant.get('quality') or self.match_quality(
            self.search_var.get(), variant.get('name', ''),
            variant.get('source_url', '')
        )
        return (
            f"{variant.get('name', '')}  "
            f"[{' + '.join(sources)} · {quality}]"
        )

    @staticmethod
    def normalize_isbn(value):
        compact = re.sub(r'(?i)\bISBN(?:-1[03])?\b\s*:?\s*', '', value)
        compact = re.sub(r'[^0-9Xx]', '', compact).upper()
        if len(compact) == 10:
            total = sum(
                (10 - index) * (10 if char == 'X' else int(char))
                for index, char in enumerate(compact)
            )
            return compact if total % 11 == 0 else ''
        if len(compact) == 13 and compact.startswith(('978', '979')):
            total = sum(
                int(char) * (1 if index % 2 == 0 else 3)
                for index, char in enumerate(compact[:12])
            )
            check = (10 - total % 10) % 10
            return compact if check == int(compact[-1]) else ''
        return ''

    @classmethod
    def isbn_search_variants(cls, value):
        isbn = cls.normalize_isbn(value)
        if not isbn:
            return []
        variants = [isbn]
        if len(isbn) == 10:
            base = f"978{isbn[:9]}"
            total = sum(
                int(char) * (1 if index % 2 == 0 else 3)
                for index, char in enumerate(base)
            )
            variants.append(f"{base}{(10 - total % 10) % 10}")
        elif isbn.startswith('978'):
            base = isbn[3:12]
            total = sum((10 - index) * int(char) for index, char in enumerate(base))
            remainder = (11 - total % 11) % 11
            check = 'X' if remainder == 10 else str(remainder)
            variants.append(f"{base}{check}")
        return variants

    @staticmethod
    def expand_search_spellings(search_term):
        """Erzeugt z. B. aus S23 zusätzlich S 23 und umgekehrt."""
        if re.search(r'https?://', search_term, re.IGNORECASE):
            return [search_term]
        isbn_variants = ProductGeneratorGUI.isbn_search_variants(search_term)
        if isbn_variants:
            return list(dict.fromkeys([search_term, *isbn_variants]))
        compact = re.sub(
            r'\b([A-Za-z])\s+(\d{1,4})\b', r'\1\2', search_term
        )
        spaced = re.sub(
            r'\b([A-Za-z])(\d{1,4})\b', r'\1 \2', search_term
        )
        return list(dict.fromkeys([search_term, compact, spaced]))

    @staticmethod
    def merge_provider_results(result_groups):
        merged = []
        seen = set()
        for results in result_groups:
            for title, description, source_url in results:
                identity = (
                    re.sub(r'\W+', ' ', title.lower()).strip(),
                    source_url.split('#', 1)[0],
                )
                if identity in seen:
                    continue
                seen.add(identity)
                merged.append((title, description, source_url))
        return merged

    @staticmethod
    def normalize_text(value):
        return unicodedata.normalize(
            'NFC', html_lib.unescape(str(value or ''))
        ).strip()

    def search_spelling_variants(self, provider, search_term):
        result_groups = []
        last_error = None
        for query in self.expand_search_spellings(search_term):
            try:
                result_groups.append(provider(query))
            except Exception as exc:
                last_error = exc
        merged = self.merge_provider_results(result_groups)
        if not merged and last_error:
            raise last_error
        return merged

    def search_kleinanzeigen_agent(self, search_term):
        """Sucht öffentliche Inserate über die Kleinanzeigen-Agent REST-API."""
        api_key = self.get_secret('kleinanzeigen_api_key')
        if not api_key:
            raise RuntimeError(
                "API-Key fehlt (unter Einstellungen → Marktplatz-APIs eintragen)"
            )
        endpoint = (
            "https://api.kleinanzeigen-agent.de/api/v2/kleinanzeigen/search?"
            + urllib.parse.urlencode({
                'q': search_term, 'page': 0, 'size': 25,
                'picture_required': 'true',
            })
        )
        request = urllib.request.Request(
            endpoint,
            headers={
                'Accept': 'application/json',
                'User-Agent': 'eBay-Kleinanzeigen-Creationtool/0.2',
                'klaz_key': api_key,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode('utf-8'))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(self.api_error_message(exc)) from exc
        results = []
        for ad in payload.get('data', {}).get('ads', []):
            title = self.normalize_text(ad.get('title', ''))
            if not title:
                continue
            facts = []
            category = ad.get('category') or {}
            if category.get('name'):
                facts.append(f"Kategorie: {category['name']}")
            details = ad.get('details') or {}
            if isinstance(details, dict):
                for label, value in list(details.items())[:15]:
                    if value not in (None, '', [], {}):
                        facts.append(f"{label}: {value}")
            for attribute in (ad.get('attributes') or [])[:15]:
                if not isinstance(attribute, dict):
                    continue
                label = (
                    attribute.get('label') or attribute.get('name')
                    or attribute.get('key')
                )
                value = (
                    attribute.get('value_label') or attribute.get('value')
                    or attribute.get('values')
                )
                if label and value not in (None, '', [], {}):
                    if isinstance(value, list):
                        value = ", ".join(map(str, value))
                    facts.append(f"{label}: {value}")
            location = ad.get('location') or {}
            if location.get('city') or location.get('name'):
                facts.append(
                    "Standort des Vergleichsangebots: "
                    f"{location.get('city') or location.get('name')}"
                )
            source_url = ad.get('ad_url') or (
                f"https://www.kleinanzeigen.de/s-anzeige/{ad.get('ad_id', '')}"
            )
            price = ad.get('price') or {}
            if source_url:
                if not hasattr(self, '_market_result_metadata'):
                    self._market_result_metadata = {}
                self._market_result_metadata[source_url] = {
                    'comparison_price': price.get('amount'),
                    'comparison_condition': self.normalize_text(
                        details.get('Zustand', '')
                        if isinstance(details, dict) else ''
                    ),
                    'image_urls': [
                        image.get('url') or image.get('src')
                        for image in ad.get('images') or []
                        if isinstance(image, dict)
                        and (image.get('url') or image.get('src'))
                    ],
                }
            results.append((
                title,
                '\n'.join(facts) or "Öffentliches Kleinanzeigen-Vergleichsangebot",
                source_url,
            ))
        return results

    def test_kleinanzeigen_agent_connection(self):
        """Validiert den Key mit einer minimalen regulären Ein-Treffer-Suche."""
        api_key = self.get_secret('kleinanzeigen_api_key')
        if not api_key:
            raise RuntimeError("API-Key fehlt")
        endpoint = (
            "https://api.kleinanzeigen-agent.de/api/v2/kleinanzeigen/search?"
            + urllib.parse.urlencode({'q': 'iphone', 'page': 0, 'size': 1})
        )
        request = urllib.request.Request(
            endpoint,
            headers={
                'Accept': 'application/json',
                'User-Agent': 'eBay-Kleinanzeigen-Creationtool/0.2',
                'klaz_key': api_key,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode('utf-8'))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(self.api_error_message(exc)) from exc
        if not payload.get('success'):
            raise RuntimeError(payload.get('message') or "API-Antwort ungültig")
        return True

    @staticmethod
    def api_error_message(error):
        """Extrahiert eine sichere Meldung, ohne Header oder Schlüssel zu loggen."""
        try:
            payload = json.loads(
                error.read(4096).decode('utf-8', errors='replace')
            )
        except Exception:
            return f"HTTP {getattr(error, 'code', '?')}: {getattr(error, 'reason', '')}"
        data = payload.get('data') or {}
        code = payload.get('code') or data.get('code')
        message = payload.get('message') or data.get('message')
        details = " – ".join(str(value) for value in (code, message) if value)
        return details or f"HTTP {getattr(error, 'code', '?')}"

    def get_ebay_access_token(self):
        """Erzeugt und puffert ein eBay Application-Token."""
        if (
            self._ebay_access_token
            and time.monotonic() < self._ebay_access_token_expires
        ):
            return self._ebay_access_token
        client_id = self.get_secret('ebay_client_id')
        client_secret = self.get_secret('ebay_client_secret')
        if not client_id or not client_secret:
            raise RuntimeError(
                "Client-ID/Secret fehlen (unter Einstellungen → "
                "Marktplatz-APIs eintragen)"
            )
        credentials = base64.b64encode(
            f"{client_id}:{client_secret}".encode('utf-8')
        ).decode('ascii')
        api_host = (
            'api.sandbox.ebay.com'
            if getattr(self, 'ebay_environment', 'production') == 'sandbox'
            else 'api.ebay.com'
        )
        request = urllib.request.Request(
            f"https://{api_host}/identity/v1/oauth2/token",
            data=urllib.parse.urlencode({
                'grant_type': 'client_credentials',
                'scope': 'https://api.ebay.com/oauth/api_scope',
            }).encode('ascii'),
            headers={
                'Authorization': f"Basic {credentials}",
                'Content-Type': 'application/x-www-form-urlencoded',
                'Accept': 'application/json',
            },
            method='POST',
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode('utf-8'))
        self._ebay_access_token = payload['access_token']
        self._ebay_access_token_expires = (
            time.monotonic() + max(60, int(payload.get('expires_in', 7200)) - 60)
        )
        return self._ebay_access_token

    def search_ebay(self, search_term):
        """Sucht eBay.de über die offizielle Browse API."""
        token = self.get_ebay_access_token()
        normalized_identifier = re.sub(r'\D', '', search_term)
        parameters = {'limit': 25, 'fieldgroups': 'EXTENDED'}
        if len(normalized_identifier) in (8, 12, 13, 14):
            parameters['gtin'] = normalized_identifier
        else:
            parameters['q'] = search_term
        api_host = (
            'api.sandbox.ebay.com'
            if getattr(self, 'ebay_environment', 'production') == 'sandbox'
            else 'api.ebay.com'
        )
        endpoint = (
            f"https://{api_host}/buy/browse/v1/item_summary/search?"
            + urllib.parse.urlencode(parameters)
        )
        request = urllib.request.Request(
            endpoint,
            headers={
                'Authorization': f"Bearer {token}",
                'Accept': 'application/json',
                'X-EBAY-C-MARKETPLACE-ID': 'EBAY_DE',
                'Accept-Language': 'de-DE',
            },
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode('utf-8'))
        results = []
        for item in payload.get('itemSummaries', []):
            title = self.normalize_text(item.get('title', ''))
            if not title:
                continue
            facts = []
            if item.get('condition'):
                facts.append(f"Zustand des Vergleichsangebots: {item['condition']}")
            categories = item.get('categories') or []
            if categories and categories[0].get('categoryName'):
                facts.append(f"Kategorie: {categories[0]['categoryName']}")
            short_description = self.normalize_text(
                item.get('shortDescription', '')
            )
            if short_description:
                facts.append(short_description)
            source_url = (
                item.get('itemWebUrl')
                or item.get('itemAffiliateWebUrl', '')
            )
            image = item.get('image') or {}
            additional_images = item.get('additionalImages') or []
            metadata = {
                'ebay_item_id': item.get('itemId', ''),
                'ebay_category_id': (
                    categories[0].get('categoryId', '')
                    if categories else ''
                ),
                'ebay_category_name': (
                    categories[0].get('categoryName', '')
                    if categories else ''
                ),
                'image_urls': [
                    candidate.get('imageUrl')
                    for candidate in [image] + additional_images
                    if candidate.get('imageUrl')
                ],
                'comparison_price': (
                    float((item.get('price') or {}).get('value'))
                    if (item.get('price') or {}).get('value')
                    else None
                ),
                'comparison_condition': item.get('condition', ''),
                'comparison_shipping': (
                    float(
                        ((item.get('shippingOptions') or [{}])[0].get(
                            'shippingCost'
                        ) or {}).get('value')
                    )
                    if (
                        (item.get('shippingOptions') or [{}])[0].get(
                            'shippingCost'
                        ) or {}
                    ).get('value')
                    else None
                ),
            }
            if source_url:
                if not hasattr(self, '_ebay_result_metadata'):
                    self._ebay_result_metadata = {}
                self._ebay_result_metadata[source_url] = metadata
            results.append((
                title,
                '\n'.join(facts) or "Öffentliches eBay-Vergleichsangebot",
                source_url,
            ))
        return results

    def ebay_api_json(self, path, parameters=None, marketplace=True):
        """Ruft eine aktuelle eBay-REST-API mit Application-Token auf."""
        token = self.get_ebay_access_token()
        api_host = (
            'api.sandbox.ebay.com'
            if getattr(self, 'ebay_environment', 'production') == 'sandbox'
            else 'api.ebay.com'
        )
        endpoint = f"https://{api_host}{path}"
        if parameters:
            endpoint += '?' + urllib.parse.urlencode(parameters)
        headers = {
            'Authorization': f"Bearer {token}",
            'Accept': 'application/json',
            'Accept-Language': 'de-DE',
        }
        if marketplace:
            headers['X-EBAY-C-MARKETPLACE-ID'] = 'EBAY_DE'
        request = urllib.request.Request(endpoint, headers=headers)
        with urllib.request.urlopen(request, timeout=20) as response:
            data = response.read(MAX_TEXT_RESPONSE_BYTES + 1)
        if len(data) > MAX_TEXT_RESPONSE_BYTES:
            raise ValueError("eBay-Antwort ist zu groß")
        return json.loads(data.decode('utf-8'))

    def get_ebay_item_details(self, item_id):
        """Lädt Produktdaten, Merkmale und Bilder eines Browse-Treffers."""
        payload = self.ebay_api_json(
            '/buy/browse/v1/item/'
            + urllib.parse.quote(str(item_id), safe=''),
            {'fieldgroups': 'PRODUCT'},
        )
        product = payload.get('product') or {}
        aspect_values = {}
        for group in product.get('aspectGroups') or []:
            for aspect in group.get('aspects') or []:
                name = self.normalize_text(aspect.get('localizedName', ''))
                values = [
                    self.normalize_text(value)
                    for value in aspect.get('localizedValues') or []
                    if self.normalize_text(value)
                ]
                if name and values:
                    aspect_values[name] = ', '.join(values)
        for aspect in payload.get('localizedAspects') or []:
            name = self.normalize_text(aspect.get('name', ''))
            value = self.normalize_text(aspect.get('value', ''))
            if name and value:
                aspect_values[name] = value
        image_urls = []
        for image in (
            [product.get('image') or {}, payload.get('image') or {}]
            + list(product.get('additionalImages') or [])
            + list(payload.get('additionalImages') or [])
        ):
            url = image.get('imageUrl')
            if url and url not in image_urls:
                image_urls.append(url)
        facts = []
        if product.get('brand'):
            facts.append(f"Marke: {self.normalize_text(product['brand'])}")
        if product.get('mpns'):
            facts.append(f"Herstellernummer: {', '.join(product['mpns'])}")
        if product.get('gtins'):
            facts.append(f"GTIN: {', '.join(product['gtins'])}")
        facts.extend(
            f"{name}: {value}" for name, value in aspect_values.items()
        )
        return {
            'ebay_item_id': payload.get('itemId') or item_id,
            'ebay_category_id': payload.get('categoryId', ''),
            'ebay_category_name': (
                str(payload.get('categoryPath', '')).split('|')[-1]
            ),
            'category_id': payload.get('categoryId', ''),
            'category_name': (
                str(payload.get('categoryPath', '')).split('|')[-1]
            ),
            'category_path': str(
                payload.get('categoryPath', '')
            ).replace('|', ' > '),
            'aspect_values': aspect_values,
            'image_urls': image_urls,
            'ebay_product_facts': facts,
        }

    def get_ebay_default_category_tree_id(self):
        payload = self.ebay_api_json(
            '/commerce/taxonomy/v1/get_default_category_tree_id',
            {'marketplace_id': 'EBAY_DE'},
            marketplace=False,
        )
        return str(payload['categoryTreeId'])

    def get_ebay_category_suggestions(self, query):
        """Liefert relevante deutsche eBay-Blattkategorien."""
        tree_id = self.get_ebay_default_category_tree_id()
        payload = self.ebay_api_json(
            '/commerce/taxonomy/v1/category_tree/'
            f"{urllib.parse.quote(tree_id, safe='')}/get_category_suggestions",
            {'q': query},
            marketplace=False,
        )
        results = []
        for suggestion in payload.get('categorySuggestions') or []:
            category = suggestion.get('category') or {}
            category_id = str(category.get('categoryId', '')).strip()
            name = self.normalize_text(category.get('categoryName', ''))
            if not category_id or not name:
                continue
            ancestors = [
                self.normalize_text(
                    (entry.get('category') or {}).get('categoryName', '')
                )
                for entry in reversed(
                    suggestion.get('categoryTreeNodeAncestors') or []
                )
            ]
            path = ' > '.join(
                value for value in ancestors + [name] if value
            )
            results.append({
                'id': category_id,
                'name': name,
                'path': path,
            })
        return results[:8]

    def get_ebay_item_aspects(self, category_id):
        """Lädt Pflicht-, empfohlene und optionale Artikelmerkmale."""
        tree_id = self.get_ebay_default_category_tree_id()
        payload = self.ebay_api_json(
            '/commerce/taxonomy/v1/category_tree/'
            f"{urllib.parse.quote(tree_id, safe='')}/"
            'get_item_aspects_for_category',
            {'category_id': str(category_id)},
            marketplace=False,
        )
        aspects = []
        for entry in payload.get('aspects') or []:
            name = self.normalize_text(entry.get('localizedAspectName', ''))
            if not name:
                continue
            constraint = entry.get('aspectConstraint') or {}
            values = [
                self.normalize_text(value.get('localizedValue', ''))
                for value in entry.get('aspectValues') or []
                if self.normalize_text(value.get('localizedValue', ''))
            ]
            aspects.append({
                'name': name,
                'required': bool(constraint.get('aspectRequired')),
                'recommended': str(
                    constraint.get('aspectUsage', '')
                ).upper() == 'RECOMMENDED',
                'values': values,
                'mode': constraint.get('aspectMode', ''),
                'cardinality': constraint.get(
                    'itemToAspectCardinality', 'SINGLE'
                ),
            })
        aspects.sort(
            key=lambda entry: (
                not entry['required'],
                not entry['recommended'],
                entry['name'].casefold(),
            )
        )
        return aspects

    def search_geizhals(self, search_term):
        return self.search_spelling_variants(
            self._search_geizhals_once, search_term
        )

    def _search_geizhals_once(self, search_term):
        direct_match = re.search(
            r'https?://(?:www\.)?geizhals\.de/[^\s]+-v\d+\.html',
            search_term,
            re.IGNORECASE,
        )
        if direct_match:
            url = direct_match.group(0)
            title, description = self.extract_comparison_product(
                self.fetch_url(url), 'geizhals'
            )
            return [(title, description, url)] if title else []
        query = urllib.parse.quote(search_term)
        url = f"https://geizhals.de/?fs={query}&hloc=de&nocookie=1"
        return self.search_search_page(url, "https://geizhals.de")

    def search_idealo(self, search_term):
        return self.search_spelling_variants(
            self._search_idealo_once, search_term
        )

    def _search_idealo_once(self, search_term):
        direct_match = re.search(
            r'https?://(?:www\.)?idealo\.de/preisvergleich/'
            r'OffersOfProduct/\d+_[^\s]+\.html',
            search_term,
            re.IGNORECASE,
        )
        if direct_match:
            url = direct_match.group(0)
            title, description = self.extract_comparison_product(
                self.fetch_url(url), 'idealo'
            )
            return [(title, description, url)] if title else []
        query = urllib.parse.quote(search_term)
        url = f"https://www.idealo.de/preisvergleich/MainSearchProductCategory.html?q={query}"
        return self.search_search_page(url, "https://www.idealo.de")

    def extract_comparison_product(self, html, provider):
        """Extrahiert Titel und technische Fakten direkter Vergleichsseiten."""
        title_match = re.search(
            r'<h1[^>]*>(.*?)</h1>', html, re.IGNORECASE | re.DOTALL
        )
        title = self.clean_html_text(title_match.group(1)) if title_match else ''
        if not title:
            title_match = re.search(
                r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)',
                html,
                re.IGNORECASE,
            )
            title = self.clean_html_text(title_match.group(1)) if title_match else ''
        title = re.sub(
            r'\s+(?:ab\s+€.*|\|\s*(?:Preisvergleich|Vergleiche).*)$',
            '',
            title,
            flags=re.IGNORECASE,
        ).strip()

        facts = []
        for label, value in re.findall(
            r'<dt[^>]*>(.*?)</dt>\s*<dd[^>]*>(.*?)</dd>',
            html,
            re.IGNORECASE | re.DOTALL,
        ):
            clean_label = self.clean_html_text(label)
            clean_value = self.clean_html_text(value)
            if re.search(
                r'(Kontakt zum Hersteller|Verantwortliche Person|'
                r'Sicherheitsinformationen)',
                clean_label,
                re.IGNORECASE,
            ):
                continue
            if clean_label and clean_value:
                facts.append(f"{clean_label}: {clean_value}")
            if len(facts) >= 15:
                break

        if not facts and provider == 'idealo':
            overview = re.search(
                r'Produktübersicht\s*:?\s*</?[^>]*>\s*(.*?)(?=<a|</div>|</section>)',
                html,
                re.IGNORECASE | re.DOTALL,
            )
            if overview:
                text = self.clean_html_text(overview.group(1))
                if text:
                    facts.append(f"Produktübersicht: {text}")

        if not facts:
            description_match = re.search(
                r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)',
                html,
                re.IGNORECASE,
            )
            if description_match:
                facts.append(self.clean_html_text(description_match.group(1)))

        description = '\n'.join(f"• {fact}" for fact in facts)
        return title, description

    def search_dnb_isbn(self, search_term):
        """Liest deutsche Buchdaten über die öffentliche DNB-SRU-API."""
        variants = self.isbn_search_variants(search_term)
        if not variants:
            return []
        isbn13 = next(
            (value for value in variants if len(value) == 13),
            variants[0],
        )
        params = urllib.parse.urlencode({
            'version': '1.1',
            'operation': 'searchRetrieve',
            'query': f'NUM={isbn13}',
            'recordSchema': 'MARC21-xml',
        })
        endpoint = f"https://services.dnb.de/sru/dnb?{params}"
        root = ET.fromstring(self.fetch_url(endpoint))
        marc_ns = {'m': 'http://www.loc.gov/MARC21/slim'}
        record = root.find('.//m:record', marc_ns)
        if record is None:
            return []

        def subfields(tag, code):
            return [
                self.clean_marc_text(node.text or '')
                for node in record.findall(
                    f"./m:datafield[@tag='{tag}']/m:subfield[@code='{code}']",
                    marc_ns,
                )
                if self.clean_marc_text(node.text or '')
            ]

        title_parts = subfields('245', 'a') + subfields('245', 'b')
        title = ': '.join(title_parts)
        if not title:
            return []

        facts = []
        mappings = (
            ('Autor', subfields('100', 'a')),
            ('Ausgabe', subfields('250', 'a')),
            ('Erscheinungsort', subfields('264', 'a')),
            ('Verlag', subfields('264', 'b')),
            ('Erscheinungsdatum', subfields('264', 'c')),
            ('Umfang', subfields('300', 'a')),
            ('Ausstattung', subfields('300', 'b')),
            ('Format', subfields('300', 'c')),
        )
        for label, values in mappings:
            if values:
                facts.append(f"{label}: {', '.join(values)}")

        isbn_values = subfields('020', 'a')
        for value in isbn_values:
            label = 'ISBN-13' if len(value) == 13 else 'ISBN-10'
            facts.append(f"{label}: {value}")

        dnb_id_node = record.find("./m:controlfield[@tag='001']", marc_ns)
        dnb_id = (
            self.clean_marc_text(dnb_id_node.text or '')
            if dnb_id_node is not None else ''
        )
        publisher_urls = [
            value for value in subfields('856', 'u')
            if value.startswith('https://') and not value.lower().endswith('.pdf')
        ]
        source_url = next(
            (value for value in publisher_urls if '/ean/' in value),
            f"https://d-nb.info/{dnb_id}" if dnb_id else endpoint,
        )
        description = '\n'.join(f"• {fact}" for fact in facts)
        return [(title, description, source_url)]

    def search_zvab_isbn(self, search_term):
        """Fallback für ISBNs, die nicht im DNB-Bestand enthalten sind."""
        variants = self.isbn_search_variants(search_term)
        if not variants:
            return []
        isbn13 = next(
            (value for value in variants if len(value) == 13),
            variants[0],
        )
        source_url = f"https://www.zvab.com/products/isbn/{isbn13}"
        html = self.fetch_url(source_url)
        title, description = self.extract_comparison_product(html, 'zvab')
        if not title or isbn13 not in html:
            return []
        title = re.sub(
            r'\s+-\s+(?:Softcover|Hardcover|Taschenbuch)\s*$',
            '',
            title,
            flags=re.IGNORECASE,
        ).strip()
        if not description:
            description = f"• ISBN-13: {isbn13}"
        return [(title, description, source_url)]

    def search_open_library(self, search_term):
        """Liest strukturierte Buchdaten über die öffentliche Search API."""
        isbn = self.normalize_isbn(search_term)
        if not isbn:
            return []
        endpoint = "https://openlibrary.org/search.json?" + urllib.parse.urlencode({
            'isbn': isbn,
            'fields': (
                'key,title,author_name,publisher,first_publish_year,isbn,'
                'number_of_pages_median,language'
            ),
            'limit': 3,
        })
        try:
            payload = json.loads(self.fetch_url(endpoint))
        except Exception:
            return []
        results = []
        for item in payload.get('docs', []):
            title = unicodedata.normalize(
                'NFC', str(item.get('title') or '')
            ).strip()
            if not title:
                continue
            facts = []
            for label, value in (
                ('Autor', ', '.join(item.get('author_name') or [])),
                ('Verlag', ', '.join((item.get('publisher') or [])[:3])),
                ('Erscheinungsdatum', item.get('first_publish_year')),
                ('Anzahl der Seiten', item.get('number_of_pages_median')),
                ('ISBN', isbn),
            ):
                if value:
                    facts.append(f"{label}: {value}")
            key = str(item.get('key') or '')
            source = (
                urllib.parse.urljoin('https://openlibrary.org', key)
                if key else f"https://openlibrary.org/isbn/{isbn}"
            )
            results.append((title, '\n'.join(facts), source))
        return results

    def search_google_books(self, search_term):
        """Liest öffentliche Google-Books-Metadaten anhand einer ISBN."""
        isbn = self.normalize_isbn(search_term)
        if not isbn:
            return []
        endpoint = (
            "https://www.googleapis.com/books/v1/volumes?"
            + urllib.parse.urlencode({
                'q': f'isbn:{isbn}',
                'maxResults': 3,
                'printType': 'books',
            })
        )
        try:
            payload = json.loads(self.fetch_url(endpoint))
        except Exception:
            # Ohne API-Schlüssel kann Google öffentliche Anfragen begrenzen.
            # Die anderen ISBN-Provider laufen unabhängig weiter.
            return []
        results = []
        for item in payload.get('items', []):
            info = item.get('volumeInfo') or {}
            title = unicodedata.normalize(
                'NFC', str(info.get('title') or '')
            ).strip()
            if not title:
                continue
            subtitle = str(info.get('subtitle') or '').strip()
            if subtitle and subtitle.casefold() not in title.casefold():
                title = f"{title}: {subtitle}"
            facts = []
            for label, value in (
                ('Autor', ', '.join(info.get('authors') or [])),
                ('Verlag', info.get('publisher')),
                ('Erscheinungsdatum', info.get('publishedDate')),
                ('Anzahl der Seiten', info.get('pageCount')),
                ('Sprache', info.get('language')),
                ('ISBN', isbn),
                ('Produktübersicht', self.clean_html_text(
                    info.get('description') or ''
                )),
            ):
                if value:
                    facts.append(f"{label}: {value}")
            source = (
                info.get('infoLink')
                or f"https://books.google.com/books?id={item.get('id', '')}"
            )
            results.append((title, '\n'.join(facts), source))
        return results

    @staticmethod
    def clean_marc_text(value):
        value = re.sub(r'[\x80-\x9f]', '', value)
        value = unicodedata.normalize('NFC', value)
        return re.sub(r'\s+', ' ', value).strip(' /:;,')

    def search_web_suggestions(self, search_term):
        """Liefert breite, schlüsselfreie Produktkandidaten aus der Websuche."""
        params = urllib.parse.urlencode({
            'client': 'firefox',
            'hl': 'de',
            'q': search_term,
        })
        endpoint = (
            "https://suggestqueries.google.com/complete/search?"
            f"{params}"
        )
        payload = json.loads(self.fetch_url(endpoint))
        suggestions = payload[1] if len(payload) > 1 else []
        query_terms = re.findall(r'[\w-]+', search_term.lower())
        brand_term = (
            query_terms[0]
            if query_terms and not re.fullmatch(r'\d{8,14}', search_term.strip())
            else ''
        )
        results = []
        seen = set()
        for suggestion in suggestions:
            title = str(suggestion).strip()
            normalized = re.sub(r'\W+', ' ', title.lower()).strip()
            if (
                not normalized
                or normalized in seen
                or (brand_term and brand_term not in normalized.split())
            ):
                continue
            seen.add(normalized)
            source_url = endpoint
            description = (
                f"Web-Suchvorschlag für „{search_term}“: {title}\n"
                "Bitte das genaue Modell auswählen und die automatisch "
                "geladenen Produktangaben vor dem Speichern prüfen."
            )
            results.append((title, description, source_url))
            if len(results) >= 10:
                break
        return results

    def search_wikipedia(self, search_term):
        """Liefert Produktkandidaten über die öffentliche MediaWiki-API."""
        language = 'en' if self.language == 'en' else 'de'
        params = urllib.parse.urlencode({
            'action': 'query',
            'generator': 'search',
            'gsrsearch': search_term,
            'gsrnamespace': 0,
            'gsrlimit': 20,
            'prop': 'extracts|info',
            'exintro': 1,
            'explaintext': 1,
            'inprop': 'url',
            'format': 'json',
            'formatversion': 2,
        })
        payload = json.loads(
            self.fetch_url(f"https://{language}.wikipedia.org/w/api.php?{params}")
        )
        pages = payload.get('query', {}).get('pages', [])
        query_terms = [
            term for term in re.findall(r'[\w-]+', search_term.lower())
            if len(term) > 1
        ]
        non_distinctive = {
            'google', 'apple', 'samsung', 'sony', 'microsoft',
            'pro', 'plus', 'ultra', 'max', 'mini', 'standard',
        }
        core_terms = [term for term in query_terms if term not in non_distinctive]
        distinctive_term = (core_terms or query_terms)[-1] if query_terms else ''
        has_model_number = any(any(char.isdigit() for char in term) for term in core_terms)
        candidates = []
        for page in pages:
            title = str(page.get('title') or '').strip()
            extract = str(page.get('extract') or '').strip()
            source_url = str(page.get('fullurl') or '').strip()
            if not title or not extract or not source_url:
                continue
            title_lower = title.lower()
            text_lower = f"{title} {extract}".lower()
            if 'begriffsklärung' in title_lower or 'disambiguation' in title_lower:
                continue
            if has_model_number and any(
                term not in title_lower for term in core_terms
            ):
                continue
            if not has_model_number and distinctive_term and distinctive_term not in title_lower:
                continue
            matched_terms = sum(term in text_lower for term in query_terms)
            title_matches = sum(term in title_lower for term in query_terms)
            api_rank = int(page.get('index') or 999)
            display_title = title
            if search_term.lower().startswith('google ') and title_lower.startswith('pixel'):
                display_title = f"Google {title}"
            score = (title_matches * 10) + matched_terms - (api_rank / 100)
            candidates.append((
                score,
                display_title,
                extract[:1500],
                source_url,
            ))
            # Familienseiten nennen Varianten oft nur im Einleitungstext
            # (z. B. Pixel 10 Pro), obwohl keine eigene Artikelseite existiert.
            if (
                query_terms
                and title_matches < len(query_terms)
                and all(term in text_lower for term in query_terms)
                and re.sub(r'\W+', ' ', search_term.lower()).strip()
                != re.sub(r'\W+', ' ', display_title.lower()).strip()
            ):
                candidates.append((
                    score + 50,
                    search_term.strip(),
                    extract[:1500],
                    source_url,
                ))
        candidates.sort(key=lambda item: item[0], reverse=True)
        results = []
        seen_titles = set()
        for _, title, extract, source_url in candidates:
            normalized_title = re.sub(r'\W+', ' ', title.lower()).strip()
            if not normalized_title or normalized_title in seen_titles:
                continue
            seen_titles.add(normalized_title)
            results.append((title, extract, source_url))
            if len(results) >= 12:
                break
        return results

    def search_amazon(self, search_term):
        return self.search_spelling_variants(
            self._search_amazon_once, search_term
        )

    @staticmethod
    def amazon_asin(value):
        """Liest die ASIN aus einer Amazon-URL oder einer reinen ASIN-Eingabe.

        Die Produktkennung wird ausschliesslich am ``/dp/``- oder
        ``/gp/product/``-Segment erkannt. Ein optionales Praefix wuerde in
        einem Slug wie ``…-temperaturgeregelt/dp/B01GSWFOA4`` das zehn Zeichen
        lange Wortende vor dem Schraegstrich als ASIN missdeuten.
        """
        text = str(value or '').strip()
        marker = re.search(
            r'/(?:dp|gp/product|gp/aw/d|product)/([A-Za-z0-9]{10})'
            r'(?![A-Za-z0-9])',
            text,
        )
        if marker:
            return marker.group(1).upper()
        if 'amazon.' in text.casefold():
            # Amazon-Link ohne erkennbares Produktsegment: nicht raten.
            return ''
        if re.fullmatch(r'[A-Za-z0-9]{10}', text) and re.search(r'\d', text):
            return text.upper()
        return ''

    @classmethod
    def amazon_search_query(cls, search_term):
        """Macht aus einer Amazon-URL ohne ASIN einen brauchbaren Suchbegriff.

        Ohne diese Umwandlung wuerde wortwoertlich nach der URL gesucht.
        """
        text = str(search_term or '').strip()
        parsed = urllib.parse.urlparse(text)
        if parsed.scheme not in ('http', 'https'):
            return text
        keywords = urllib.parse.parse_qs(parsed.query).get('k', [''])[0].strip()
        if keywords:
            return keywords
        # Der sprechende Teil steht bei Amazon vorne, nicht im letzten
        # Segment: /Fantec-QB-X2US3R-Gehaeuse/b/12345
        segments = [
            urllib.parse.unquote(segment)
            for segment in parsed.path.split('/') if segment
        ]
        descriptive = [
            segment for segment in segments
            if segment.lower() not in ('dp', 'gp', 'product', 'b', 'd', 'aw')
            and not segment.isdigit()
            and re.search(r'[^\W\d_]', segment)
        ]
        if not descriptive:
            return ''
        best = max(descriptive, key=len)
        best = re.sub(r'\.(?:html?|php)$', '', best, flags=re.IGNORECASE)
        return re.sub(r'\s+', ' ', re.sub(r'[_-]+', ' ', best)).strip()

    def _search_amazon_once(self, search_term):
        """Sucht Amazon.de und extrahiert Fakten aus Produktseiten."""
        asin = self.amazon_asin(search_term)
        if asin:
            source_url = f"https://www.amazon.de/dp/{asin}"
            title, description = self.extract_amazon_product(self.fetch_url(source_url))
            title = self.repair_truncated_amazon_title(title)
            return [(title, description, source_url)] if title else []

        search_term = self.amazon_search_query(search_term)
        if not search_term:
            return []
        query = urllib.parse.quote_plus(search_term)
        html = self.fetch_url(f"https://www.amazon.de/s?k={query}")
        self.raise_for_amazon_block(html)
        candidates = []
        seen = set()
        query_terms = re.findall(r'[\w-]+', search_term.lower())
        normalized_query = ' '.join(query_terms)
        result_pattern = re.compile(
            r'<div\b(?=[^>]*data-asin=["\']([A-Z0-9]{10})["\'])'
            r'(?=[^>]*data-component-type=["\']s-search-result["\'])[^>]*>',
            re.IGNORECASE,
        )
        starts = list(result_pattern.finditer(html))
        for index, match in enumerate(starts):
            asin = match.group(1)
            block_end = starts[index + 1].start() if index + 1 < len(starts) else len(html)
            block = html[match.end():block_end]
            if re.search(
                r'(puis-sponsored|s-sponsored-label|'
                r'Gesponserte Anzeige|Sponsored Ad|/sspa/click|sp_csd=)',
                block,
                re.IGNORECASE,
            ):
                continue
            title_match = re.search(
                r'<h2[^>]+aria-label=["\']([^"\']+)["\']',
                block,
                re.IGNORECASE | re.DOTALL,
            )
            if not title_match:
                title_match = re.search(
                    r'<h2[^>]*>.*?<span[^>]*>(.*?)</span>.*?</h2>',
                    block,
                    re.IGNORECASE | re.DOTALL,
                )
            if not title_match or asin in seen:
                continue
            title = self.clean_html_text(title_match.group(1))
            if not title:
                continue
            seen.add(asin)
            title_terms = re.findall(r'[\w-]+', title.lower())
            normalized_title = ' '.join(title_terms)
            matched = sum(term in normalized_title for term in query_terms)
            score = matched * 4
            score += difflib.SequenceMatcher(
                None, normalized_query, normalized_title
            ).ratio() * 5
            if normalized_query and normalized_query in normalized_title:
                score += 20
            candidates.append((score, asin, title))
            if len(candidates) >= 24:
                break
        candidates.sort(key=lambda item: item[0], reverse=True)

        results = []
        for _, asin, search_title in candidates[:8]:
            source_url = f"https://www.amazon.de/dp/{asin}"
            description = f"Amazon-Suchergebnis: {search_title}"
            results.append((search_title, description, source_url))
        return results

    @staticmethod
    def complete_truncated_title(title, candidate_titles):
        """Ersetzt nur ein nachweislich vervollständigtes letztes Wort."""
        words = title.split()
        if not words:
            return title
        fragment = re.sub(r'\W+', '', words[-1]).casefold()
        if len(fragment) < 4:
            return title
        completions = []
        for candidate in candidate_titles:
            for word in re.findall(r'[\w-]+', candidate):
                normalized = word.casefold()
                if normalized.startswith(fragment) and len(normalized) > len(fragment):
                    completions.append(word)
        normalized_completions = {
            value.casefold(): value for value in completions
        }
        if len(normalized_completions) != 1:
            return title
        completion = next(iter(normalized_completions.values()))
        if words[-1][:1].isupper():
            completion = completion[:1].upper() + completion[1:]
        return ' '.join(words[:-1] + [completion])

    def repair_truncated_amazon_title(self, title):
        """Gleicht abgeschnittene Amazon-Titel mit Webvorschlägen ab."""
        if not title:
            return title
        last_word = re.sub(r'\W+', '', title.split()[-1]).casefold()
        suspicious_fragments = (
            'receiv', 'verstärk', 'lautsprech', 'smartphon',
            'kopfhör', 'netzwerkplay',
        )
        if not any(
            last_word == fragment for fragment in suspicious_fragments
        ):
            return title
        try:
            suggestions = self.search_web_suggestions(title)
        except Exception:
            suggestions = []
        repaired = self.complete_truncated_title(
            title, [candidate[0] for candidate in suggestions]
        )
        return self.complete_known_title_fragment(repaired)

    @staticmethod
    def complete_known_title_fragment(title):
        """Repariert eindeutige, bekannte Wortabbrüche ohne Netzwerkzugriff."""
        words = title.split()
        if not words:
            return title
        completions = {
            'receiv': 'Receiver',
            'verstärk': 'Verstärker',
            'lautsprech': 'Lautsprecher',
            'smartphon': 'Smartphone',
            'kopfhör': 'Kopfhörer',
            'netzwerkplay': 'Netzwerkplayer',
        }
        fragment = re.sub(r'\W+', '', words[-1]).casefold()
        completion = completions.get(fragment)
        if not completion:
            return title
        return ' '.join(words[:-1] + [completion])

    @staticmethod
    def repair_known_fragments_in_text(text):
        replacements = {
            'Receiv': 'Receiver',
            'Verstärk': 'Verstärker',
            'Lautsprech': 'Lautsprecher',
            'Smartphon': 'Smartphone',
            'Kopfhör': 'Kopfhörer',
            'Netzwerkplay': 'Netzwerkplayer',
        }
        for fragment, completion in replacements.items():
            text = re.sub(
                rf'\b{re.escape(fragment)}\b',
                completion,
                text,
                flags=re.IGNORECASE,
            )
        return text

    @staticmethod
    def is_unwanted_search_result(title, source_url=''):
        normalized = re.sub(r'\s+', ' ', str(title)).strip()
        return bool(re.search(
            r'^(?:Gesponserte Anzeige|Sponsored(?: Ad| Anzeige)?)\b|'
            r'^\d+\s+Angebote?$|^Anzeige\b',
            normalized,
            re.IGNORECASE,
        )) or '/sspa/click' in str(source_url).lower()

    def raise_for_amazon_block(self, html):
        lowered = html.lower()
        if (
            'api-services-support@amazon.com' in lowered
            or 'enter the characters you see below' in lowered
            or 'geben sie die zeichen unten ein' in lowered
        ):
            raise RuntimeError("Zugriff durch Amazon-Captcha blockiert")

    @staticmethod
    def clean_html_text(value):
        # Ein echter Parser statt regulaerer Ausdruecke: Markup wie
        # <a title="a>b"> oder ein unvollstaendiges Tag laesst sich mit
        # <[^>]+> nicht zuverlaessig entfernen.
        value = _TextExtractor.text_of(value)
        value = re.sub(
            r'\(function\([^)]*\)\s*\{.*?\}\)\);?',
            ' ',
            value,
            flags=re.DOTALL,
        )
        value = unicodedata.normalize('NFC', value)
        return re.sub(r'\s+', ' ', value).strip()

    def extract_amazon_product(self, html):
        self.raise_for_amazon_block(html)
        title_match = re.search(
            r'<span[^>]+id=["\']productTitle["\'][^>]*>(.*?)</span>',
            html,
            re.IGNORECASE | re.DOTALL,
        )
        title = self.clean_html_text(title_match.group(1)) if title_match else ''
        facts = []

        feature_start = re.search(
            r'<div[^>]+id=["\']feature-bullets["\'][^>]*>',
            html,
            re.IGNORECASE,
        )
        if feature_start:
            section = html[feature_start.end():feature_start.end() + 30000]
            for item in re.findall(
                r'<li[^>]*>\s*<span[^>]*>(.*?)</span>\s*</li>',
                section,
                re.IGNORECASE | re.DOTALL,
            ):
                text = self.clean_html_text(item)
                if len(text) >= 15 and text not in facts:
                    facts.append(text)
                if len(facts) >= 8:
                    break

        for label, value in re.findall(
            r'<tr[^>]*>\s*<(?:th|td)[^>]*>(.*?)</(?:th|td)>\s*'
            r'<td[^>]*>(.*?)</td>\s*</tr>',
            html,
            re.IGNORECASE | re.DOTALL,
        ):
            clean_label = self.clean_html_text(label)
            clean_value = self.clean_html_text(value)
            fact = f"{clean_label}: {clean_value}"
            if clean_label and clean_value and fact not in facts:
                facts.append(fact)
            if len(facts) >= 12:
                break

        if not facts:
            aplus_start = re.search(
                r'<div[^>]+(?:id|class)=["\'][^"\']*aplus[^"\']*["\'][^>]*>',
                html,
                re.IGNORECASE,
            )
            if aplus_start:
                section = html[aplus_start.end():aplus_start.end() + 100000]
                for paragraph in re.findall(
                    r'<(?:h3|h4|p)[^>]*>(.*?)</(?:h3|h4|p)>',
                    section,
                    re.IGNORECASE | re.DOTALL,
                ):
                    text = self.clean_html_text(paragraph)
                    if len(text) >= 10 and text not in facts:
                        facts.append(text)
                    if len(facts) >= 8:
                        break

        description = '\n'.join(f"• {fact}" for fact in facts)
        if not description and title:
            description = f"Amazon-Produkt: {title}"
        return title, description

    def fetch_url(self, url):
        self.validate_remote_url(url)
        headers = {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/138.0.0.0 Safari/537.36'
            ),
            'Accept': (
                'text/html,application/xhtml+xml,application/xml;q=0.9,'
                'image/avif,image/webp,*/*;q=0.8'
            ),
            'Accept-Language': 'de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7',
            'Cache-Control': 'no-cache',
            'Upgrade-Insecure-Requests': '1',
        }
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as response:
            self.validate_remote_url(response.geturl())
            declared = int(response.headers.get('Content-Length') or 0)
            if declared > MAX_TEXT_RESPONSE_BYTES:
                raise ValueError("Webantwort ist zu groß")
            encoding = response.headers.get_content_charset() or 'utf-8'
            data = response.read(MAX_TEXT_RESPONSE_BYTES + 1)
            if len(data) > MAX_TEXT_RESPONSE_BYTES:
                raise ValueError("Webantwort ist zu groß")
            return data.decode(encoding, errors='replace')

    @staticmethod
    def url_host(value):
        """Liefert den Hostnamen einer Adresse in Kleinschreibung."""
        try:
            host = urllib.parse.urlparse(str(value or '')).hostname or ''
        except ValueError:
            return ''
        return host.casefold().rstrip('.')

    @classmethod
    def host_is(cls, value, *domains):
        """Prüft den Hostnamen einer Adresse, nicht die Zeichenkette.

        ``'amazon.' in url`` trifft auch auf ``https://fremd.test/?x=amazon.de``
        zu. Verglichen wird deshalb der geparste Host mit der Domain selbst
        oder einer ihrer Unterdomains.
        """
        host = cls.url_host(value)
        return any(
            host == domain or host.endswith(f".{domain}")
            for domain in domains
        )

    @classmethod
    def host_has_label(cls, value, label):
        """Prüft die registrierbare Domain, nicht irgendeinen Namensteil.

        Nötig für Anbieter mit vielen Länderdomains: amazon.de und
        amazon.co.uk sollen zählen, ``amazon.de.fremd.test`` dagegen nicht –
        dort ist ``amazon`` nur eine Unterdomain fremder Herkunft.
        """
        parts = cls.url_host(value).split('.')
        wanted = label.casefold()
        if len(parts) >= 2 and parts[-2] == wanted:
            return True
        # Zweistufige Endungen wie co.uk oder com.au.
        return (
            len(parts) >= 3 and parts[-3] == wanted and len(parts[-2]) <= 3
        )

    @staticmethod
    def validate_remote_url(url):
        """Blockiert lokale Dateipfade und direkte private Netzwerkziele."""
        parsed = urllib.parse.urlparse(str(url))
        if parsed.scheme not in ('http', 'https') or not parsed.hostname:
            raise ValueError("Nur HTTP-/HTTPS-Produktlinks sind erlaubt")
        hostname = parsed.hostname.casefold().rstrip('.')
        if hostname == 'localhost' or hostname.endswith('.localhost'):
            raise ValueError("Lokale Netzwerkziele sind nicht erlaubt")
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            return
        if not address.is_global:
            raise ValueError("Private oder lokale IP-Adressen sind nicht erlaubt")

    @staticmethod
    def is_product_page_link(href, base_url):
        """Trennt Produktseiten von Navigation, Filtern und Skriptlinks.

        Kategorie- und Filterlinks wie ``geizhals.de/?fs=…&cat=gehhd`` haben
        keinen eigenen Pfad; ``javascript:;`` ist ueberhaupt kein Ziel.
        """
        if href.casefold().startswith(('javascript:', 'mailto:', '#')):
            return False
        parsed = urllib.parse.urlparse(urllib.parse.urljoin(base_url, href))
        if parsed.scheme not in ('http', 'https'):
            return False
        return parsed.path.strip('/') != ''

    def search_search_page(self, url, base_url):
        html = self.fetch_url(url)
        lowered = html.lower()
        if 'enable javascript and cookies to continue' in lowered:
            raise RuntimeError("Zugriff durch JavaScript-/Cloudflare-Challenge blockiert")
        if 'sorry! something has gone wrong' in lowered:
            raise RuntimeError("Zugriff von der Website abgewiesen")
        links = []

        patterns = [
            r'<a[^>]*href=["\']([^"\']*-v\d+\.html[^"\']*)["\'][^>]*>(.*?)</a>',
            r'<a[^>]*href=["\']([^"\']*OffersOfProduct/\d+_[^"\']+\.html[^"\']*)["\'][^>]*>(.*?)</a>',
            r'<a[^>]*href=["\']([^"\']+)["\'][^>]*class=["\']?[^"\'>]*(?:productlink|productLink|ga_title|product_name|productName|productListItemLink|offerListItem__title|productName)[^"\'>]*["\']?[^>]*>(.*?)</a>',
            r'<a[^>]*href=["\']([^"\']+)["\'][^>]*title=["\']([^"\']+)["\'][^>]*>',
            r'<h2[^>]*>\s*<a[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>\s*</h2>'
        ]

        for pattern in patterns:
            for match in re.findall(pattern, html, re.IGNORECASE | re.DOTALL):
                href = html_lib.unescape(match[0]).strip()
                title = match[1] if len(match) > 1 else ''
                title_clean = re.sub(r'<[^>]+>', '', title).strip()
                title_clean = re.sub(r'\s+', ' ', title_clean)
                if not title_clean or not href:
                    continue
                if not self.is_product_page_link(href, base_url):
                    continue
                if re.search(
                    r'(Geizhals-App|Gesponserte Anzeige|AppStore|Google Play|'
                    r'AppGallery|^Geizhals$|^Geizhals auf |'
                    r'Bitte beachte die Hinweise|Ende der Seite|'
                    r'^\d+\s+Angebote?$)',
                    title_clean,
                    re.IGNORECASE,
                ):
                    continue
                full_url = urllib.parse.urljoin(base_url, href)
                canonical_match = re.search(
                    r'(https?://[^"\']+(?:-v\d+|OffersOfProduct/\d+_[^/?#]+)\.html)',
                    full_url,
                    re.IGNORECASE,
                )
                if canonical_match:
                    full_url = canonical_match.group(1)
                if full_url not in [u for u, _ in links]:
                    links.append((full_url, title_clean))
            if links:
                break

        results = []
        for full_url, title in links[:8]:
            description = f"Online gefunden: {title}"
            results.append((title, description, full_url))
        return results

    def save_file(self):
        """Speichert die Produktbeschreibung als Textdatei"""
        trans = TRANSLATIONS[self.language]
        if not self.selected_variant:
            messagebox.showwarning(
                trans['no_selection'],
                trans['no_selection']
            )
            return

        self.save_visible_platform_draft()
        draft = self.platform_drafts.get(self.current_platform, {})
        errors = self.platform_limit_errors(
            self.current_platform,
            draft.get('title', ''),
            draft.get('description', ''),
        )
        if errors:
            messagebox.showwarning(
                trans['limit_exceeded'], '\n'.join(errors)
            )
            return
        self.persist_platform_draft(self.current_platform, draft)
        
        # Listing generieren
        listing = (
            f"{draft.get('title', '').strip()}\n\n"
            f"{self.full_platform_description(draft.get('description', ''))}\n"
        )
        
        # Speichern im separaten Thread um GUI nicht zu blockieren
        def save_async():
            try:
                if self.opened_file_path:
                    # Eine geöffnete Datei wird bewusst zurückgeschrieben.
                    filepath = Path(self.opened_file_path)
                    filepath.write_text(listing, encoding='utf-8')
                else:
                    name = (
                        f"{draft.get('title') or self.selected_variant['name']}"
                        f"-{self.current_platform}"
                    )
                    # Neue Beiträge überschreiben keine vorhandenen Dateien.
                    filepath = self.generator.save_listing(
                        listing, name, output_dir=self.save_path
                    )

                # Status updaten
                self.root.after(0, lambda path=filepath: self.status_var.set(
                    f"{trans['saved_success']} {path.name}"
                ))
                self.root.after(0, lambda path=filepath: messagebox.showinfo(
                    trans['saved_success'],
                    f"{trans['saved_success']}\n\n{path}"
                ))

            except Exception as exc:
                # Das Lambda läuft erst später im Tk-Event-Loop; der Text muss
                # deshalb jetzt gebunden werden, nicht die Exception selbst.
                self.root.after(0, lambda error=str(exc): messagebox.showerror(
                    trans['save_error'],
                    f"{trans['save_error']}\n\n{error}"
                ))
                self.root.after(0, lambda: self.status_var.set(trans['save_error']))
        
        thread = threading.Thread(target=save_async, daemon=True)
        thread.start()

    def export_product_package(self):
        """Exportiert getrennte Plattformtexte und die aktuelle Bildergalerie."""
        trans = TRANSLATIONS[self.language]
        if not self.selected_variant or not self.product_record_id:
            messagebox.showwarning(
                trans['no_selection'], trans['no_selection']
            )
            return
        self.save_visible_platform_draft()
        problems = []
        for platform, draft in self.platform_drafts.items():
            errors = self.platform_limit_errors(
                platform, draft['title'], draft['description']
            )
            if errors:
                problems.extend(
                    f"{PLATFORM_PROFILES[platform].label_de}: {error}"
                    for error in errors
                )
            self.persist_platform_draft(platform, draft)
        if problems:
            messagebox.showwarning(
                trans['limit_exceeded'], '\n'.join(problems)
            )
            return
        output_root = filedialog.askdirectory(
            title=trans['export_package'],
            initialdir=self.save_path,
        )
        if not output_root:
            return
        product_id = self.product_record_id
        own_paths = [
            image['path'] for image in self.own_images
            if Path(image['path']).is_file()
        ]
        # Eigene Fotos schlagen die Herstellerbilder: wer selbst fotografiert
        # hat, will die Werbebilder nicht im Ordner haben.
        image_urls = (
            [] if own_paths
            else list(self.selected_variant.get('image_urls') or [])
        )

        def worker():
            failed_images = 0
            try:
                folder = self.listing_store.export_package(
                    product_id, output_root, images=own_paths,
                    prepare=prepare_own_image,
                )
                for index, url in enumerate(image_urls, 1):
                    try:
                        data = self.fetch_binary(url)
                        suffix = (
                            Path(urllib.parse.urlparse(url).path).suffix.lower()
                        )
                        if suffix not in ('.jpg', '.jpeg', '.png', '.webp'):
                            suffix = '.jpg'
                        name = (
                            f"{index:02d}-hauptbild{suffix}"
                            if index == 1 else
                            f"{index:02d}-produktbild{suffix}"
                        )
                        (folder / name).write_bytes(data)
                    except Exception:
                        # Einzelne blockierte Bilder brechen den Export nicht
                        # ab, werden aber am Ende ausgewiesen.
                        failed_images += 1
            except Exception as exc:
                self.root.after(
                    0, lambda error=str(exc): messagebox.showerror(
                        trans['save_error'], error
                    )
                )
                return
            notice = (
                f"\n\n{trans['export_images_failed']} {failed_images}"
                if failed_images else ''
            )
            self.root.after(
                0, lambda: messagebox.showinfo(
                    trans['export_package'],
                    f"{trans['export_success']}\n\n{folder}{notice}",
                )
            )

        threading.Thread(target=worker, daemon=True).start()

    def ebay_client(self):
        """Erzeugt einen Client mit den gespeicherten Zugangsdaten."""
        client_id = self.get_secret('ebay_client_id')
        client_secret = self.get_secret('ebay_client_secret')
        if not client_id or not client_secret or not self.ebay_ru_name:
            raise EbayError(TRANSLATIONS[self.language]['ebay_no_credentials'])
        client = EbayListingClient(
            client_id=client_id,
            client_secret=client_secret,
            environment=self.ebay_environment,
        )
        refresh_token = self.get_secret('ebay_refresh_token')
        if refresh_token:
            client.tokens.refresh_token = refresh_token
        return client

    def ebay_draft(self):
        """Stellt aus dem aktuellen Beitrag ein Angebot zusammen."""
        variant = self.selected_variant or {}
        draft = self.platform_drafts.get('ebay', {})
        amount = self.parse_price(self.asking_price_var.get())
        return ListingDraft(
            sku=sku_for(variant.get('name', ''), str(
                variant.get('ean') or variant.get('gtin') or ''
            )),
            title=draft.get('title', '') or variant.get('name', ''),
            description=self.full_platform_description(
                draft.get('description', '')
            ),
            condition=condition_code(self.condition_var.get()),
            price='' if amount is None else f"{amount:.2f}",
            quantity=max(1, int(self.ebay_quantity_var.get() or 1)),
            category_id=str(variant.get('ebay_category_id', '')),
            aspects=dict(self.ebay_aspect_values or {}),
            merchant_location_key=DEFAULT_LOCATION_KEY,
            fulfillment_policy_id=self.ebay_policy_ids.get('fulfillment', ''),
            payment_policy_id=self.ebay_policy_ids.get('payment', ''),
            return_policy_id=self.ebay_policy_ids.get('return', ''),
            image_urls=[],
        )

    def open_ebay_publisher(self):
        """Führt Schritt für Schritt zum offiziell eingestellten Angebot."""
        trans = TRANSLATIONS[self.language]
        if not self.selected_variant or not self.product_record_id:
            messagebox.showwarning(
                trans['no_selection'], trans['no_selection']
            )
            return
        self.save_visible_platform_draft()

        window = tk.Toplevel(self.root)
        window.title(trans['ebay_publish_title'])
        window.transient(self.root.winfo_toplevel())
        outer = ttk.Frame(window, padding=12)
        outer.pack(fill=tk.BOTH, expand=True)

        status_var = tk.StringVar()

        def report(message):
            self.root.after(0, lambda: status_var.set(message))

        def in_thread(action):
            threading.Thread(target=action, daemon=True).start()

        # --- 1. Einwilligung -------------------------------------------
        consent = ttk.LabelFrame(
            outer, text=trans['ebay_consent_frame'], padding=8
        )
        consent.pack(fill=tk.X)
        ttk.Label(
            consent, text=trans['ebay_consent_hint'],
            wraplength=520, justify=tk.LEFT, foreground='#555555',
        ).pack(fill=tk.X)
        runame_row = ttk.Frame(consent)
        runame_row.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(
            runame_row, text=trans['ebay_runame_label'], width=26
        ).pack(side=tk.LEFT)
        runame_var = tk.StringVar(value=self.ebay_ru_name)
        ttk.Entry(runame_row, textvariable=runame_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True
        )
        consent_state = tk.StringVar(
            value=trans['ebay_consent_present']
            if self.get_secret('ebay_refresh_token')
            else trans['ebay_consent_missing']
        )
        ttk.Label(consent, textvariable=consent_state).pack(
            fill=tk.X, pady=(6, 0)
        )

        def start_consent():
            self.ebay_ru_name = runame_var.get().strip()
            self.save_config()
            try:
                # Der Statuswert bindet die Antwort an genau diese Anfrage.
                self._ebay_consent_state = secrets.token_urlsafe(16)
                url = consent_url(
                    self.get_secret('ebay_client_id'),
                    self.ebay_ru_name,
                    self.ebay_environment,
                    state=self._ebay_consent_state,
                )
            except EbayError as error:
                report(str(error))
                return
            webbrowser.open(url)

        ttk.Button(
            consent, text=trans['ebay_consent_start'], command=start_consent
        ).pack(anchor=tk.W, pady=(6, 0))
        ttk.Label(consent, text=trans['ebay_consent_paste']).pack(
            anchor=tk.W, pady=(6, 0)
        )
        redirect_var = tk.StringVar()
        ttk.Entry(consent, textvariable=redirect_var).pack(fill=tk.X)

        def save_consent():
            self.ebay_ru_name = runame_var.get().strip()
            self.save_config()

            def run():
                try:
                    code = authorization_code(
                        redirect_var.get(),
                        getattr(self, '_ebay_consent_state', ''),
                    )
                    client = self.ebay_client()
                    tokens = client.exchange_code(code, self.ebay_ru_name)
                    # Nur der Erneuerungstoken wird dauerhaft abgelegt.
                    self.set_secret('ebay_refresh_token', tokens.refresh_token)
                    self.audit_security_event('ebay_consent', 'ebay')
                except Exception as error:
                    self.audit_security_event(
                        'ebay_consent', 'ebay', 'failed'
                    )
                    report(str(error))
                    return
                self.root.after(0, lambda: (
                    consent_state.set(trans['ebay_consent_present']),
                    redirect_var.set(''),
                ))
                report(trans['ebay_consent_saved'])

            in_thread(run)

        consent_buttons = ttk.Frame(consent)
        consent_buttons.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(
            consent_buttons, text=trans['ebay_consent_save'],
            command=save_consent,
        ).pack(side=tk.LEFT)

        def revoke_consent():
            """Entfernt nur die Anmeldung, nicht die API-Zugangsdaten."""
            if not messagebox.askyesno(
                trans['ebay_publish_title'],
                trans['ebay_consent_revoke_confirm'],
                parent=window,
                default=messagebox.NO,
            ):
                return
            try:
                self.delete_secret('ebay_refresh_token')
                self.audit_security_event('ebay_consent_deleted', 'ebay')
            except Exception as error:
                report(str(error))
                return
            consent_state.set(trans['ebay_consent_missing'])
            status_var.set(trans['ebay_consent_revoked'])

        ttk.Button(
            consent_buttons, text=trans['ebay_consent_revoke'],
            command=revoke_consent,
        ).pack(side=tk.LEFT, padx=(6, 0))

        # --- 2. Richtlinien und Standort -------------------------------
        policies = ttk.LabelFrame(
            outer, text=trans['ebay_policies_frame'], padding=8
        )
        policies.pack(fill=tk.X, pady=(10, 0))
        policy_vars = {}
        policy_options = {}
        for row, (kind, label_key) in enumerate((
            ('fulfillment', 'ebay_policy_fulfillment'),
            ('payment', 'ebay_policy_payment'),
            ('return', 'ebay_policy_return'),
        )):
            ttk.Label(policies, text=trans[label_key], width=14).grid(
                row=row, column=0, sticky=tk.W, pady=2
            )
            policy_vars[kind] = tk.StringVar()
            combo = ttk.Combobox(
                policies, textvariable=policy_vars[kind],
                state='readonly', width=44,
            )
            combo.grid(row=row, column=1, sticky=tk.EW, pady=2)
            policy_options[kind] = combo
        policies.columnconfigure(1, weight=1)

        address_row = ttk.Frame(policies)
        address_row.grid(row=3, column=0, columnspan=2, sticky=tk.EW, pady=(6, 0))
        ttk.Label(address_row, text=trans['ebay_postal_code']).pack(side=tk.LEFT)
        postal_var = tk.StringVar(value=self.ebay_postal_code)
        ttk.Entry(address_row, textvariable=postal_var, width=10).pack(
            side=tk.LEFT, padx=(4, 12)
        )
        ttk.Label(address_row, text=trans['ebay_country']).pack(side=tk.LEFT)
        country_var = tk.StringVar(value=self.ebay_country)
        ttk.Entry(address_row, textvariable=country_var, width=6).pack(
            side=tk.LEFT, padx=(4, 0)
        )

        def load_policies():
            def run():
                try:
                    client = self.ebay_client()
                    found = client.policies()
                except Exception as error:
                    report(str(error))
                    return

                def apply():
                    for kind, entries in found.items():
                        labels = [entry['name'] for entry in entries]
                        policy_options[kind].configure(values=labels)
                        self._ebay_policy_entries[kind] = entries
                        if entries:
                            policy_vars[kind].set(labels[0])
                            self.ebay_policy_ids[kind] = entries[0]['id']
                    status_var.set(trans['ebay_ready'])

                self.root.after(0, apply)

            in_thread(run)

        def on_policy_selected(kind):
            entries = self._ebay_policy_entries.get(kind, [])
            index = policy_options[kind].current()
            if 0 <= index < len(entries):
                self.ebay_policy_ids[kind] = entries[index]['id']

        for kind, combo in policy_options.items():
            combo.bind(
                '<<ComboboxSelected>>',
                lambda event, name=kind: on_policy_selected(name),
            )
        ttk.Button(
            policies, text=trans['ebay_policies_load'], command=load_policies
        ).grid(row=4, column=0, columnspan=2, sticky=tk.W, pady=(6, 0))

        # --- 3. Angebot -------------------------------------------------
        offer = ttk.LabelFrame(
            outer, text=trans['ebay_offer_frame'], padding=8
        )
        offer.pack(fill=tk.X, pady=(10, 0))
        quantity_row = ttk.Frame(offer)
        quantity_row.pack(fill=tk.X)
        ttk.Label(quantity_row, text=trans['ebay_quantity']).pack(side=tk.LEFT)
        ttk.Spinbox(
            quantity_row, from_=1, to=99, width=5,
            textvariable=self.ebay_quantity_var,
        ).pack(side=tk.LEFT, padx=(4, 0))

        ttk.Label(
            outer, textvariable=status_var, wraplength=540,
            justify=tk.LEFT, foreground='#555555',
        ).pack(fill=tk.X, pady=(10, 0))

        def check():
            draft = self.ebay_draft()
            if not draft.category_id:
                status_var.set(trans['ebay_no_category'])
                return None
            missing = [
                name for name in draft.missing_fields() if name != 'images'
            ]
            if not self.own_images:
                missing.append('images')
            status_var.set(
                f"{trans['ebay_publish_missing']} {', '.join(missing)}"
                if missing else trans['ebay_ready']
            )
            return draft if not missing else None

        def publish():
            draft = check()
            if draft is None:
                return
            variant = self.selected_variant or {}
            if not messagebox.askyesno(
                trans['ebay_publish_title'],
                trans['ebay_confirm'].format(
                    title=draft.title,
                    price=draft.price,
                    category=variant.get('ebay_category_name', draft.category_id),
                    images=len(self.own_images),
                    environment=self.ebay_environment,
                ),
                default=messagebox.NO,
                icon=messagebox.WARNING,
            ):
                return
            photos = [
                image['path'] for image in self.own_images
                if Path(image['path']).is_file()
            ]
            address = {
                'postalCode': postal_var.get().strip(),
                'country': country_var.get().strip().upper() or 'DE',
            }
            self.ebay_postal_code = address['postalCode']
            self.ebay_country = address['country']
            self.save_config()
            report(trans['ebay_working'])

            def run():
                try:
                    client = self.ebay_client()
                    draft.image_urls = [
                        client.upload_picture(path) for path in photos
                    ]
                    client.ensure_location(
                        draft.merchant_location_key, address
                    )
                    client.create_inventory_item(draft)
                    offer_id = client.create_offer(draft)
                    listing_id = client.publish_offer(offer_id)
                except Exception as error:
                    self.audit_security_event('ebay_publish', 'ebay', 'failed')
                    report(str(error))
                    return
                self.audit_security_event('ebay_publish', 'ebay')
                report(f"{trans['ebay_published']} {listing_id}")

            in_thread(run)

        buttons = ttk.Frame(outer)
        buttons.pack(fill=tk.X, pady=(10, 0))
        ttk.Button(
            buttons, text=trans['ebay_check'], command=check
        ).pack(side=tk.LEFT)
        ttk.Button(
            buttons, text=trans['ebay_publish_action'], command=publish
        ).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(
            buttons, text=trans['dialog_close'], command=window.destroy
        ).pack(side=tk.RIGHT)

    def copy_listing(self):
        """Kopiert den vollständigen Beitrag inklusive Pflichttext."""
        trans = TRANSLATIONS[self.language]
        if not self.selected_variant:
            messagebox.showwarning(trans['no_selection'], trans['no_selection'])
            return
        self.save_visible_platform_draft()
        draft = self.platform_drafts.get(self.current_platform, {})
        errors = self.platform_limit_errors(
            self.current_platform,
            draft.get('title', ''),
            draft.get('description', ''),
        )
        if errors:
            messagebox.showwarning(
                trans['limit_exceeded'], '\n'.join(errors)
            )
            return
        self.persist_platform_draft(self.current_platform, draft)
        listing = (
            f"{draft.get('title', '').strip()}\n\n"
            f"{self.full_platform_description(draft.get('description', ''))}"
        )
        self.root.clipboard_clear()
        self.root.clipboard_append(listing)
        self.status_var.set(trans['copied_success'])


class TabbedProductGeneratorGUI:
    """Verwaltet mehrere vollständig unabhängige Verkaufsbeiträge."""

    def __init__(self, root):
        self.root = root
        self.root.geometry("1200x900")
        self.root.resizable(True, True)
        self.root.title(TRANSLATIONS['de']['title'])
        self.controllers = {}
        self.retired_tabs = []
        self.tab_counter = 0
        self._session_fingerprint = None
        self.session_file = Path.home() / ".eBayCreationToolSession.json"
        self.config_file = Path.home() / ".eBayCreationToolConfig.json"
        try:
            app_config = json.loads(
                self.config_file.read_text(encoding='utf-8')
            )
        except Exception:
            app_config = {}
        self.restore_session_enabled = bool(
            app_config.get('restore_session', True)
        )
        self.clear_session_on_exit = bool(
            app_config.get('clear_session_on_exit', False)
        )

        toolbar = ttk.Frame(root, padding=(8, 6))
        toolbar.pack(fill=tk.X)
        self.new_tab_button = ttk.Button(
            toolbar,
            text=TRANSLATIONS['de']['new_tab'],
            command=self.add_tab,
        )
        self.new_tab_button.pack(side=tk.LEFT, padx=(0, 6))
        self.close_tab_button = ttk.Button(
            toolbar,
            text=TRANSLATIONS['de']['close_tab'],
            command=self.close_current_tab,
        )
        self.close_tab_button.pack(side=tk.LEFT)
        self.export_button = ttk.Button(
            toolbar,
            text=TRANSLATIONS['de']['export_button'],
            command=lambda: self.run_on_active(ProductGeneratorGUI.save_file),
        )
        self.export_button.pack(side=tk.LEFT, padx=(12, 6))
        self.copy_button = ttk.Button(
            toolbar,
            text=TRANSLATIONS['de']['copy_button'],
            command=lambda: self.run_on_active(
                ProductGeneratorGUI.copy_listing
            ),
        )
        self.copy_button.pack(side=tk.LEFT)
        self.package_button = ttk.Button(
            toolbar,
            text=TRANSLATIONS['de']['export_package'],
            command=lambda: self.run_on_active(
                ProductGeneratorGUI.export_product_package
            ),
        )
        self.package_button.pack(side=tk.LEFT, padx=(6, 0))
        self.ebay_publish_button = ttk.Button(
            toolbar,
            text=TRANSLATIONS['de']['ebay_publish'],
            command=lambda: self.run_on_active(
                ProductGeneratorGUI.open_ebay_publisher
            ),
        )
        self.ebay_publish_button.pack(side=tk.LEFT, padx=(6, 0))

        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 6))

        # tearoff=0: sonst verschiebt ein unsichtbarer Eintrag alle Indizes und
        # update_chrome_language bricht mit TclError ab.
        self.menubar = tk.Menu(root, tearoff=0)
        self.file_menu = tk.Menu(self.menubar, tearoff=0)
        self.file_menu.add_command(
            label=TRANSLATIONS['de']['menu_new'], command=self.add_tab
        )
        self.file_menu.add_command(
            label=TRANSLATIONS['de']['menu_open'], command=self.open_file
        )
        self.file_menu.add_command(
            label=TRANSLATIONS['de']['menu_save'],
            command=lambda: self.run_on_active(ProductGeneratorGUI.save_file),
        )
        self.file_menu.add_separator()
        self.file_menu.add_command(
            label=TRANSLATIONS['de']['menu_exit'], command=self.on_close
        )
        self.menubar.add_cascade(
            label=TRANSLATIONS['de']['menu_file'], menu=self.file_menu
        )
        self.menubar.add_command(
            label=TRANSLATIONS['de']['menu_settings'],
            command=self.open_settings,
        )
        root.config(menu=self.menubar)
        self.notebook.bind('<<NotebookTabChanged>>', self.on_tab_changed)
        self.settings_window = None
        if not self.restore_session_enabled or not self.restore_session():
            self.add_tab()
        self.root.after(SESSION_AUTOSAVE_MS, self.autosave_session)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def update_chrome_language(self, language):
        trans = TRANSLATIONS.get(language, TRANSLATIONS['de'])
        self.root.title(trans['title'])
        self.new_tab_button.config(text=trans['new_tab'])
        self.close_tab_button.config(text=trans['close_tab'])
        self.export_button.config(text=trans['export_button'])
        self.copy_button.config(text=trans['copy_button'])
        self.package_button.config(text=trans['export_package'])
        self.ebay_publish_button.config(text=trans['ebay_publish'])
        self.file_menu.entryconfig(0, label=trans['menu_new'])
        self.file_menu.entryconfig(1, label=trans['menu_open'])
        self.file_menu.entryconfig(2, label=trans['menu_save'])
        self.file_menu.entryconfig(4, label=trans['menu_exit'])
        self.menubar.entryconfig(0, label=trans['menu_file'])
        self.menubar.entryconfig(1, label=trans['menu_settings'])

    def open_file(self):
        """Öffnet einen vorhandenen TXT-Beitrag in einem eigenen Tab."""
        controller = self.active_controller()
        language = controller.language if controller else 'de'
        trans = TRANSLATIONS[language]
        filename = filedialog.askopenfilename(
            title=trans['open_file_title'],
            filetypes=[
                ("Textdateien", "*.txt"),
                ("Alle Dateien", "*.*"),
            ],
        )
        if not filename:
            return
        try:
            text = Path(filename).read_text(encoding='utf-8-sig')
        except UnicodeDecodeError:
            text = Path(filename).read_text(encoding='cp1252')
        except Exception as exc:
            messagebox.showerror(trans['open_file_title'], str(exc))
            return

        # Der Pflichttext wird in der Live-Vorschau separat dargestellt.
        markers = [
            text.find(clause)
            for clause in (controller.legal_clause, WARRANTY_CLAUSE)
            if clause and text.find(clause) >= 0
        ]
        if markers:
            text = text[:min(markers)]
        text = re.sub(r'\s*---\s*$', '', text.rstrip()).rstrip()

        controller = self.add_tab()
        product_name = Path(filename).stem
        controller.opened_file_path = str(Path(filename))
        controller.search_var.set(product_name)
        if controller._search_after_id is not None:
            controller.root.after_cancel(controller._search_after_id)
            controller._search_after_id = None
        controller._search_generation += 1
        variant = {
            'name': product_name,
            'description': {'de': text, 'en': text},
            'source_url': '',
            'sources': ['Datei'],
            'quality': 'exakt',
        }
        controller.selected_variant = variant
        controller.search_results = [{
            'group_id': 'file',
            'variant': variant,
        }]
        controller.variant_listbox.delete(0, tk.END)
        controller.variant_listbox.insert(
            tk.END, controller.result_display_label(
                controller.search_results[0]
            )
        )
        controller.variant_listbox.selection_set(0)
        controller.preview_text.delete('1.0', tk.END)
        controller.preview_text.insert('1.0', text)
        controller.initialize_listing_assistant(variant, text)
        controller.render_live_preview()
        if controller.title_callback:
            controller.title_callback(product_name)

    def open_settings(self):
        """Zeigt die Konfiguration des aktiven Beitrags separat an."""
        controller = self.active_controller()
        if not controller:
            return
        if self.settings_window and self.settings_window.winfo_exists():
            self.settings_window.destroy()

        trans = TRANSLATIONS[controller.language]
        window = tk.Toplevel(self.root)
        self.settings_window = window
        window.title(trans['options_frame'])
        window.transient(self.root)
        window.resizable(True, False)
        window.protocol("WM_DELETE_WINDOW", window.destroy)

        content = ttk.Frame(window, padding=14)
        content.pack(fill=tk.BOTH, expand=True)

        ttk.Label(
            content, text=trans['path_label'], font=("Segoe UI", 9, "bold")
        ).pack(anchor=tk.W)
        ttk.Label(
            content, text=controller.save_path, foreground="#1a73e8"
        ).pack(anchor=tk.W, fill=tk.X, pady=(2, 8))

        path_buttons = ttk.Frame(content)
        path_buttons.pack(fill=tk.X, pady=(0, 12))
        ttk.Button(
            path_buttons,
            text=trans['menu_change_save_path'],
            command=lambda: self._settings_path_action(
                controller, ProductGeneratorGUI.change_save_path
            ),
        ).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(
            path_buttons,
            text=trans['menu_default_save_path'],
            command=lambda: self._settings_path_action(
                controller, ProductGeneratorGUI.set_default_save_path
            ),
        ).pack(side=tk.LEFT)

        language_row = ttk.Frame(content)
        language_row.pack(fill=tk.X, pady=(0, 12))
        ttk.Label(language_row, text=trans['language_label']).pack(side=tk.LEFT)

        def change_language(value):
            controller.on_language_changed(value)
            window.destroy()
            self.open_settings()

        ttk.OptionMenu(
            language_row,
            controller.language_var,
            controller.language,
            "de",
            "en",
            command=change_language,
        ).pack(side=tk.LEFT, padx=10)

        providers = ttk.LabelFrame(
            content, text=trans['provider_frame'], padding=8
        )
        providers.pack(fill=tk.X, pady=(0, 12))
        for index, (name, (_, label_key)) in enumerate(
            controller.provider_buttons.items()
        ):
            tk.Checkbutton(
                providers,
                text=trans[label_key],
                variable=controller.provider_vars[name],
                command=controller.save_config,
                anchor=tk.W,
                borderwidth=0,
                highlightthickness=0,
            ).grid(
                row=index // 3, column=index % 3,
                sticky=tk.W, padx=(0, 18), pady=2,
            )

        marketplace_frame = ttk.LabelFrame(
            content, text=trans['marketplace_api_frame'], padding=8
        )
        marketplace_frame.pack(fill=tk.X, pady=(0, 12))
        secret_entries = {}
        for row, (secret_name, label_key) in enumerate((
            ('kleinanzeigen_api_key', 'kleinanzeigen_api_key'),
            ('ebay_client_id', 'ebay_client_id'),
            ('ebay_client_secret', 'ebay_client_secret'),
        )):
            ttk.Label(
                marketplace_frame, text=trans[label_key]
            ).grid(row=row, column=0, sticky=tk.W, padx=(0, 8), pady=3)
            entry = ttk.Entry(marketplace_frame, show='•', width=54)
            entry.grid(row=row, column=1, sticky=tk.EW, pady=3)
            secret_entries[secret_name] = entry
            stored = bool(controller.get_secret(secret_name))
            entry._has_stored_secret = stored
            if stored:
                entry.insert(0, SECRET_PLACEHOLDER)

            def clear_placeholder(event, widget=entry):
                if widget.get() == SECRET_PLACEHOLDER:
                    widget.delete(0, tk.END)

            def restore_placeholder(event, widget=entry):
                if (
                    not widget.get().strip()
                    and getattr(widget, '_has_stored_secret', False)
                ):
                    widget.insert(0, SECRET_PLACEHOLDER)

            entry.bind('<FocusIn>', clear_placeholder)
            entry.bind('<FocusOut>', restore_placeholder)
            status = (
                trans['secret_saved_status']
                if stored
                else trans['secret_missing_status']
            )
            ttk.Label(
                marketplace_frame, text=status, foreground='#555555'
            ).grid(row=row, column=2, sticky=tk.W, padx=(8, 0))
        marketplace_frame.columnconfigure(1, weight=1)

        ebay_environment_var = tk.StringVar(
            value=controller.ebay_environment
        )
        ttk.Label(
            marketplace_frame, text=trans['ebay_environment']
        ).grid(row=3, column=0, sticky=tk.W, padx=(0, 8), pady=3)
        ttk.OptionMenu(
            marketplace_frame,
            ebay_environment_var,
            controller.ebay_environment,
            'production',
            'sandbox',
        ).grid(row=3, column=1, sticky=tk.W, pady=3)

        api_actions = ttk.Frame(marketplace_frame)
        api_actions.grid(
            row=4, column=0, columnspan=3, sticky=tk.W, pady=(6, 2)
        )
        ttk.Button(
            api_actions,
            text=f"{trans['secret_test']} – Kleinanzeigen",
            command=lambda: self._test_marketplace_connection(
                controller, window, 'kleinanzeigen', secret_entries,
                ebay_environment_var
            ),
        ).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(
            api_actions,
            text=f"{trans['secret_test']} – eBay",
            command=lambda: self._test_marketplace_connection(
                controller, window, 'ebay', secret_entries,
                ebay_environment_var
            ),
        ).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(
            api_actions,
            text=trans['secret_delete'],
            command=lambda: self._delete_marketplace_credentials(
                controller, window
            ),
        ).pack(side=tk.LEFT)
        ttk.Label(
            marketplace_frame,
            text=trans['secret_hint'],
            foreground='#555555',
        ).grid(row=5, column=0, columnspan=3, sticky=tk.W, pady=(5, 0))

        session_frame = ttk.LabelFrame(
            content, text=trans['session_frame'], padding=8
        )
        session_frame.pack(fill=tk.X, pady=(0, 12))
        restore_session_var = tk.BooleanVar(
            value=controller.restore_session_enabled
        )
        clear_session_var = tk.BooleanVar(
            value=controller.clear_session_on_exit
        )
        ttk.Checkbutton(
            session_frame,
            text=trans['session_restore'],
            variable=restore_session_var,
        ).pack(anchor=tk.W)
        ttk.Checkbutton(
            session_frame,
            text=trans['session_clear_on_exit'],
            variable=clear_session_var,
        ).pack(anchor=tk.W)
        try:
            database_size = controller.listing_store.path.stat().st_size
        except OSError:
            database_size = 0
        ttk.Label(
            session_frame,
            text=(
                f"{trans['database_label']} "
                f"{controller.listing_store.path} "
                f"({database_size / 1024:.1f} KB)"
            ),
            foreground='#555555',
            wraplength=600,
        ).pack(anchor=tk.W, pady=(6, 0))

        font_row = ttk.Frame(content)
        font_row.pack(fill=tk.X, pady=(0, 12))
        ttk.Label(font_row, text=trans['font_size_label']).pack(side=tk.LEFT)
        font_spinbox = ttk.Spinbox(
            font_row,
            from_=8,
            to=18,
            increment=1,
            textvariable=controller.font_size_var,
            width=5,
            command=controller.on_font_size_changed,
        )
        font_spinbox.pack(side=tk.LEFT, padx=10)
        font_spinbox.bind(
            '<Return>', lambda event: controller.on_font_size_changed()
        )
        font_spinbox.bind(
            '<FocusOut>', lambda event: controller.on_font_size_changed()
        )

        legal_frame = ttk.LabelFrame(
            content, text=trans['legal_edit_label'], padding=8
        )
        legal_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 12))
        legal_editor = tk.Text(
            legal_frame, height=5, wrap=tk.WORD,
            font=("Segoe UI", max(9, controller.font_size - 1)),
        )
        legal_editor.pack(fill=tk.BOTH, expand=True)
        legal_editor.insert('1.0', controller.legal_clause)
        ttk.Button(
            legal_frame,
            text=trans['legal_reset'],
            command=lambda: (
                legal_editor.delete('1.0', tk.END),
                legal_editor.insert('1.0', WARRANTY_CLAUSE),
            ),
        ).pack(anchor=tk.W, pady=(6, 0))

        actions = ttk.Frame(content)
        actions.pack(fill=tk.X)
        ttk.Button(
            actions,
            text=trans['save_button'],
            command=lambda: self._save_settings(
                controller, window, legal_editor, secret_entries,
                ebay_environment_var, restore_session_var, clear_session_var
            ),
        ).pack(side=tk.LEFT)
        ttk.Button(
            actions, text=trans['close_button'], command=window.destroy
        ).pack(side=tk.RIGHT)

        window.update_idletasks()
        window.minsize(max(650, window.winfo_reqwidth()), window.winfo_reqheight())
        window.grab_set()
        window.focus_set()

    def _test_marketplace_connection(
        self, controller, window, provider, secret_entries=None,
        ebay_environment_var=None,
    ):
        """Testet bewusst nur Authentifizierung und einen kleinen API-Aufruf."""
        trans = TRANSLATIONS[controller.language]
        try:
            relevant_names = (
                ('kleinanzeigen_api_key',)
                if provider == 'kleinanzeigen'
                else ('ebay_client_id', 'ebay_client_secret')
            )
            for name in relevant_names:
                entry = (secret_entries or {}).get(name)
                value = controller.entered_secret(
                    entry.get() if entry is not None else ''
                )
                if value:
                    controller.set_secret(name, value)
            if provider == 'ebay':
                controller.ebay_environment = (
                    ebay_environment_var.get()
                    if ebay_environment_var is not None else 'production'
                )
                controller._ebay_access_token = None
                controller._ebay_access_token_expires = 0
                controller.get_ebay_access_token()
            else:
                # Eine minimale Live-Suche validiert den Key; sie kostet
                # entsprechend dem Anbieter einen Credit.
                controller.test_kleinanzeigen_agent_connection()
            controller.audit_security_event(
                'connection_test', provider, 'success'
            )
            messagebox.showinfo(
                trans['marketplace_api_frame'],
                trans['secret_test_success'],
                parent=window,
            )
        except Exception as exc:
            controller.audit_security_event(
                'connection_test', provider, 'failed'
            )
            messagebox.showerror(
                trans['marketplace_api_frame'],
                f"{trans['secret_test_failed']}\n\n{exc}",
                parent=window,
            )

    def _delete_marketplace_credentials(self, controller, window):
        trans = TRANSLATIONS[controller.language]
        if not messagebox.askyesno(
            trans['marketplace_api_frame'],
            trans['secret_delete_confirm'],
            parent=window,
        ):
            return
        try:
            for name in (
                'kleinanzeigen_api_key', 'ebay_client_id',
                'ebay_client_secret',
                # Die erteilte Einwilligung gehoert mit geloescht, sonst
                # bliebe der Zugriff auf das eBay-Konto bestehen.
                'ebay_refresh_token',
            ):
                controller.delete_secret(name)
            controller._ebay_access_token = None
            controller._ebay_access_token_expires = 0
            controller.audit_security_event(
                'credentials_deleted', 'all', 'success'
            )
        except Exception as exc:
            messagebox.showerror(
                trans['marketplace_api_frame'], str(exc), parent=window
            )
            return
        window.destroy()
        self.open_settings()

    def _save_settings(
        self, controller, window, legal_editor=None, secret_entries=None,
        ebay_environment_var=None, restore_session_var=None,
        clear_session_var=None,
    ):
        """Speichert nur die Konfiguration, niemals einen Verkaufsbeitrag."""
        controller.on_font_size_changed()
        legal_clause = (
            legal_editor.get('1.0', tk.END).strip()
            if legal_editor is not None else controller.legal_clause
        ) or WARRANTY_CLAUSE
        if (
            legal_clause != WARRANTY_CLAUSE
            and legal_clause != controller.legal_clause
            and not messagebox.askyesno(
                TRANSLATIONS[controller.language]['legal_warning_title'],
                TRANSLATIONS[controller.language]['legal_warning'],
                parent=window,
            )
        ):
            return
        try:
            for name, entry in (secret_entries or {}).items():
                value = controller.entered_secret(entry.get())
                if value:
                    controller.set_secret(name, value)
                    controller.audit_security_event(
                        'credential_saved',
                        'kleinanzeigen' if name.startswith('kleinanzeigen') else 'ebay',
                    )
        except Exception as exc:
            messagebox.showerror(
                TRANSLATIONS[controller.language]['marketplace_api_frame'],
                f"{TRANSLATIONS[controller.language]['secret_store_error']}\n\n{exc}",
                parent=window,
            )
            return
        controller._ebay_access_token = None
        controller._ebay_access_token_expires = 0
        controller.ebay_environment = (
            ebay_environment_var.get()
            if ebay_environment_var is not None else controller.ebay_environment
        )
        controller.restore_session_enabled = (
            bool(restore_session_var.get())
            if restore_session_var is not None
            else controller.restore_session_enabled
        )
        controller.clear_session_on_exit = (
            bool(clear_session_var.get())
            if clear_session_var is not None
            else controller.clear_session_on_exit
        )
        controller.save_config()
        settings = {
            'language': controller.language,
            'font_size': controller.font_size,
            'save_path': controller.save_path,
            'legal_clause': legal_clause,
            'ebay_environment': controller.ebay_environment,
            'restore_session': controller.restore_session_enabled,
            'clear_session_on_exit': controller.clear_session_on_exit,
            'providers': {
                name: bool(variable.get())
                for name, variable in controller.provider_vars.items()
            },
        }
        for other in self.controllers.values():
            other.save_path = settings['save_path']
            other.path_label.config(text=other.save_path)
            other.set_legal_clause(settings['legal_clause'])
            other.ebay_environment = settings['ebay_environment']
            other.restore_session_enabled = settings['restore_session']
            other.clear_session_on_exit = settings['clear_session_on_exit']
            for name, enabled in settings['providers'].items():
                if name in other.provider_vars:
                    other.provider_vars[name].set(enabled)
            other.font_size_var.set(settings['font_size'])
            other.on_font_size_changed()
            if other.language != settings['language']:
                other.language_var.set(settings['language'])
                other.on_language_changed(settings['language'])
            other.save_config()
            other.status_var.set(
                TRANSLATIONS[other.language]['settings_saved']
            )
        self.restore_session_enabled = settings['restore_session']
        self.clear_session_on_exit = settings['clear_session_on_exit']
        if not self.restore_session_enabled:
            self.delete_session_file()
        if window.winfo_exists():
            window.destroy()

    def _settings_path_action(self, controller, method):
        method(controller)
        if self.settings_window and self.settings_window.winfo_exists():
            self.settings_window.destroy()
        self.open_settings()

    def on_tab_changed(self, *args):
        controller = self.active_controller()
        if controller:
            self.update_chrome_language(controller.language)

    def run_on_active(self, method):
        """Führt eine Controller-Methode auf dem aktiven Tab aus."""
        controller = self.active_controller()
        if controller:
            method(controller)

    def active_controller(self):
        selected = self.notebook.select()
        return self.controllers.get(selected)

    def serialize_session(self):
        tabs = []
        for tab_id in self.notebook.tabs():
            controller = self.controllers.get(tab_id)
            if not controller:
                continue
            tabs.append({
                'query': controller.search_var.get().strip(),
                'variant': controller.selected_variant,
                'draft': controller.preview_text.get(
                    '1.0', tk.END
                ).rstrip(),
                'language': controller.language,
                'opened_file_path': controller.opened_file_path,
                'platform': controller.current_platform,
                'platform_drafts': controller.platform_drafts,
                'condition': controller.condition_var.get(),
                'scope': controller.scope_var.get(),
                'asking_price': controller.asking_price_var.get(),
                'price_type': controller.price_type_var.get(),
                'price_basis': controller.price_basis_var.get(),
            })
        return {
            'active': self.notebook.index(self.notebook.select())
            if self.notebook.tabs() else 0,
            'tabs': tabs,
        }

    def save_session(self, only_if_changed=False):
        if not self.restore_session_enabled:
            self.delete_session_file()
            return
        try:
            data = self.serialize_session()
            if only_if_changed:
                # Unveränderte Sitzungen nicht erneut auf die Platte schreiben.
                fingerprint = json.dumps(data, ensure_ascii=False, sort_keys=True)
                if fingerprint == self._session_fingerprint:
                    return
                self._session_fingerprint = fingerprint
            temporary = self.session_file.with_suffix('.tmp')
            with open(temporary, 'w', encoding='utf-8') as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
            restrict_to_owner(temporary)
            os.replace(temporary, self.session_file)
        except Exception:
            pass

    def delete_session_file(self):
        try:
            self.session_file.unlink(missing_ok=True)
            self.session_file.with_suffix('.tmp').unlink(missing_ok=True)
        except Exception:
            pass

    def autosave_session(self):
        if not self.root.winfo_exists():
            return
        self.save_session(only_if_changed=True)
        self.root.after(SESSION_AUTOSAVE_MS, self.autosave_session)

    def restore_session(self):
        if not self.restore_session_enabled:
            return False
        try:
            with open(self.session_file, 'r', encoding='utf-8') as handle:
                data = json.load(handle)
        except Exception:
            return False
        tabs = data.get('tabs') or []
        if not tabs:
            return False
        for saved in tabs:
            controller = self.add_tab()
            controller.opened_file_path = saved.get('opened_file_path')
            language = saved.get('language')
            if language in TRANSLATIONS and language != controller.language:
                controller.language_var.set(language)
                controller.on_language_changed(language)
            query = str(saved.get('query') or '')
            controller.search_var.set(query)
            if controller._search_after_id is not None:
                controller.root.after_cancel(controller._search_after_id)
                controller._search_after_id = None
            controller._search_generation += 1
            variant = saved.get('variant')
            if isinstance(variant, dict) and variant.get('name'):
                if 'amazon.' in variant.get('source_url', ''):
                    variant['name'] = (
                        controller.complete_known_title_fragment(
                            variant['name']
                        )
                    )
                controller.selected_variant = variant
                controller.search_results = [{
                    'group_id': 'restored',
                    'variant': variant,
                }]
                controller.variant_listbox.delete(0, tk.END)
                controller.variant_listbox.insert(tk.END, variant['name'])
                controller.variant_listbox.selection_set(0)
                if controller.title_callback:
                    controller.title_callback(variant['name'])
            draft = str(saved.get('draft') or '')
            if (
                isinstance(variant, dict)
                and 'amazon.' in variant.get('source_url', '')
            ):
                draft = controller.repair_known_fragments_in_text(draft)
            controller.preview_text.delete('1.0', tk.END)
            controller.preview_text.insert('1.0', draft)
            if isinstance(variant, dict) and variant.get('name'):
                controller.initialize_listing_assistant(variant, draft)
                saved_drafts = saved.get('platform_drafts')
                if isinstance(saved_drafts, dict) and saved_drafts:
                    # Wie beim Laden aus der Datenbank: eine inzwischen
                    # entfernte Plattform darf den Beitrag nicht lahmlegen.
                    controller.platform_drafts = {
                        key: value for key, value in saved_drafts.items()
                        if key in PLATFORM_PROFILES
                    }
                platform = saved.get('platform', 'kleinanzeigen')
                if platform in PLATFORM_PROFILES:
                    controller.current_platform = platform
                    controller.platform_var.set(
                        PLATFORM_PROFILES[platform].label_de
                    )
                controller.condition_var.set(
                    saved.get('condition')
                    or TRANSLATIONS[controller.language][
                        'condition_values'
                    ].split('|')[0]
                )
                controller.scope_var.set(str(saved.get('scope') or ''))
                controller.asking_price_var.set(
                    str(saved.get('asking_price') or '')
                )
                controller.price_type_var.set(
                    saved.get('price_type')
                    or TRANSLATIONS[controller.language][
                        'price_type_values'
                    ].split('|')[0]
                )
                controller.price_basis_var.set(
                    saved.get('price_basis', 'active')
                )
                controller.price_basis_display_var.set(
                    TRANSLATIONS[controller.language][
                        'price_sold'
                        if controller.price_basis_var.get() == 'sold'
                        else 'price_active'
                    ]
                )
                controller.load_platform_draft(
                    controller.current_platform
                )
                controller.update_listing_completeness()
            controller.render_live_preview()
        active = min(max(int(data.get('active', 0)), 0), len(tabs) - 1)
        self.notebook.select(self.notebook.tabs()[active])
        return True

    def on_close(self):
        if self.clear_session_on_exit:
            self.delete_session_file()
        else:
            self.save_session()
        for controller in self.controllers.values():
            self.release_controller(controller)
        for _container, controller in self.retired_tabs:
            self.release_controller(controller)
        self.retired_tabs = []
        self.root.destroy()

    def add_tab(self):
        self.tab_counter += 1
        tab_number = self.tab_counter
        container = tk.Frame(self.notebook, background='#f5f5f5')
        tab_id = str(container)

        def update_title(title):
            if tab_id not in self.controllers:
                return
            clean_title = re.sub(r'\s+', ' ', title).strip()
            language = (
                self.controllers[tab_id].language
                if tab_id in self.controllers else 'de'
            )
            default_label = TRANSLATIONS[language]['tab_default']
            label = (
                clean_title[:42]
                if clean_title else f"{default_label} {tab_number}"
            )
            try:
                self.notebook.tab(container, text=label)
            except tk.TclError:
                pass

        controller = ProductGeneratorGUI(
            container,
            embedded=True,
            close_callback=lambda: self.close_tab(container),
            title_callback=update_title,
            language_callback=self.update_chrome_language,
            variant_open_callback=self.open_result_in_new_tab,
        )
        self.controllers[tab_id] = controller
        default_label = TRANSLATIONS[controller.language]['tab_default']
        self.notebook.add(container, text=f"{default_label} {tab_number}")
        self.notebook.select(container)
        self.update_chrome_language(controller.language)
        return controller

    def open_result_in_new_tab(self, result, query=''):
        """Öffnet einen Treffer als unabhängige Kopie in einem neuen Tab."""
        controller = self.add_tab()
        variant = result.get('variant') or {}
        search_text = query or variant.get('name', '')
        controller.search_var.set(search_text)
        if controller._search_after_id is not None:
            controller.root.after_cancel(controller._search_after_id)
            controller._search_after_id = None
        controller._search_generation += 1
        controller.search_results = [result]
        controller.variant_listbox.delete(0, tk.END)
        controller.variant_listbox.insert(
            tk.END, controller.result_display_label(result)
        )
        controller.variant_listbox.selection_set(0)
        controller.on_variant_selected()
        return controller

    def close_current_tab(self):
        selected = self.notebook.select()
        if selected:
            controller = self.controllers.get(selected)
            if controller:
                self.close_tab(controller.root)

    def close_tab(self, container):
        tab_id = str(container)
        controller = self.controllers.pop(tab_id, None)
        if controller:
            controller._closed = True
            controller._search_generation += 1
            controller.selected_variant = None
            # Versteckt statt sofort zerstört: bereits laufende Netzwerk-Threads
            # können gefahrlos auslaufen, ohne andere Tabs zu beeinflussen.
            self.retired_tabs.append((container, controller))
            # Nach der Schonfrist werden Frame und Datenbankverbindung
            # tatsächlich freigegeben, sonst wachsen beide unbegrenzt.
            self.root.after(RETIRED_TAB_GRACE_MS, self.dispose_retired_tabs)
        try:
            self.notebook.forget(container)
        except tk.TclError:
            return
        if not self.notebook.tabs():
            self.add_tab()

    def dispose_retired_tabs(self):
        """Gibt Frames und Datenbankverbindungen stillgelegter Tabs frei."""
        pending, self.retired_tabs = self.retired_tabs, []
        for container, controller in pending:
            self.release_controller(controller)
            try:
                container.destroy()
            except tk.TclError:
                pass

    @staticmethod
    def release_controller(controller):
        """Schließt die Produktakte eines Controllers genau einmal."""
        if getattr(controller, '_store_released', False):
            return
        controller._store_released = True
        try:
            controller.listing_store.close()
        except Exception:
            pass


def main():
    root = tk.Tk()
    TabbedProductGeneratorGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
