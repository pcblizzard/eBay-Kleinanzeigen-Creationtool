# eBay Kleinanzeigen - Produktbeschreibungs-Generator

Ein Python-Tool zur Erstellung prüfbarer Produktbeschreibungen mit fest angehängtem Privatverkaufs-Hinweis.

## Features

- ✅ **GUI-Interface**: Benutzerfreundliche graphische Oberfläche
- ✅ **Produktsuche**: Intelligente Suche mit Live-Ergebnissen
- ✅ **Variantenauswahl**: Übersichtliche Auswahl bei mehreren Modellen
- ✅ **Prüfbare Beschreibung**: Produkttext vor dem Speichern bearbeiten
- ✅ **Privatverkaufs-Hinweis**: Automatisch angehängt, warnend editierbar und auf den Standard zurücksetzbar
- ✅ **Speicherort wählbar**: Standardmäßig im Projektordner `product_listings`
- ✅ **Quellennachweis**: Fundstellen werden pro Angabe gespeichert und beim
  Produktordner-Export als `quellen.txt` mit ausgegeben
- ✅ **Textdatei-Export**: Dateiname aus Titel und Plattform
- ✅ **Mehrere Beiträge**: Unabhängige Suchen und Entwürfe in separaten Tabs
- ✅ **EAN/GTIN-Suche**: Barcodes werden direkt an die Produktsuchen übergeben
- ✅ **ISBN-10/ISBN-13**: Deutsche Buchdaten über die Deutsche Nationalbibliothek
- ✅ **Geteilte Bearbeitung**: Editor und formatierte Live-Vorschau nebeneinander
- ✅ **Produktcover**: Bild der ausgewählten Produktseite wird automatisch geladen
- ✅ **Produktbild-Galerie**: Mehrere Amazon-Bilder durchblättern und einzeln speichern
- ✅ **Geteilte Vorschau**: Cover links, formatierter Verkaufstext rechts
- ✅ **Treffer in neuem Tab**: Varianten per Rechtsklick unabhängig öffnen
- ✅ **Produktlink-Import**: Amazon-, Geizhals-, Idealo- und Herstellerlinks direkt in das Suchfeld einfügen
- ✅ **Quellen & Qualität**: Treffer zeigen Herkunft und geschätzte Übereinstimmung
- ✅ **Sitzungswiederherstellung**: Offene Tabs und Entwürfe werden automatisch gesichert
- ✅ **Buchkataloge**: ISBN-Metadaten zusätzlich über Open Library und Google Books
- ✅ **Kleinanzeigen-Livesuche**: Optionale Inseratsuche über Kleinanzeigen Agent
- ✅ **eBay-Livesuche**: Optionale Produktsuche über die offizielle eBay Browse API
- ✅ **eBay-Kategorievorschläge**: Passende Kategorien über die aktuelle Taxonomy API
- ✅ **eBay-Artikelmerkmale**: Pflicht-, empfohlene und optionale Angaben dynamisch prüfen
- ✅ **eBay-Datenprüfung**: Fehlende Pflichtmerkmale vor einem späteren Export sichtbar machen
- ✅ **eBay-Angebot einstellen**: Offizieller Weg über OAuth, Bild-Upload und Sell-Inventory-API
- ✅ **Eigene Produktfotos**: Sortieren, aufbereiten und Standortdaten (GPS) entfernen
- ✅ **Inserat-Assistent**: Zustand, Lieferumfang, Preis und Vollständigkeit zentral prüfen
- ✅ **Plattform-Entwürfe**: Kleinanzeigen, eBay, ausführliches eBay und mobile eBay-Vorschau unabhängig bearbeiten
- ✅ **Zeichenlimits**: 65/4.000 für Kleinanzeigen sowie 80/4.000, 80/500.000 und 80/800 für eBay-Profile
- ✅ **Lokale Produktakte**: SQLite speichert Fakten, Quellen, Konflikte, Preise, Cache und Entwurfsversionen
- ✅ **Konfliktprüfung**: Abweichende Angaben werden vor einer Bestätigung hervorgehoben
- ✅ **Preisvergleich**: Aktive und tatsächlich verkaufte Vergleichspreise bleiben klar getrennt
- ✅ **Produktordner**: Plattformtexte, Bilder und interne Nachweise gemeinsam exportieren
- ✅ **Sichere API-Zugangsdaten**: Speicherung im Betriebssystem-Schlüsselspeicher
- ✅ **Sicherheitskontrollen**: Keyring-Prüfung, Verbindungstests und Secret-Löschung
- ✅ **Geschützte Webimporte**: Größenlimits und Sperre lokaler Netzwerkziele
- ✅ **Security-CI**: Abhängigkeits-, Secret- und CodeQL-Scans bei jedem Push
- ✅ **Sicherheitsdokumentation**: [SECURITY.md](SECURITY.md) beschreibt Umgang mit
  Zugangsdaten, Netzwerk und Dateien samt bekannter Einschränkungen

