# Changelog

Alle wesentlichen Änderungen an diesem Projekt werden in dieser Datei dokumentiert.

## Unveröffentlicht

### Hinzugefügt

- Angebote lassen sich über die offiziellen eBay-Schnittstellen einstellen:
  Benutzer-Einwilligung per OAuth, Upload eigener Fotos zu den eBay Picture
  Services, Lagerort, Bestandsartikel, Angebot und Veröffentlichung. Vor dem
  Veröffentlichen nennt eine Rückfrage Titel, Preis, Kategorie, Anzahl der
  Fotos und die Umgebung; die Schritte davor verändern nichts an aktiven
  Angeboten.
- Eigene Produktfotos verwalten, sortieren und aufbereiten. Beim Export
  werden Standortdaten (GPS) entfernt, die Drehung korrigiert und die Größe
  angepasst; die Dateien werden in Anzeigereihenfolge benannt.
- Der Wunschpreis kennt die bei Kleinanzeigen übliche Preisart: VB,
  Festpreis oder „Zu verschenken“.

### Nicht umsetzbar: Anzeigen automatisch bei Kleinanzeigen einstellen

Ein assistierter Browser-Ansatz (Playwright, sichtbares Fenster, eigenes
Profil, Absenden von Hand) wurde umgesetzt und wieder entfernt. Kleinanzeigen
erkannte die Automatisierung bereits **beim Anmelden** und sperrte den
IP-Bereich vorübergehend – das Formular wurde nie erreicht. Das Benutzerkonto
blieb unberührt.

Damit ist der Stand für Kleinanzeigen:

- Keine Schreib-Schnittstelle. Die API von kleinanzeigen-agent.de ist laut
  eigener Dokumentation ausschließlich lesend, alle Endpunkte sind `GET`.
- Verfügbare Projekte (DanielWTE/ebay-kleinanzeigen-api, Apify, ScrapingBee,
  Octoparse, Automatio) lesen ausnahmslos Inserate aus; keines stellt welche
  ein.
- Browser-Automatisierung wird erkannt. Sie zuverlässig zu machen erforderte
  das Aushebeln der Bot-Erkennung; das wird bewusst nicht gebaut, weil es die
  Nutzungsbedingungen verletzt und das Konto gefährdet.

Anzeigen werden bei Kleinanzeigen daher von Hand eingestellt. Das Werkzeug
bereitet dafür Text und Fotos vollständig vor.

## [0.3.0] - 2026-07-27

### Behoben

- Ein fehlgeschlagener Speichervorgang zeigte wegen einer nach dem
  `except`-Block gelöschten Variablen gar keine Fehlermeldung mehr an.
- Das installierte Konsolenskript findet Produktdatenbank und Ausgabeordner
  jetzt unabhängig vom Arbeitsverzeichnis; `products.json` wird mitinstalliert.
- Geschlossene Tabs geben Fenster und Datenbankverbindung nach einer Schonfrist
  wieder frei, statt bis zum Programmende belegt zu bleiben.
- Datenbankzugriffe aus den Netzwerk-Threads werden serialisiert; ein `commit()`
  konnte zuvor die offene Transaktion eines anderen Threads beenden.
- Gekürzte Beschreibungen brechen am Limit ab, statt einen zu langen Absatz zu
  überspringen und dadurch Inhalte aus der Mitte zu entfernen.
- Preisangaben mit Tausenderpunkt wie „1.234,56“ werden korrekt gelesen.
- Nicht abrufbare Produktbilder werden beim Paketexport benannt statt
  verschwiegen.
- Ein nicht beschreibbares Sicherheitsprotokoll bricht die auslösende Aktion
  nicht mehr ab.

### Geändert

- Sitzungen werden alle 15 Sekunden und nur bei tatsächlichen Änderungen
  gespeichert statt alle 2 Sekunden unbedingt.
- Der tab-übergreifende Suchcache räumt abgelaufene Einträge ab und ist auf
  128 Einträge begrenzt.
- Zeichenlimit-Meldungen erscheinen in der eingestellten Oberflächensprache.
- `locale.getdefaultlocale()` durch eine nicht veraltete Spracherkennung
  ersetzt.
- Speicherpfad und Dateinamensvergabe laufen für Oberfläche und Backend über
  denselben Code; die ungenutzte `generate_listing`-Variante entfiel.
- CI testet zusätzlich unter Windows und kompiliert `listing_store.py` mit.

## [0.2.0] - 2026-07-27

### Hinzugefügt

- Plattformneutralen Inserat-Assistenten für Zustand, Lieferumfang, Wunschpreis
  und Vollständigkeit ergänzt.
- Vier voneinander unabhängige Entwürfe für Kleinanzeigen, eBay, ausführliche
  eBay-Beschreibungen und die mobile 800-Zeichen-Vorschau ergänzt.
- Plattformgrenzen werden live gezählt und vor Speichern, Kopieren und
  Paketexport verbindlich geprüft.
