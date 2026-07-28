# Sicherheit

## Schwachstellen melden

Bitte melde Sicherheitsprobleme **nicht** über ein öffentliches Issue, sondern
über die private Meldefunktion von GitHub:

> Repository → **Security** → **Report a vulnerability**

Falls das nicht möglich ist, eröffne ein Issue ohne technische Einzelheiten mit
der Bitte um einen privaten Kontaktweg.

## Einordnung dieses Dokuments

Dieses Dokument beschreibt, **wie die Anwendung mit Zugangsdaten, Netzwerk und
Dateien umgeht** und welche Prüfungen laufen. Es ist eine Selbstauskunft der
Entwicklung und ausdrücklich **kein unabhängiges Sicherheitsaudit**. Eine
Prüfung durch Dritte hat nicht stattgefunden.

Wer eine belastbare Aussage braucht, sollte den Quelltext selbst lesen – er ist
vollständig offen, und jeder der im Folgenden genannten Punkte lässt sich daran
nachvollziehen. Genau dafür ist dieses Dokument gedacht: als Wegweiser zu den
Stellen, an denen man selbst nachsieht, nicht als Ersatz dafür.

## Was die Anwendung ist

Ein lokales Desktop-Werkzeug ohne Server-Anteil. Es läuft mit den Rechten des
angemeldeten Benutzers, nimmt keine eingehenden Verbindungen an und öffnet
keine Ports. Alle Daten bleiben auf dem Rechner, sofern nicht ausdrücklich
etwas an eine Plattform gesendet wird.

## Zugangsdaten

| | |
|---|---|
| **Speicherort** | Ausschließlich der Schlüsselspeicher des Betriebssystems über `keyring`. Alternativ werden die Umgebungsvariablen `KLAZ_API_KEY`, `EBAY_CLIENT_ID` und `EBAY_CLIENT_SECRET` gelesen. |
| **Backend-Prüfung** | Null-, Fail- und unverschlüsselte Backends werden abgewiesen. Unter Windows wird der Windows Credential Locker verlangt. |
| **Konfigurationsdatei** | Enthält **keine** Zugangsdaten – nur Sprache, Pfade, Umgebung und Anzeigeeinstellungen. |
| **Anzeige** | Gespeicherte Werte erscheinen nur als Maskierungsplatzhalter und werden nie in das Eingabefeld zurückgelesen. |
| **Löschen** | Alle gespeicherten Geheimnisse lassen sich über *Einstellungen → Marktplatz-APIs* entfernen; die eBay-Einwilligung zusätzlich einzeln im eBay-Dialog. Ein Test stellt sicher, dass jedes gespeicherte Geheimnis auch löschbar ist. |
| **eBay-Tokens** | Nur der Erneuerungstoken wird abgelegt. Der kurzlebige Zugriffstoken bleibt im Arbeitsspeicher. |

Ein dauerhafter Widerruf des eBay-Zugriffs erfolgt im eBay-Konto unter
*Kontoeinstellungen → Anwendungen*; das Löschen in der Anwendung entfernt nur
die lokale Kopie.

## Netzwerk

- TLS-Zertifikate werden durchgängig geprüft; die Prüfung wird nirgends
  abgeschaltet.
- Importierte Produktlinks dürfen nur `http` oder `https` verwenden.
  `localhost` sowie direkt angegebene private oder lokale IP-Adressen werden
  vor dem Abruf **und nach Weiterleitungen** abgewiesen.
- Größenbegrenzungen: Textantworten 5 MB, Bilder 15 MB und 50 Megapixel,
  eBay-Antworten 8 MB.
- Der eBay-Zugriff verwendet nur die Berechtigungen `sell.inventory` und
  `sell.account`.
- Der OAuth-Statuswert wird erzeugt **und beim Einlösen geprüft**, damit eine
  untergeschobene Weiterleitungsadresse das Werkzeug nicht an ein fremdes Konto
  binden kann.

## Daten auf der Festplatte

