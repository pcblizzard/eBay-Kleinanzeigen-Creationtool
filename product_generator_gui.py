#!/usr/bin/env python3
"""
eBay Kleinanzeigen Produktdatei Generator
Erstellt automatische Produktbeschreibungen mit Gewährleistungsklausel
GUI-Version mit Dateiauswahl
"""

import json
import io
import locale
import os
from pathlib import Path
from datetime import datetime
import difflib
import html as html_lib
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, filedialog, messagebox
import threading
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = None
    ImageTk = None

# Konstante für die Gewährleistungsklausel
WARRANTY_CLAUSE = """Privatverkauf. Die Ware wird unter Ausschluss der Sachmängelhaftung nach § 475 BGB verkauft. Ausgeschlossen ist jede Gewährleistung für Sachmängel. Die Haftung für arglistig verschwiegene Mängel sowie für Schäden aus der Verletzung von Leben, Körper oder Gesundheit bleibt unberührt."""

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
        "legal_frame": "Fester Hinweis (wird immer angehängt)",
        "options_frame": "4. Optionen",
        "path_label": "Speicherpfad:",
        "change_path": "📁 Speicherpfad ändern",
        "save_button": "💾 Speichern",
        "close_button": "❌ Beenden",
        "new_tab": "＋ Neuer Beitrag",
        "close_tab": "✕ Tab schließen",
        "language_label": "Sprache:",
        "default_path_button": "Standard-Speicherort wählen",
        "status_ready": "Bereit",
        "no_selection": "Bitte wählen Sie zuerst eine Produktvariante aus!",
        "saved_success": "Datei erfolgreich gespeichert:",
        "font_size_label": "Schriftgröße:",
        "menu_settings": "Einstellungen",
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
        "default_save_path_notice": "Standardpfad gespeichert",
        "config_load_error": "Fehler beim Laden der Konfiguration",
        "config_save_error": "Fehler beim Speichern der Konfiguration",
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
        "legal_frame": "Mandatory notice (always appended)",
        "options_frame": "4. Options",
        "path_label": "Save path:",
        "change_path": "📁 Change save path",
        "save_button": "💾 Save",
        "close_button": "❌ Close",
        "new_tab": "＋ New listing",
        "close_tab": "✕ Close tab",
        "language_label": "Language:",
        "default_path_button": "Select default save location",
        "status_ready": "Ready",
        "no_selection": "Please select a product variant first!",
        "saved_success": "File saved successfully:",
        "font_size_label": "Font size:",
        "menu_settings": "Options",
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
        "default_save_path_notice": "Default path saved",
        "config_load_error": "Error loading configuration",
        "config_save_error": "Error saving configuration",
    }
}


class ProductGenerator:
    """Backend für Produktverwaltung"""
    
    def __init__(self, products_file="products.json", output_dir="product_listings"):
        self.products_file = products_file
        self.output_dir = output_dir
        self.products = []
        
        # Output-Verzeichnis erstellen
        Path(self.output_dir).mkdir(exist_ok=True)
        
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
    
    def generate_listing(self, product_variant, language="de", description_override=None):
        """Generiert die komplette Produktliste"""
        description = product_variant['description']
        if description_override is not None:
            description = description_override.strip()
        elif isinstance(description, dict):
            description = description.get(language, description.get('de', ''))

        source_url = product_variant.get("source_url", "")
        source_label = "Quelle" if language == "de" else "Source"
        source_line = f"\n\n{source_label}: {source_url}" if source_url else ""
        return (
            f"{description.rstrip()}{source_line}\n\n---\n\n"
            f"{WARRANTY_CLAUSE}\n"
        )

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
                    "The disc and case are in **[like new / very good / good / "
                    "used]** condition.\n\n"
                    "Scratches are **[not present / slight / visible]**. "
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
                    "The item is in **[very good / good / used]** condition and "
                    "**[works perfectly / has the following defects: ...]**.\n\n"
                    "Normal signs of use are **[present / not present]**.\n\n"
                    "Feel free to contact me with any questions."
                )
            contents_heading = "### Included"
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
                    "Datenträger und Hülle befinden sich in **[neuwertigem / "
                    "sehr gutem / gutem / gebrauchtem]** Zustand.\n\n"
                    "Kratzer sind **[nicht vorhanden / leicht vorhanden / "
                    "sichtbar vorhanden]**. Die Wiedergabe wurde "
                    "**[erfolgreich getestet / nicht getestet]**.\n\n"
                    "Bei Fragen einfach melden."
                )
            else:
                footer = (
                    "Der Artikel befindet sich in **[sehr gutem / gutem / "
                    "gebrauchtem]** Zustand und **[funktioniert einwandfrei / hat "
                    "folgende Einschränkungen: ...]**.\n\n"
                    "Normale Gebrauchsspuren sind **[vorhanden / nicht vorhanden]**.\n\n"
                    "Bei Fragen einfach melden."
                )
            contents_heading = "### Lieferumfang"

        facts = '\n'.join(f"* {line}" for line in raw_lines)
        if not facts:
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
            "*(Nicht Zutreffendes bitte entfernen oder ergänzen.)*\n\n"
            f"{footer}"
        )
    
    def save_listing(self, listing, product_name):
        """Speichert die Liste als Textdatei"""
        filename = product_name.replace('/', '_').replace('\\', '_').replace(':', '')
        filepath = Path(self.output_dir) / f"{filename}.txt"
        
        counter = 1
        original_path = filepath
        while filepath.exists():
            name_parts = original_path.stem.rsplit('_', 1)
            if name_parts[-1].isdigit():
                base_name = name_parts[0]
            else:
                base_name = original_path.stem
            filepath = Path(self.output_dir) / f"{base_name}_{counter}.txt"
            counter += 1
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(listing)
        
        return filepath