## Installation

**Anforderung:** Python 3.10+ mit tkinter. Die Abhängigkeiten `Pillow`
(Produktbilder) und `keyring` (Zugangsdaten) installiert pip mit:

```powershell
python -m pip install .
```

## Verwendung - GUI Version (EMPFOHLEN)

Nach der Installation:

```powershell
ebay-creationtool
```

Oder direkt aus dem geklonten Projektordner:

```powershell
python product_generator_gui.py
```

### Workflow:

1. Produktname, ISBN, EAN/GTIN oder Produktlink suchen.
2. Variante auswählen und gefundene Fakten beziehungsweise Konflikte prüfen.
3. Zustand, Lieferumfang und Wunschpreis im Inserat-Assistenten ergänzen.
4. Zwischen Kleinanzeigen-, eBay-, ausführlichem eBay- und mobilem eBay-Entwurf
   wechseln. Jeder Entwurf wird unabhängig gespeichert.
5. Zeichenanzeigen und Vollständigkeitshinweise prüfen.
6. Mit **Produktordner exportieren** alle Plattformtexte und verfügbaren Bilder
   in einem gemeinsamen Ordner ausgeben.

Die Standardgrenzen sind 65 Zeichen für einen Kleinanzeigen-Titel und 4.000
Zeichen für dessen Beschreibung. eBay-Titel sind auf 80 Zeichen begrenzt. Das
normale eBay-Profil verwendet die mit `product.description` kompatiblen 4.000
Zeichen; das ausführliche Angebotsprofil erlaubt bis zu 500.000 Zeichen und die
mobile Kurzvorschau bis zu 800 Zeichen.

### Lokale Produktdatenbank

Die SQLite-Datei `listings.db` liegt im lokalen Anwendungsdatenverzeichnis und
wird im Einstellungsdialog einschließlich ihrer aktuellen Größe angezeigt.
Gespeichert werden nur Texte, Quellen, Zustände, Preise, Konfliktentscheidungen,
Cache-Einträge und Bildpfade. Bilddateien selbst liegen außerhalb der Datenbank.
Dadurch bleibt sie üblicherweise auch bei vielen hundert Beiträgen nur wenige
Megabyte groß.

Technische Produktdaten werden länger, Suchergebnisse und Preise nur kurz
zwischengespeichert. Aktive Vergleichspreise sind ausdrücklich keine
tatsächlich erzielten Verkaufspreise. Sind keine verlässlichen abgeschlossenen
Verkäufe vorhanden, weist die Oberfläche darauf hin.

Der Produktordner bleibt für den Endbenutzer übersichtlich:

```text
Samsung Galaxy S23/
├── beitrag-kleinanzeigen.txt
├── beitrag-ebay.txt
├── beitrag-ebay-ausfuehrlich.txt
├── beitrag-ebay-mobil.txt
├── 01-hauptbild.jpg
└── .creationtool/
    ├── produktdaten.json
    └── quellen.txt
```

