#!/usr/bin/env python3
"""
eBay Kleinanzeigen Produktdatei Generator
Erstellt automatische Produktbeschreibungen mit Gewährleistungsklausel
GUI-Version mit Dateiauswahl
"""

import json
import os
from pathlib import Path
from datetime import datetime
import difflib
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading

# Konstante für die Gewährleistungsklausel
WARRANTY_CLAUSE = """
Privatverkauf. Die Ware wird unter Ausschluss der Sachmängelhaftung nach § 475 BGB verkauft. Ausgeschlossen ist jede Gewährleistung für Sachmängel. Die Haftung für arglistig verschwiegene Mängel sowie für Schäden aus der Verletzung von Leben, Körper oder Gesundheit bleibt unberührt.""".strip()

class ProductGenerator:
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
            print(f"✓ {len(self.products)} Produktgruppen geladen")
        except FileNotFoundError:
            print(f"✗ Datei '{self.products_file}' nicht gefunden!")
            self.products = []
        except json.JSONDecodeError:
            print(f"✗ JSON-Fehler in '{self.products_file}'")
            self.products = []
    
    def search_products(self, search_term):
        """Sucht Produkte nach Name"""
        results = []
        search_term_lower = search_term.lower()
        
        for product_group in self.products:
            for variant in product_group.get('variants', []):
                variant_name = variant['name'].lower()
                # Exakte oder partielle Übereinstimmung
                if search_term_lower in variant_name:
                    results.append({
                        'group_id': product_group['id'],
                        'variant': variant
                    })
        
        # Sortierung nach Ähnlichkeit (beste Matches zuerst)
        if results:
            results.sort(
                key=lambda x: difflib.SequenceMatcher(
                    None, 
                    search_term_lower, 
                    x['variant']['name'].lower()
                ).ratio(),
                reverse=True
            )
        
        return results
    
    def select_variant(self, search_results):
        """Zeigt Auswahl für Benutzer"""
        if not search_results:
            return None
        
        if len(search_results) == 1:
            print(f"\n✓ Gefunden: {search_results[0]['variant']['name']}")
            return search_results[0]['variant']
        
        print(f"\nEs wurden {len(search_results)} Varianten gefunden:")
        for i, result in enumerate(search_results, 1):
            print(f"  {i}. {result['variant']['name']}")
        
        while True:
            try:
                choice = input("\nWelches Modell möchten Sie wählen? (Nummer): ").strip()
                choice_idx = int(choice) - 1
                if 0 <= choice_idx < len(search_results):
                    return search_results[choice_idx]['variant']
                print("✗ Ungültige Auswahl. Bitte erneut versuchen.")
            except ValueError:
                print("✗ Bitte geben Sie eine Nummer ein.")
    
    def generate_listing(self, product_variant, language="de"):
        """Generiert die komplette Produktliste"""
        product_name = product_variant['name']
        description = product_variant['description']
        
        listing = f"""PRODUKTBESCHREIBUNG
{'='*60}

Artikel: {product_name}

Beschreibung:
{'-'*60}
{description}

{'='*60}

VERKAUFSBEDINGUNGEN:

{WARRANTY_CLAUSE}

{'='*60}
Erstellt: {datetime.now().strftime('%d.%m.%Y um %H:%M Uhr')}
"""
        return listing
    
    def save_listing(self, listing, product_name):
        """Speichert die Liste als Textdatei"""
        # Sanitize filename
        filename = product_name.replace('/', '_').replace('\\', '_').replace(':', '')
        filepath = Path(self.output_dir) / f"{filename}.txt"
        
        # Falls Datei existiert, mit Nummer versehen
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
    
    def run(self):
        """Hauptprogramm"""
        print("\n" + "="*60)
        print("eBay Kleinanzeigen - Produktdatei Generator")
        print("="*60)
        
        while True:
            print("\nOptionen:")
            print("1. Neue Produktbeschreibung erstellen")
            print("2. Verfügbare Produkte anzeigen")
            print("3. Beenden")
            
            choice = input("\nWählen Sie eine Option (1-3): ").strip()
            
            if choice == "1":
                self.create_new_listing()
            elif choice == "2":
                self.show_all_products()
            elif choice == "3":
                print("\n✓ Auf Wiedersehen!")
                break
            else:
                print("✗ Ungültige Option.")
    
    def create_new_listing(self):
        """Erstellt eine neue Produktliste"""
        search_term = input("\nProdukt eingeben (z.B. 'Samsung Galaxy S26'): ").strip()
        
        if not search_term:
            print("✗ Bitte Produktnamen eingeben.")
            return
        
        search_results = self.search_products(search_term)
        
        if not search_results:
            print(f"✗ Keine Produkte gefunden für: '{search_term}'")
            print("\nVerfügbare Suchbegriffe:")
            for group in self.products:
                for variant in group['variants']:
                    print(f"  - {variant['name']}")
            return
        
        variant = self.select_variant(search_results)
        if not variant:
            return
        
        listing = self.generate_listing(variant)
        filepath = self.save_listing(listing, variant['name'])
        
        print(f"\n✓ Datei erstellt: {filepath}")
        print(f"\nVorschau:")
        print("-"*60)
        print(listing)
        print("-"*60)
    
    def show_all_products(self):
        """Zeigt alle verfügbaren Produkte"""
        print("\nVerfügbare Produkte:")
        print("-"*60)
        for group in self.products:
            for variant in group['variants']:
                print(f"  • {variant['name']}")


def main():
    generator = ProductGenerator()
    generator.run()


if __name__ == "__main__":
    main()