- Lokale SQLite-Produktakte für Produktzustand, Quellenfakten, Konflikte,
  Preisvergleiche, Suchcache und versionierte Entwürfe ergänzt; Bilder verbleiben
  als normale Dateien.
- Aktive Vergleichspreise und tatsächlich verkaufte Preise werden getrennt
  gespeichert und beschriftet; fehlende Verkaufsdaten werden nicht durch aktive
  Angebote ersetzt.
- Abweichende Werte derselben Produktangabe können im Konfliktdialog gezielt
  bestätigt, andere Werte verworfen werden.
- Neuer Produktordner-Export schreibt plattformspezifische Texte, verfügbare
  Produktbilder sowie interne Produkt- und Quellennachweise.
- Bereits sicher gespeicherte Marktplatz-Zugangsdaten werden in den Eingabefeldern
  durch einen festen Maskierungsplatzhalter sichtbar gemacht. Der Platzhalter
  kann weder gespeichert noch für einen Verbindungstest als Schlüssel verwendet
  werden.
- eBay-Kategorievorschläge über die aktuelle Commerce Taxonomy API ergänzt.
- Kategoriepfade sowie Pflicht-, empfohlene und optionale Artikelmerkmale werden
  pro Beitrag in einem unabhängigen Prüfbereich dargestellt.
- Artikelmerkmale können mit eBay-Wertvorschlägen bearbeitet werden; eine
  Vollständigkeitsanzeige nennt noch fehlende Pflichtwerte.
- Die Browse API lädt für ausgewählte eBay-Treffer zusätzliche Produktdetails,
  strukturierte Merkmale und Produktbilder nach.
- Sandbox und Production bleiben getrennt; unbrauchbare Sandbox-
  Kategorieplatzhalter werden nicht als echte Vorschläge angezeigt.
- Die eingestellte Finding API wird bewusst nicht als Fallback verwendet.
- Kleinanzeigen-Agent-Anfragen senden nun einen eindeutigen App-User-Agent;
  der zuvor mit gültigen Keys mögliche HTTP-403-Verbindungstest ist behoben.
- Der Verbindungstest nutzt eine minimale reguläre Ein-Treffer-Abfrage und zeigt
  strukturierte API-Fehlermeldungen ohne sensible Header an.
- Amazon-Galeriebilder werden zusätzlich zum Hauptbild in hoher Auflösung
  erkannt, dedupliziert und in der Vorschau durchblätterbar angezeigt.
- Pro Amazon-Galerieeintrag wird nur `hiRes` beziehungsweise die beste
  verfügbare Variante übernommen; Thumbnail-/Large-Doppelungen werden entfernt.
- Das aktuell angezeigte Produktbild kann über einen Speichern-Dialog als
  Originaldatei mit fortlaufender Nummer (`_01`, `_02`, …) heruntergeladen
  werden.
- Bildskalierung berücksichtigt die verfügbare Höhe; Navigation und
  Speichern-Button bleiben im Fenstermodus sichtbar.
- Der Gitleaks-Job besitzt die für Dependabot-PRs erforderliche reine
  `pull-requests: read`-Berechtigung.
- Sicheren Keyring-Backend-Check ergänzt; unsichere oder fehlende Backends
  werden für die Secret-Speicherung abgewiesen.
- Zugangsdaten können im Einstellungsdialog getestet, ersetzt und gelöscht
  werden.
- eBay Sandbox und Production sind als getrennte Umgebungen auswählbar.
- Metadatenbasiertes Sicherheitsprotokoll ohne Secrets oder API-Inhalte ergänzt.
- Sitzungswiederherstellung kann deaktiviert und die Sitzungsdatei beim Beenden
  automatisch gelöscht werden.
- Webimporte blockieren lokale/private IP-Ziele und begrenzen Text-, Bild- und
  Pixelgrößen.
- GitHub Actions führt jetzt `pip-audit` und Gitleaks aus; Dependabot überwacht
  Python- und Actions-Abhängigkeiten.
- Kleinanzeigen Agent als optionale Live-Quelle für öffentliche Inserate,
  Kategorien und strukturierte Merkmale ergänzt.
- eBay.de über die offizielle Browse API mit Stichwort- und GTIN-Suche ergänzt.
- API-Schlüssel und eBay-Anwendungsdaten werden ausschließlich im sicheren
  Betriebssystem-Schlüsselspeicher verwaltet; Konfigurations- und Sitzungsdateien
  enthalten keine Zugangsdaten.
- Marktplatz-Zugangsdaten können maskiert im Einstellungsdialog hinterlegt werden.
- Automatisierte Provider-Tests für beide neuen Quellen ergänzt.
- Der Privatverkaufs-Hinweis kann in den Einstellungen global bearbeitet und
  auf den mitgelieferten Standard zurückgesetzt werden. Vor der Übernahme
  eines abweichenden Wortlauts erscheint eine rechtliche Warnung.