Der interne `.creationtool`-Ordner enthält Quellen und maschinenlesbare
Produktdaten; die sichtbaren Verkaufsdateien bleiben davon unabhängig.

1. **Produktname eingeben** → Echtzeit-Suche lädt Varianten
2. **Variante auswählen** → Live-Vorschau erscheint
3. **Beschreibung prüfen und bei Bedarf bearbeiten**
4. **Einstellungen** bei Bedarf über die obere Menüleiste öffnen
5. **Beitrag speichern** oder **Beitrag kopieren** in der oberen Werkzeugleiste

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
   → Datei "Samsung Galaxy S26-kleinanzeigen.txt" wird erstellt
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

Vollständige Produktlinks können direkt in das normale Suchfeld eingefügt
werden. Links von Amazon, Geizhals und Idealo werden gezielt an den jeweiligen
Importer übergeben; andere HTTP-/HTTPS-Produktseiten werden über strukturierte
Seitentitel, Beschreibungen und Datenlisten ausgewertet. Bei Amazon übernimmt
die Anwendung zusätzlich das hochauflösende `landingImage`, sofern verfügbar.
Blockiert Idealo oder Geizhals einen direkten Abruf, wird aus dem Link der
Produktname abgeleitet und automatisch über die übrigen Produktquellen gesucht.
Offensichtlich abgeschnittene Amazon-Titel werden mit Webvorschlägen
abgeglichen und nur bei einer eindeutigen Vervollständigung korrigiert.

ISBN-10 und ISBN-13 werden auch mit Bindestrichen erkannt, validiert und
gegenseitig umgerechnet. Für Bücher fragt das Tool die Deutsche
Nationalbibliothek, Open Library, Google Books und als Fallback ZVAB ab.
Identische Titel werden zusammengeführt und ihre Quellen gemeinsam angezeigt.

### eBay und Kleinanzeigen Agent

Unter **Einstellungen → Marktplatz-APIs** können die Zugangsdaten für beide
optionalen Quellen hinterlegt werden. Die Werte werden mit `keyring` im
Schlüsselspeicher des Betriebssystems abgelegt und ausdrücklich nicht in
`.eBayCreationToolConfig.json`, der Sitzungsdatei oder im Repository gespeichert.
Alternativ erkennt die Anwendung die Umgebungsvariablen `KLAZ_API_KEY`,
`EBAY_CLIENT_ID` und `EBAY_CLIENT_SECRET`.

Für Kleinanzeigen Agent genügt dessen API-Key. Die Quelle liefert öffentliche
Vergleichsinserate, Kategorien und strukturierte Merkmale. Für eBay werden
Production-Zugangsdaten aus dem eBay Developers Program benötigt: App-ID
(Client-ID) und Cert-ID (Client-Secret). Die Anwendung erzeugt daraus kurzlebige
Application-Tokens und sucht auf dem Marktplatz `EBAY_DE` per Suchbegriff oder
GTIN. Für ausgewählte Produkte schlägt die Taxonomy API passende deutsche
eBay-Kategorien vor. Nach der Kategorieauswahl zeigt das Tool die aktuellen
Pflicht-, empfohlenen und optionalen Artikelmerkmale an. Werte aus einem
eBay-Produktdatensatz werden nach Möglichkeit vorausgefüllt und können im
Prüfbereich bearbeitet werden.

Die eBay-Sandbox liefert bei Kategorie-Vorschlägen laut eBay nur Test- und
Platzhalterdaten. Das Tool überspringt diese Vorschläge deshalb in der Sandbox;
bereits im Suchtreffer enthaltene Kategorien und deren Merkmale können weiterhin
geprüft werden. Für echte Vorschläge ist die Production-Umgebung erforderlich.

### Angebot bei eBay einstellen

