# eBay Kleinanzeigen - Produktbeschreibungs-Generator

Ein Python-Tool zur automatischen Erstellung von Produktbeschreibungen mit rechtssicherer Gewährleistungsklausel.

## Features

✅ **GUI-Interface**: Benutzerfreundliche graphische Oberfläche  
✅ **Produktsuche**: Intelligente Suche mit Live-Ergebnissen  
✅ **Variantenauswahl**: Übersichtliche Auswahl bei mehreren Modellen  
✅ **Live-Vorschau**: Sehen Sie die Beschreibung in Echtzeit  
✅ **Gewährleistungsklausel**: Automatisch eingefügt & nicht editierbar  
✅ **Speicherort wählbar**: Dialog zur Pfadauswahl (Standard: Dokumente)  
✅ **Textdatei-Export**: Mit Produktnamen als Dateinamen  
✅ **Zeitstempel**: Automatisch hinzugefügt  

## Installation

**Anforderung:** Python 3.10+

Keine zusätzlichen Pakete nötig! (tkinter ist in Python enthalten)

## Verwendung - GUI Version (EMPFOHLEN)

### Terminal öffnen:
```powershell
cd "I:\_BACKUP\MICHAEL\Dokumente\GitHub\eBay_Kleinanzeigen"
python product_generator_gui.py
```

### Workflow:

1. **Produktname eingeben** → Echtzeit-Suche lädt Varianten
2. **Variante auswählen** → Live-Vorschau erscheint
3. **Speicherpfad wählen** (optional) → Dialog "📁 Speicherpfad ändern"
4. **Speichern** → Button "💾 Speichern"

→ **Textdatei wird erstellt im gewählten Ordner!**

---

## Verwendung - CLI Version (Alternative)

```powershell
python product_generator.py
```

Interaktives Kommandozeilen-Menü.

---

## Produktdatenbank erweitern

Datei `products.json` bearbeiten:

```json
{
  "products": [
    {
      "id": "product_id",
      "variants": [
        {
          "name": "Produktname",
          "description": "Beschreibung mit Specs"
        }
      ]
    }
  ]
}
```

---

## Beispiel-Workflow

```
1. Tippen Sie "Samsung" oder "iPhone" oder "Galaxy S26"
   → Live-Suche zeigt Treffer

2. Klicken Sie auf "Samsung Galaxy S26"
   → Vorschau wird angezeigt

3. Optional: "📁 Speicherpfad ändern" um einen anderen Ordner zu wählen
   → Standard: C:\Users\[YourName]\Dokumente

4. "💾 Speichern" klicken
   → Datei "Samsung Galaxy S26.txt" wird erstellt
```

---

## Geplante Features

- 🔄 Automatisches Scraping von Geizhals/Hersteller-Webseiten
- 🌐 Sprachen-Auswahl (Deutsch/Englisch)
- 🔗 eBay/Kleinanzeigen-API Integration
- 📝 Custom Template-System
- 💾 CSV-Import für Massendaten
- 📊 Verkaufsstatistiken

---

## Dateien

- `product_generator_gui.py` - **GUI Version (EMPFOHLEN)**
- `product_generator.py` - CLI Version
- `products.json` - Produktdatenbank
- `product_listings/` - Generierte Textdateien (oder Speicherpfad nach Wahl)

---

## Lizenz

Für private Nutzung
