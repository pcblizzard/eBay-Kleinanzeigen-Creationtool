# eBay Kleinanzeigen - Produktbeschreibungs-Generator

Ein Python-Tool zur Erstellung prüfbarer Produktbeschreibungen mit fest angehängtem Privatverkaufs-Hinweis.

## Features

✅ **GUI-Interface**: Benutzerfreundliche graphische Oberfläche  
✅ **Produktsuche**: Intelligente Suche mit Live-Ergebnissen  
✅ **Variantenauswahl**: Übersichtliche Auswahl bei mehreren Modellen  
✅ **Prüfbare Beschreibung**: Produkttext vor dem Speichern bearbeiten  
✅ **Privatverkaufs-Hinweis**: Automatisch eingefügt & in der Ausgabe nicht entfernbar  
✅ **Speicherort wählbar**: Standardmäßig im Projektordner `product_listings`  
✅ **Quellennachweis**: Online gefundene Daten enthalten die Produkt-URL  
✅ **Textdatei-Export**: Mit Produktnamen als Dateinamen  
✅ **Zeitstempel**: Automatisch hinzugefügt  
✅ **Mehrere Beiträge**: Unabhängige Suchen und Entwürfe in separaten Tabs  
✅ **EAN/GTIN-Suche**: Barcodes werden direkt an die Produktsuchen übergeben  
✅ **ISBN-10/ISBN-13**: Deutsche Buchdaten über die Deutsche Nationalbibliothek  
✅ **Geteilte Bearbeitung**: Editor und formatierte Live-Vorschau nebeneinander  
✅ **Produktcover**: Bild der ausgewählten Produktseite wird automatisch geladen  

## Installation

**Anforderung:** Python 3.10+, tkinter und Pillow. Installation:

```powershell
python -m pip install .
```

## Verwendung - GUI Version (EMPFOHLEN)

### Terminal öffnen:
```powershell
cd "I:\_BACKUP\MICHAEL\Dokumente\GitHub\eBay_Kleinanzeigen"
python product_generator_gui.py
```

### Workflow:

1. **Produktname eingeben** → Echtzeit-Suche lädt Varianten
2. **Variante auswählen** → Live-Vorschau erscheint
3. **Beschreibung prüfen und bei Bedarf bearbeiten**
4. **Speicherpfad wählen** (optional) → Dialog "📁 Speicherpfad ändern"
5. **Speichern** → Button "💾 Speichern"

→ **Textdatei wird erstellt im gewählten Ordner!**

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
→ Standard: `product_listings` im Projektordner

4. "💾 Speichern" klicken
   → Datei "Samsung Galaxy S26.txt" wird erstellt
```

---

## Online-Quellen

Das Tool fragt bei jeder Eingabe zusätzlich die in der Oberfläche aktivierten
Online-Quellen ab und ergänzt damit lokale Treffer. Die globale Web-Vorschlagssuche
liefert ohne API-Schlüssel breit gefächerte Modellkandidaten, auch für reine
Markensuchen wie „Fantec“. Die Wikipedia-Livesuche nutzt die öffentliche
MediaWiki-API und liefert Modellkandidaten mit deutsch- oder englischsprachigen
Kurzbeschreibungen. Sie eignet sich besonders für bekannte Produktreihen, ersetzt
aber keine technischen Herstellerdaten.

ISBN-10 und ISBN-13 werden auch mit Bindestrichen erkannt, validiert und
gegenseitig umgerechnet. Für deutsche Bücher fragt das Tool die öffentliche
SRU-Schnittstelle der Deutschen Nationalbibliothek ab und übernimmt Titel,
Autor, Ausgabe, Verlag, Erscheinungsdatum, Umfang und beide ISBN-Formen. Ein im
Datensatz hinterlegter Verlagslink wird zusätzlich für das Buchcover verwendet.

Amazon.de, Geizhals und Idealo sind experimentelle HTML-Provider. Amazon liefert
Suchtreffer und – soweit zugänglich – Stichpunkte und technische Fakten der
Produktseiten. Auch eine ASIN oder vollständige Amazon-Produkt-URL kann in das
Suchfeld eingefügt werden. Direkte Geizhals-URLs mit `-v<Nummer>.html` und
Idealo-URLs mit `OffersOfProduct/<Nummer>_...html` werden ebenfalls erkannt und
gezielt als Produktseite ausgewertet. Alle drei Anbieter können automatisierte
Zugriffe blockieren; die Anwendung zeigt das dann pro Quelle an. Webseiten können
ihr Markup oder ihre Zugriffsregeln jederzeit ändern, deshalb muss der gefundene
Text anhand der mitgespeicherten Quelle geprüft werden.

> **Rechtlicher Hinweis:** Der vorgegebene Text wird technisch unverändert
> angehängt, ist aber keine Rechtsberatung. Insbesondere behandelt § 475 BGB
> Verbrauchsgüterkäufe zwischen Unternehmern und Verbrauchern; für
> Haftungsausschlüsse ist auch § 444 BGB relevant. Den Wortlaut daher vor einer
> Veröffentlichung fachlich prüfen lassen.

## Geplante Features

- 🔄 Hersteller-Connectoren und strukturierte Produktdaten
- 🌐 Sprachen-Auswahl (Deutsch/Englisch)
- 🔗 eBay/Kleinanzeigen-API Integration
- 📝 Custom Template-System
- 💾 CSV-Import für Massendaten
- 📊 Verkaufsstatistiken

---

## Dateien

- `product_generator_gui.py` - **GUI Version (EMPFOHLEN)**
- `products.json` - Produktdatenbank
- `product_listings/` - Generierte Textdateien (oder Speicherpfad nach Wahl)

---

## Lizenz

Für private Nutzung