Über **🛒 Bei eBay einstellen…** wird ein Angebot auf dem offiziellen Weg
angelegt. Voraussetzungen sind Client-ID und Client-Secret sowie ein **RuName**
aus dem eBay Developers Program. Der Ablauf im Dialog:

1. **Zugriff erteilen.** Der Browser öffnet die eBay-Einwilligungsseite. Nach
   dem Zustimmen leitet eBay auf deine RuName-Adresse weiter; die vollständige
   Adresse wird zurück in das Feld kopiert. Gespeichert wird ausschließlich der
   Erneuerungstoken, und zwar im Schlüsselspeicher des Betriebssystems.
2. **Richtlinien und Standort.** Versand-, Zahlungs- und Rücknahmerichtlinie
   werden aus dem Konto geladen und ausgewählt; Postleitzahl und Land ergeben
   den Lagerort, ohne den eBay nicht veröffentlicht.
3. **Angebot.** Menge festlegen, **Angaben prüfen** nennt alles Fehlende.

**Angebot anlegen und veröffentlichen** lädt zuerst die eigenen Fotos zu den
eBay Picture Services, legt Lagerort, Bestandsartikel und Angebot an und
veröffentlicht zuletzt. Alle Schritte vor der Veröffentlichung verändern nichts
an bestehenden Angeboten. Vorher nennt eine Rückfrage Titel, Preis, Kategorie,
Anzahl der Fotos und die gewählte Umgebung — es entsteht ein öffentliches,
kostenpflichtiges Angebot.

Zum Ausprobieren eignet sich die Sandbox-Umgebung. Die Zugangsdaten dafür sind
andere als die der Produktionsumgebung.

Kleinanzeigen Agent dokumentiert dagegen keine Funktion zum Veröffentlichen;
alle Endpunkte sind lesend. Anzeigen werden dort von Hand eingestellt — das
Werkzeug bereitet Text und Fotos vollständig vor.

### Eigene Produktfotos

Neben den online gefundenen Bildern lassen sich in der Cover-Spalte eigene
Fotos hinterlegen, sortieren und entfernen; das erste ist das Hauptbild.
Gespeichert werden nur Dateipfade, das Entfernen löscht nie die Originaldatei.

Beim Produktordner-Export und beim eBay-Upload werden die Fotos aufbereitet:
**sämtliche EXIF-Daten werden entfernt**, die Drehung nach der
Orientierungsangabe korrigiert und die Kantenlänge begrenzt. Das Entfernen der
Metadaten ist kein Beiwerk — Handyfotos enthalten GPS-Koordinaten, und wer den
Artikel zu Hause fotografiert, veröffentlicht sonst mit dem Bild seine
Wohnadresse. Kleinanzeigen erlaubt bis zu 20 Bilder, eBay bis zu 24.

Der Kleinanzeigen-Verbindungstest verwendet eine reguläre Ein-Treffer-Abfrage
mit eindeutigem App-User-Agent. Strukturierte API-Fehler werden ohne Schlüssel
oder Authorization-Header angezeigt. Der Test verbraucht einen Credit.

### Produktbilder

Amazon-Produktseiten werden neben dem Hauptbild auch nach den Galerieeinträgen
`hiRes`, `large` und `mainUrl` ausgewertet. Bis zu 20 unterschiedliche Bilder
werden in der Cover-Spalte angeboten. Pfeiltasten wechseln das aktuelle Bild,
der Zähler zeigt die Position und **Bild speichern…** lädt auf ausdrücklichen
Wunsch die angezeigte Originaldatei in einen gewählten Ordner. Amazon-
Thumbnail- und Auflösungsvarianten desselben Galerieeintrags zählen dabei nicht
als zusätzliche Bilder. Gespeicherte Dateien erhalten fortlaufende Endungen wie
`_01.jpg`, `_02.jpg` und so weiter. Die Darstellung skaliert nach verfügbarer
Breite und Höhe, damit die Galeriebuttons auch in kleineren Fenstern sichtbar
bleiben. Bilder werden
nicht automatisch dauerhaft gespeichert. Nutzungs- und Veröffentlichungsrechte
an Bildern müssen unabhängig davon beachtet werden.