class ProductGeneratorGUI:
    """GUI für den Produktgenerator"""
    
    def __init__(
        self, root, embedded=False, close_callback=None, title_callback=None
    ):
        self.root = root
        self.embedded = embedded
        self.close_callback = close_callback
        self.title_callback = title_callback
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
            {'wikipedia': True, 'amazon': True, 'geizhals': True, 'idealo': True},
        )
        project_output = str(Path(__file__).resolve().parent / "product_listings")
        self.save_path = config.get('save_path', project_output)
        if not os.path.exists(self.save_path):
            Path(project_output).mkdir(parents=True, exist_ok=True)
            self.save_path = project_output
        
        self.selected_variant = None
        self.search_results = []
        self._search_after_id = None
        self._search_generation = 0
        
        self.style = ttk.Style()
        theme = 'vista' if 'vista' in self.style.theme_names() else 'clam'
        self.style.theme_use(theme)
        self.root.configure(background='#f5f5f5')
        self.set_font_size(self.font_size)
        
        if not embedded:
            self.create_menu()
        self.setup_ui()

    def detect_system_language(self):
        language, _ = locale.getdefaultlocale()
        if language and language.startswith('en'):
            return 'en'
        return 'de'

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
        self.menubar = tk.Menu(self.root)
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
                'providers': {
                    name: bool(variable.get())
                    for name, variable in getattr(self, 'provider_vars', {}).items()
                } or self.provider_settings,
            }
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
        except Exception:
            print(TRANSLATIONS['de']['config_save_error'])
    
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

        self.product_image_label = ttk.Label(
            self.rendered_frame,
            text=trans['no_product_image'],
            anchor=tk.CENTER,
        )
        self.product_image_label.pack(fill=tk.X, pady=(0, 6))
        self._product_photo = None
        self._image_generation = 0

        rendered_scrollbar = ttk.Scrollbar(self.rendered_frame)
        rendered_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.rendered_preview = tk.Text(
            self.rendered_frame,
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
        self.legal_text.insert("1.0", WARRANTY_CLAUSE)
        self.legal_text.config(state=tk.DISABLED)
        
        # ===== Frame 4: Aktionen =====
        self.options_frame = ttk.LabelFrame(self.root, text=trans['options_frame'], padding=10)
        self.options_frame.pack(fill=tk.BOTH, expand=False, padx=10, pady=10)
        
        path_info_frame = ttk.Frame(self.options_frame)
        path_info_frame.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(path_info_frame, text=trans['path_label'], font=("Segoe UI", 9, "bold")).pack(anchor=tk.W)
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
        ):
            variable = tk.BooleanVar(
                value=self.provider_settings.get(name, True)
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
                self.variant_listbox.insert(tk.END, result['variant']['name'])
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
                and 'suggestqueries.google.com/' not in source_url
            ):
                self.load_product_image_async(
                    self.selected_variant, source_url
                )
            if (
                'amazon.de/' in source_url
                and str(description).startswith('Amazon-Suchergebnis:')
            ):
                self.load_amazon_details_async(self.selected_variant)
            elif (
                ('geizhals.de/' in source_url or 'idealo.de/' in source_url)
                and str(description).startswith('Online gefunden:')
            ):
                self.load_comparison_details_async(self.selected_variant)
            elif (
                'suggestqueries.google.com/' in source_url
                and str(description).startswith('Web-Suchvorschlag')
            ):
                self.load_suggestion_details_async(self.selected_variant)
            
            trans = TRANSLATIONS[self.language]
            self.status_var.set(f"{trans['selected_variant']} {self.selected_variant['name']}")

    def select_result_index(self, index):
        """Wählt einen Treffer und erzeugt sofort dessen Live-Entwurf."""
        if not (0 <= index < len(self.search_results)):
            return
        self.variant_listbox.selection_clear(0, tk.END)
        self.variant_listbox.selection_set(index)
        self.variant_listbox.activate(index)
        self.variant_listbox.see(index)
        self.on_variant_selected()

    def on_draft_modified(self, *args):
        if not self.preview_text.edit_modified():
            return
        self.preview_text.edit_modified(False)
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
            tk.END, WARRANTY_CLAUSE, 'legal'
        )
        self.rendered_preview.config(state=tk.DISABLED)

    def reset_product_image(self):
        if not hasattr(self, 'product_image_label'):
            return
        self._image_generation += 1
        self._product_photo = None
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
                html = self.fetch_url(source_url)
                image_url = self.extract_product_image_url(html, source_url)
                if not image_url:
                    return
                image_data = self.fetch_binary(image_url)
                image = Image.open(io.BytesIO(image_data))
                image.thumbnail((240, 240), Image.Resampling.LANCZOS)
                if image.mode not in ('RGB', 'RGBA'):
                    image = image.convert('RGBA')
            except Exception:
                return
            self.root.after(
                0,
                lambda: self.apply_product_image(
                    variant, generation, image
                ),
            )

        threading.Thread(target=worker, daemon=True).start()

    def apply_product_image(self, variant, generation, image):
        if (
            self._closed
            or self.selected_variant is not variant
            or generation != self._image_generation
        ):
            return
        photo = ImageTk.PhotoImage(image)
        self._product_photo = photo
        self.product_image_label.config(image=photo, text='')

    @staticmethod
    def extract_product_image_url(html, page_url):
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
                return urllib.parse.urljoin(
                    page_url, html_lib.unescape(match.group(1))
                )
        return ''

    def fetch_binary(self, url):
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
            return response.read()
    
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
            self.language = value
            self.save_config()
            self.update_ui_language()

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

    def update_ui_language(self):
        trans = TRANSLATIONS[self.language]
        if not self.embedded:
            self.root.title(trans['title'])
        self.search_frame.config(text=trans['search_frame'])
        self.search_label_widget.config(text=trans['search_label'])
        self.variant_frame.config(text=trans['variant_frame'])
        self.preview_frame.config(text=trans['preview_frame'])
        self.editor_frame.config(text=trans['editor_label'])
        self.rendered_frame.config(text=trans['live_preview_label'])
        self.legal_frame.config(text=trans['legal_frame'])
        self.provider_frame.config(text=trans['provider_frame'])
        for button, label_key in self.provider_buttons.values():
            button.config(text=trans[label_key])
        self.options_frame.config(text=trans['options_frame'])
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
        descriptions = []
        errors = []
        providers = []
        if self.normalize_isbn(search_term):
            providers.append(
                ('Deutsche Nationalbibliothek', self.search_dnb_isbn)
            )
            providers.append(('ZVAB ISBN', self.search_zvab_isbn))
        if enabled.get('web_suggestions'):
            providers.append(('Web', self.search_web_suggestions))
        if enabled.get('wikipedia'):
            providers.append(('Wikipedia', self.search_wikipedia))
        if enabled.get('amazon'):
            providers.append(('Amazon', self.search_amazon))
        if enabled.get('geizhals'):
            providers.append(('Geizhals', self.search_geizhals))
        if enabled.get('idealo'):
            providers.append(('Idealo', self.search_idealo))
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
                key=lambda item: 'suggestqueries.google.com' in item[2]
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
                        'd-nb.info/' in item[2]
                        or '/ean/' in item[2]
                        or 'zvab.com/products/isbn/' in item[2]
                    ),
                    normalized_query in item[0].lower(),
                    sum(word in item[0].lower() for word in query_words),
                    difflib.SequenceMatcher(
                        None, normalized_query, item[0].lower()
                    ).ratio(),
                ),
                reverse=True,
            )
        self.root.after(
            0,
            lambda: self.apply_online_results(
                search_term, request_id, unique_results, errors
            ),
        )

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
        online_results = [
            {
                'group_id': 'online',
                'variant': {
                    'name': title,
                    'description': {'de': desc, 'en': desc},
                    'source_url': source_url,
                },
            }
            for title, desc, source_url in results
        ]
        existing_names = {
            re.sub(r'\W+', ' ', result['variant']['name'].lower()).strip()
            for result in self.search_results
        }
        self.search_results.extend(
            result for result in online_results
            if re.sub(
                r'\W+', ' ', result['variant']['name'].lower()
            ).strip() not in existing_names
        )
        self.populate_online_results()

    def load_amazon_details_async(self, variant):
        """Lädt die langsamere Amazon-Produktseite erst nach der Auswahl."""
        source_url = variant.get('source_url', '')

        def worker():
            try:
                title, description = self.extract_amazon_product(
                    self.fetch_url(source_url)
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
        self.status_var.set(
            f"{TRANSLATIONS[self.language]['selected_variant']} {variant['name']}"
        )

    def load_comparison_details_async(self, variant):
        """Lädt Geizhals-/Idealo-Details erst nach Auswahl eines Treffers."""
        source_url = variant.get('source_url', '')
        provider = 'geizhals' if 'geizhals.' in source_url else 'idealo'

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
                title, description, source_url = candidate
                normalized_title = re.sub(
                    r'\W+', ' ', title.lower()
                ).strip()
                exact_bonus = 100 if normalized_title == normalized_requested else 0
                source_bonus = (
                    30 if 'geizhals.' in source_url
                    else 25 if 'idealo.' in source_url
                    else 20 if 'amazon.' in source_url
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
            self.variant_listbox.insert(tk.END, result['variant']['name'])
        self.status_var.set(f"{len(self.search_results)} {trans['online_results']}")
        selected_index = next(
            (
                index for index, result in enumerate(self.search_results)
                if result['variant'] is selected_variant
            ),
            0,
        )
        self.select_result_index(selected_index)

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

    def _search_amazon_once(self, search_term):
        """Sucht Amazon.de und extrahiert Fakten aus Produktseiten."""
        asin_match = re.search(
            r'(?:/dp/|/gp/product/)?([A-Z0-9]{10})(?:[/?]|$)',
            search_term.upper(),
        )
        if asin_match and (
            'AMAZON.' in search_term.upper()
            or re.fullmatch(r'[A-Z0-9]{10}', search_term.strip().upper())
        ):
            asin = asin_match.group(1)
            source_url = f"https://www.amazon.de/dp/{asin}"
            title, description = self.extract_amazon_product(self.fetch_url(source_url))
            return [(title, description, source_url)] if title else []

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
        value = re.sub(r'<[^>]+>', ' ', value)
        value = html_lib.unescape(value)
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
            encoding = response.headers.get_content_charset() or 'utf-8'
            return response.read().decode(encoding, errors='replace')

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
                href = match[0]
                title = match[1] if len(match) > 1 else ''
                title_clean = re.sub(r'<[^>]+>', '', title).strip()
                title_clean = re.sub(r'\s+', ' ', title_clean)
                if not title_clean or not href:
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

    def fetch_online_description(self, url):
        try:
            html = self.fetch_url(url)
        except Exception:
            return ''

        patterns = [
            r'<div[^>]*class=["\']?[^"\'>]*(?:product-description|description|product-specs|productDetails|product-detail|description-box)[^"\'>]*["\']?[^>]*>(.*?)</div>',
            r'<section[^>]*class=["\']?[^"\'>]*(?:description|product-description)[^"\'>]*["\']?[^>]*>(.*?)</section>',
            r'<p[^>]*>(.*?)</p>'
        ]
        for pattern in patterns:
            match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
            if match:
                text = re.sub(r'<[^>]+>', '', match.group(1))
                text = html_lib.unescape(text)
                text = re.sub(r'\s+', ' ', text).strip()
                if len(text) > 40:
                    return text
        return ''
    
    def save_file(self):
        """Speichert die Produktbeschreibung als Textdatei"""
        trans = TRANSLATIONS[self.language]
        if not self.selected_variant:
            messagebox.showwarning(
                trans['no_selection'],
                trans['no_selection']
            )
            return
        
        # Listing generieren
        edited_description = self.preview_text.get("1.0", tk.END).strip()
        listing = self.generator.generate_listing(
            self.selected_variant, self.language, edited_description
        )
        
        # Speichern im separaten Thread um GUI nicht zu blockieren
        def save_async():
            try:
                # Dateiname sanitieren
                filename = self.selected_variant['name'].replace('/', '_').replace('\\', '_').replace(':', '')
                filepath = Path(self.save_path) / f"{filename}.txt"
                
                # Falls Datei existiert, mit Nummer versehen
                counter = 1
                original_path = filepath
                while filepath.exists():
                    name_parts = original_path.stem.rsplit('_', 1)
                    if name_parts[-1].isdigit():
                        base_name = name_parts[0]
                    else:
                        base_name = original_path.stem
                    filepath = Path(self.save_path) / f"{base_name}_{counter}.txt"
                    counter += 1
                
                # Datei schreiben
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(listing)
                
                # Status updaten
                self.root.after(0, lambda: self.status_var.set(f"{trans['saved_success']} {filepath.name}"))
                self.root.after(0, lambda: messagebox.showinfo(
                    trans['saved_success'],
                    f"{trans['saved_success']}\n\n{filepath}"
                ))
                
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror(
                    trans['save_error'],
                    f"{trans['save_error']}\n\n{str(e)}"
                ))
                self.root.after(0, lambda: self.status_var.set(f"{trans['save_error']}"))
        
        thread = threading.Thread(target=save_async, daemon=True)
        thread.start()


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

        toolbar = ttk.Frame(root, padding=(8, 6))
        toolbar.pack(fill=tk.X)
        ttk.Button(
            toolbar,
            text=TRANSLATIONS['de']['new_tab'],
            command=self.add_tab,
        ).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(
            toolbar,
            text=TRANSLATIONS['de']['close_tab'],
            command=self.close_current_tab,
        ).pack(side=tk.LEFT)

        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 6))

        menubar = tk.Menu(root)
        settings_menu = tk.Menu(menubar, tearoff=0)
        settings_menu.add_command(
            label=TRANSLATIONS['de']['menu_change_save_path'],
            command=lambda: self.run_on_active('change_save_path'),
        )
        settings_menu.add_command(
            label=TRANSLATIONS['de']['menu_default_save_path'],
            command=lambda: self.run_on_active('set_default_save_path'),
        )
        menubar.add_cascade(
            label=TRANSLATIONS['de']['menu_settings'],
            menu=settings_menu,
        )
        root.config(menu=menubar)
        self.add_tab()

    def run_on_active(self, method_name):
        controller = self.active_controller()
        if controller:
            getattr(controller, method_name)()

    def active_controller(self):
        selected = self.notebook.select()
        return self.controllers.get(selected)

    def add_tab(self):
        self.tab_counter += 1
        tab_number = self.tab_counter
        container = tk.Frame(self.notebook, background='#f5f5f5')
        tab_id = str(container)

        def update_title(title):
            if tab_id not in self.controllers:
                return
            clean_title = re.sub(r'\s+', ' ', title).strip()
            label = clean_title[:42] if clean_title else f"Beitrag {tab_number}"
            try:
                self.notebook.tab(container, text=label)
            except tk.TclError:
                pass

        controller = ProductGeneratorGUI(
            container,
            embedded=True,
            close_callback=lambda: self.close_tab(container),
            title_callback=update_title,
        )
        self.controllers[tab_id] = controller
        self.notebook.add(container, text=f"Beitrag {tab_number}")
        self.notebook.select(container)
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
        try:
            self.notebook.forget(container)
        except tk.TclError:
            return
        if not self.notebook.tabs():
            self.add_tab()


def main():
    root = tk.Tk()
    app = TabbedProductGeneratorGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