| Datei | Inhalt |
|---|---|
| `~/.eBayCreationToolConfig.json` | Einstellungen, keine Zugangsdaten |
| `~/.eBayCreationToolSession.json` | Offene Tabs und Entwürfe |
| `~/.eBayCreationToolSecurity.log` | Nur Metadaten: Zeitstempel, Aktion, Anbieter, Ergebnis – niemals Schlüssel, Tokens oder API-Antworten |
| `listings.db` (Anwendungsdatenverzeichnis) | Texte, Quellen, Preise, Bildpfade – keine Bilddaten, keine Zugangsdaten |

Diese Dateien werden nach dem Schreiben auf den Eigentümer beschränkt (`0600`).
Unter Windows greifen die Zugriffslisten des Benutzerprofils.

## Privatsphäre bei eigenen Fotos

Vor dem Export und vor dem Upload werden **sämtliche EXIF-Daten entfernt**,
insbesondere GPS-Koordinaten. Handyfotos enthalten den Aufnahmeort; ohne diesen
Schritt veröffentlicht man mit dem Bild seine Wohnadresse. Zusätzlich wird die
Drehung korrigiert und die Kantenlänge begrenzt.

## Veröffentlichen

Ein eBay-Angebot entsteht erst nach einer ausdrücklichen Rückfrage, die Titel,
Preis, Kategorie, Anzahl der Fotos und die Umgebung nennt und auf *Nein*
voreingestellt ist. Alle vorbereitenden Schritte verändern keine bestehenden
Angebote; ein Test belegt, dass dabei keine Veröffentlichungs-Adresse
aufgerufen wird.

Für Kleinanzeigen gibt es **keine** Automatisierung. Ein entsprechender Versuch
wurde erprobt und wieder entfernt – die Begründung steht im
[CHANGELOG](CHANGELOG.md). Techniken zur Umgehung von Bot-Erkennung sind nicht
enthalten und werden nicht aufgenommen.

## Automatische Prüfungen

Bei jedem Push und Pull Request laufen:

- **Tests** unter Ubuntu und Windows für Python 3.10, 3.12 und 3.14
- **`pip-audit`** gegen bekannte Schwachstellen in Abhängigkeiten
- **Gitleaks** über die vollständige Historie
- **CodeQL** als statische Sicherheitsanalyse, zusätzlich wöchentlich

Dependabot prüft wöchentlich Python- und Actions-Abhängigkeiten.

Die Ergebnisse sind im Tab *Actions* beziehungsweise *Security* einsehbar –
maschinell erhoben und damit belastbarer als die Beschreibung in diesem
Dokument. CodeQL hat dabei bereits echte Fehler gefunden, die behoben wurden;
die Zusammenfassung steht im [CHANGELOG](CHANGELOG.md).

Der CodeQL-Job überspringt sich selbst, solange das Repository privat ist:
Code-Scanning setzt dann GitHub Advanced Security voraus, und der Job fiele
sonst dauerhaft aus – was daran gewöhnt, Fehlschläge zu übersehen.

## Bekannte Einschränkungen

Diese Punkte sind bewusst offen und sollen nicht verschwiegen werden:

1. **Die Prüfung importierter Adressen ist syntaktisch.** Der Hostname wird
   ausgewertet, aber nicht aufgelöst. Ein öffentlicher Name, der auf eine
   interne Adresse zeigt, wird nicht erkannt. Für ein lokales Werkzeug, in das
   der Benutzer selbst Links einfügt, wird das als vertretbar eingeschätzt.
2. **XML wird mit `xml.etree` verarbeitet.** Das Modul ist laut
   Python-Dokumentation nicht gegen bösartig konstruierte Daten gehärtet.
   Betroffen sind nur Antworten von `api.ebay.com` und `d-nb.info` über TLS,
   und deren Größe ist begrenzt.
3. **Die HTML-Anbieter** (Amazon, Geizhals, Idealo) werten fremdes Markup mit
   regulären Ausdrücken aus. Das Ergebnis landet als Text in einem Entwurf und
   wird nirgends ausgeführt oder gerendert – gelesene Angaben sind aber
   grundsätzlich anhand der mitgespeicherten Quelle zu prüfen.

## Abhängigkeiten

`Pillow` (Bildverarbeitung) und `keyring` (Zugangsdaten). Sonst ausschließlich
die Standardbibliothek. Wenige Abhängigkeiten bedeuten eine kleine
Angriffsfläche über die Lieferkette.