## Sicherheit

Die Anwendung akzeptiert für Zugangsdaten ausschließlich ein geeignetes
Betriebssystem-Keyring. Unter Windows muss der Windows Credential Locker aktiv
sein; Null-, Fail- oder unverschlüsselte Backends werden abgewiesen. Im
Einstellungsdialog können Zugangsdaten ersetzt, getestet und vollständig aus dem
Keyring gelöscht werden. Bereits gespeicherte Werte erscheinen ausschließlich
als Maskierungsplatzhalter; der echte Inhalt wird nie in das Eingabefeld
zurückgelesen. Der Kleinanzeigen-Verbindungstest führt eine minimale
Live-Suche aus und verbraucht dabei einen Credit.

Für eBay kann getrennt zwischen `sandbox` und `production` gewählt werden.
Application-Tokens bleiben nur im Arbeitsspeicher. Sicherheitsrelevante Aktionen
werden ohne Schlüssel, Tokens oder API-Antworten in
`~/.eBayCreationToolSecurity.log` protokolliert.

Die Sitzungswiederherstellung kann abgeschaltet werden. Optional löscht die App
beim Beenden die lokale Sitzungsdatei. Importierte Produktlinks dürfen nur HTTP
oder HTTPS verwenden; `localhost` sowie direkt angegebene private oder lokale
IP-Adressen werden vor dem Abruf und nach Weiterleitungen abgewiesen. Diese
Prüfung wertet den Hostnamen aus und löst ihn nicht auf: ein öffentlicher Name,
der auf eine interne Adresse zeigt, wird nicht erkannt. Textantworten sind auf
5 MB, Bilder auf 15 MB und 50 Megapixel begrenzt.

GitHub Actions führt zusätzlich `pip-audit` und Gitleaks aus. Dependabot prüft
wöchentlich Python- und GitHub-Actions-Abhängigkeiten. Lokal lässt sich der Audit
mit Python 3.14 starten:

```powershell
py -3.14 -m pip install pip-audit
py -3.14 -m pip_audit .
```

Bei der derzeitigen Python-3.15-Vorabversion kann die Installation unter Windows
scheitern, solange für die indirekte Abhängigkeit `msgpack` kein passendes Wheel
vorliegt. Das betrifft die Entwicklungsprüfung, nicht die Anwendung.

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
> angehängt, solange er nicht in den Einstellungen bewusst bearbeitet wird.
> Vor der Übernahme eines geänderten Wortlauts zeigt die Anwendung eine
> Warnung. Weder Standardtext noch Warnung sind eine Rechtsberatung.
> Insbesondere behandelt § 475 BGB
> Verbrauchsgüterkäufe zwischen Unternehmern und Verbrauchern; für
> Haftungsausschlüsse ist auch § 444 BGB relevant. Den Wortlaut daher vor einer
> Veröffentlichung fachlich prüfen lassen.

## Geplante Features

- 🔄 Weitere Hersteller-Connectoren und strukturierte Produktdaten
- 🔗 Optionales Veröffentlichen über unterstützte offizielle Plattform-APIs
- 📝 Custom Template-System
- 💾 CSV-Import für Massendaten
- 📊 Verkaufsstatistiken

---

## Dateien

- `product_generator_gui.py` - Oberfläche, Online-Quellen und Entwurfslogik
- `listing_store.py` - SQLite-Produktakte, Plattformprofile und Paketexport
- `ebay_listing.py` - Offizieller eBay-Angebotsfluss (OAuth, Bilder, Inventory-API)
- `products.json` - Mitgelieferte lokale Produktdatenbank
- `test_product_generator.py` - Testsuite (`python -m unittest`)
- `product_listings/` - Generierte Textdateien (oder Speicherpfad nach Wahl)

---

## Lizenz

MIT — siehe [LICENSE](LICENSE).
