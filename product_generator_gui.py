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
import sys

# Konstante für die Gewährleistungsklausel
WARRANTY_CLAUSE = """Privatverkauf. Die Ware wird unter Ausschluss der Sachmängelhaftung nach § 475 BGB verkauft. Ausgeschlossen ist jede Gewährleistung für Sachmängel. Die Haftung für arglistig verschwiegene Mängel sowie für Schäden aus der Verletzung von Leben, Körper oder Gesundheit bleibt unberührt."""


class ProductGenerator:
    """Backend für Produktverwaltung"""
    
    def __init__(self, products_file="products.json"):
        self.products_file = products_file
        self.products = []
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
        """Sucht Produkte nach Name"""
        results = []
        search_term_lower = search_term.lower()
        
        for product_group in self.products:
            for variant in product_group.get('variants', []):
                variant_name = variant['name'].lower()
                if search_term_lower in variant_name:
                    results.append({
                        'group_id': product_group['id'],
                        'variant': variant
                    })
        
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
    
    def generate_listing(self, product_variant):
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


class ProductGeneratorGUI:
    """GUI für den Produktgenerator"""
    
    def __init__(self, root):
        self.root = root
        self.root.title("eBay Kleinanzeigen - Produktbeschreibungs-Generator")
        self.root.geometry("900x750")
        self.root.resizable(True, True)
        
        # Backend
        self.generator = ProductGenerator()
        
        # Standard-Speicherpfad (Dokumente)
        self.save_path = str(Path.home() / "Dokumente")
        if not os.path.exists(self.save_path):
            self.save_path = str(Path.home())
        
        # Current variant
        self.selected_variant = None
        self.search_results = []
        
        # Style
        self.style = ttk.Style()
        self.style.theme_use('clam')
        
        self.setup_ui()
    
    def setup_ui(self):
        """Erstellt die Benutzeroberfläche"""
        
        # ===== Frame 1: Produktsuche =====
        search_frame = ttk.LabelFrame(self.root, text="1. Produktsuche", padding=10)
        search_frame.pack(fill=tk.X, padx=10, pady=10)
        
        ttk.Label(search_frame, text="Produktname eingeben:").pack(anchor=tk.W)
        self.search_var = tk.StringVar()
        self.search_var.trace_add('write', self.on_search_changed)
        
        search_entry = ttk.Entry(search_frame, textvariable=self.search_var, width=60)
        search_entry.pack(fill=tk.X, pady=5)
        search_entry.focus()
        
        # ===== Frame 2: Variantenauswahl =====
        variant_frame = ttk.LabelFrame(self.root, text="2. Variante auswählen", padding=10)
        variant_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Listbox für Varianten
        scrollbar = ttk.Scrollbar(variant_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.variant_listbox = tk.Listbox(
            variant_frame,
            yscrollcommand=scrollbar.set,
            font=("Arial", 10),
            height=8
        )
        self.variant_listbox.pack(fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.variant_listbox.yview)
        
        self.variant_listbox.bind('<<ListboxSelect>>', self.on_variant_selected)
        
        # ===== Frame 3: Vorschau =====
        preview_frame = ttk.LabelFrame(self.root, text="3. Vorschau", padding=10)
        preview_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Text-Widget mit Scrollbar
        scrollbar_text = ttk.Scrollbar(preview_frame)
        scrollbar_text.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.preview_text = tk.Text(
            preview_frame,
            yscrollcommand=scrollbar_text.set,
            font=("Courier", 9),
            height=10,
            width=100
        )
        self.preview_text.pack(fill=tk.BOTH, expand=True)
        self.preview_text.config(state=tk.DISABLED)
        scrollbar_text.config(command=self.preview_text.yview)
        
        # ===== Frame 4: Aktionen =====
        action_frame = ttk.Frame(self.root, padding=10)
        action_frame.pack(fill=tk.X, padx=10, pady=10)
        
        # Speicherpfad-Anzeige
        path_info_frame = ttk.Frame(action_frame)
        path_info_frame.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(path_info_frame, text="Speicherpfad:", font=("Arial", 9, "bold")).pack(anchor=tk.W)
        self.path_label = ttk.Label(
            path_info_frame, 
            text=self.save_path,
            foreground="blue",
            font=("Arial", 9)
        )
        self.path_label.pack(anchor=tk.W, fill=tk.X)
        
        # Buttons
        button_frame = ttk.Frame(action_frame)
        button_frame.pack(fill=tk.X)
        
        ttk.Button(
            button_frame,
            text="📁 Speicherpfad ändern",
            command=self.change_save_path
        ).pack(side=tk.LEFT, padx=5)
        
        ttk.Button(
            button_frame,
            text="💾 Speichern",
            command=self.save_file
        ).pack(side=tk.LEFT, padx=5)
        
        ttk.Button(
            button_frame,
            text="❌ Beenden",
            command=self.root.quit
        ).pack(side=tk.RIGHT, padx=5)
        
        # Status-Bar
        self.status_var = tk.StringVar(value="Bereit")
        status_bar = ttk.Label(
            self.root,
            textvariable=self.status_var,
            relief=tk.SUNKEN,
            anchor=tk.W
        )
        status_bar.pack(fill=tk.X, side=tk.BOTTOM, padx=5, pady=5)
    
    def on_search_changed(self, *args):
        """Wird aufgerufen, wenn der Suchtext sich ändert"""
        search_term = self.search_var.get().strip()
        
        self.variant_listbox.delete(0, tk.END)
        self.selected_variant = None
        self.preview_text.config(state=tk.NORMAL)
        self.preview_text.delete(1.0, tk.END)
        self.preview_text.config(state=tk.DISABLED)
        
        if not search_term:
            self.status_var.set("Bitte Produktnamen eingeben")
            self.search_results = []
            return
        
        self.search_results = self.generator.search_products(search_term)
        
        if not self.search_results:
            self.status_var.set(f"Keine Produkte gefunden für: '{search_term}'")
            return
        
        # Varianten in Listbox einfügen
        for i, result in enumerate(self.search_results):
            self.variant_listbox.insert(tk.END, result['variant']['name'])
        
        self.status_var.set(f"{len(self.search_results)} Variante(n) gefunden")
    
    def on_variant_selected(self, *args):
        """Wird aufgerufen, wenn eine Variante ausgewählt wird"""
        selection = self.variant_listbox.curselection()
        
        if not selection:
            return
        
        idx = selection[0]
        if 0 <= idx < len(self.search_results):
            self.selected_variant = self.search_results[idx]['variant']
            
            # Vorschau generieren
            listing = self.generator.generate_listing(self.selected_variant)
            
            self.preview_text.config(state=tk.NORMAL)
            self.preview_text.delete(1.0, tk.END)
            self.preview_text.insert(1.0, listing)
            self.preview_text.config(state=tk.DISABLED)
            
            self.status_var.set(f"✓ Variante ausgewählt: {self.selected_variant['name']}")
    
    def change_save_path(self):
        """Öffnet Dialog zur Pfadauswahl"""
        folder = filedialog.askdirectory(
            title="Speicherort auswählen",
            initialdir=self.save_path
        )
        
        if folder:
            self.save_path = folder
            self.path_label.config(text=self.save_path)
            self.status_var.set(f"Speicherpfad geändert: {self.save_path}")
    
    def save_file(self):
        """Speichert die Produktbeschreibung als Textdatei"""
        if not self.selected_variant:
            messagebox.showwarning(
                "Keine Auswahl",
                "Bitte wählen Sie zuerst eine Produktvariante aus!"
            )
            return
        
        # Listing generieren
        listing = self.generator.generate_listing(self.selected_variant)
        
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
                self.root.after(0, lambda: self.status_var.set(f"✓ Datei gespeichert: {filepath.name}"))
                self.root.after(0, lambda: messagebox.showinfo(
                    "Erfolg",
                    f"Datei erfolgreich gespeichert:\n\n{filepath}"
                ))
                
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror(
                    "Fehler",
                    f"Fehler beim Speichern:\n\n{str(e)}"
                ))
                self.root.after(0, lambda: self.status_var.set(f"✗ Fehler beim Speichern"))
        
        thread = threading.Thread(target=save_async, daemon=True)
        thread.start()


def main():
    root = tk.Tk()
    app = ProductGeneratorGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