- Automatische Sicherung und Wiederherstellung geöffneter Tabs und Entwürfe.
- Trefferkennzeichnung mit Quelle und geschätzter Übereinstimmungsqualität.
- Zusammenführung identischer Produkttitel mit gemeinsamer Quellenanzeige.
- Zeitlich begrenzter Suchcache zur Vermeidung identischer Netzwerkanfragen.
- Strukturierte ISBN-Suche über Open Library und Google Books ohne
  erforderlichen API-Schlüssel.
- Amazon-Produktbilder werden aus `landingImage`, `data-old-hires` und
  `data-a-dynamic-image` gelesen; hochauflösende Varianten haben Vorrang.
- Die Live-Vorschau zeigt das Cover in einer eigenen linken Spalte und den
  formatierten Beitrag vollständig im rechten Bereich.
- Die Breite der Cover-Spalte ist per Trennbalken veränderbar; Produktbilder
  werden proportional auf den jeweils verfügbaren Platz skaliert.
- Varianten können über das Rechtsklickmenü als unabhängige Kopie in einem
  neuen Tab geöffnet werden.
- Vollständige Amazon-, Geizhals-, Idealo- und allgemeine Produktlinks können
  direkt über das Suchfeld importiert werden.
- Amazon-Linkimporte entfernen eingebettete JavaScriptfragmente aus
  technischen Produktangaben.
- Blockierte Idealo-/Geizhals-Produktlinks fallen automatisch auf passende
  Treffer aus den übrigen Produktquellen zurück.
- Nachweislich abgeschnittene Amazon-Titel werden nur bei einer eindeutigen
  Vervollständigung aus Webvorschlägen repariert.
- Bekannte eindeutige Amazon-Wortabbrüche werden zusätzlich offline beim
  Auswählen, bei Cachetreffern und beim Wiederherstellen alter Sitzungen
  korrigiert.
- Parallele Livesuche über Web-Vorschläge, Wikipedia, Amazon, Geizhals und Idealo.
- Strukturierte ISBN-Suche über die Deutsche Nationalbibliothek.
- Automatische Normalisierung und gegenseitige Umrechnung von ISBN-10 und ISBN-13.
- EAN-/GTIN-Suche sowie alternative Modellschreibweisen wie `S23` und `S 23`.
- Produktabhängige Verkaufsentwürfe für Hardware, Software, Bücher und physische Medien.
- Unabhängige Beiträge in mehreren Tabs.
- Geteilter Editor mit formatierter Live-Vorschau.
- Produkt- und Buchcover aus Produkt- und Verlagsseiten.
- Automatische Tests über GitHub Actions.

### Geändert

- Die Einstellungen wurden vollständig aus den Beitrags-Tabs entfernt und
  sind über das obere Menü in einem eigenen Dialog erreichbar.
- Sprache, Schriftgröße, Speicherpfad und Provider-Auswahl werden auf alle
  geöffneten Tabs angewendet.
- „Beitrag speichern“ und „Beitrag kopieren“ sind dauerhaft in der oberen
  Werkzeugleiste erreichbar.
- Ein neues Datei-Menü bietet Neu, Öffnen, Speichern und Beenden. Geöffnete
  TXT-Beiträge werden in einem eigenen Tab bearbeitet und beim Speichern
  gezielt aktualisiert.
- Ein Sprachwechsel erzeugt den aktuellen Verkaufsentwurf neu und übersetzt
  Oberfläche, Vorlagen sowie bekannte strukturierte Metadatenfelder.
- Der verbindliche deutsche Privatverkaufstext bleibt unabhängig von der
  ausgewählten Sprache unverändert.
- Online-Suchen laufen auch bei vorhandenen lokalen Treffern.
- Produktdetails werden bei Auswahl nachgeladen und in einen editierbaren Verkaufsbeitrag umgewandelt.
- Treffer werden nach Relevanz priorisiert und Werbe-/Navigationsresultate gefiltert.
- Der feste Privatverkaufstext wird unveränderbar am Ende jeder Ausgabedatei angehängt.

### Behoben

- „Speichern“ im Einstellungsdialog speichert nur die Konfiguration und
  verlangt keine ausgewählte Produktvariante mehr.
- DNB-Metadaten werden auf Unicode-NFC normalisiert, damit Umlaute in Tk korrekt erscheinen.
- Quellenbeschriftungen bleiben unter dem Windows-/Vista-Theme sichtbar.
- Geizhals-Social-Media-, Werbe-, Hilfe- und Navigationslinks werden nicht als Produkte angezeigt.
- Amazon-Werbeanzeigen und generische Angebotstreffer werden vollständig ausgefiltert.
- Die Live-Vorschau kompiliert auch unter Python 3.10.
- Unabhängig veröffentlichte Bücher erhalten einen exakten ISBN-Fallback über ZVAB.
- ISBN-basierte AbeBooks-/ZVAB-Cover werden unterstützt.

### Entfernt

- Nicht mehr benötigte CLI-Version.
- Keepa-Konfiguration und API-Schlüsseloption.

## [0.1.0] - 2025-07-20

- Erste GUI-Version des Produktbeschreibungs-Generators.
