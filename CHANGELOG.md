# Changelog

Alle wesentlichen Änderungen an diesem Projekt werden in dieser Datei dokumentiert.

## [0.2.0] - 2026-07-27

### Hinzugefügt

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

- Online-Suchen laufen auch bei vorhandenen lokalen Treffern.
- Produktdetails werden bei Auswahl nachgeladen und in einen editierbaren Verkaufsbeitrag umgewandelt.
- Treffer werden nach Relevanz priorisiert und Werbe-/Navigationsresultate gefiltert.
- Der feste Privatverkaufstext wird unveränderbar am Ende jeder Ausgabedatei angehängt.

### Entfernt

- Nicht mehr benötigte CLI-Version.
- Keepa-Konfiguration und API-Schlüsseloption.

## [0.1.0] - 2025-07-20

- Erste GUI-Version des Produktbeschreibungs-Generators.
