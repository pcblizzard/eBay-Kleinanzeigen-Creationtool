"""Assistiertes Ausfüllen des Kleinanzeigen-Formulars.

Bewusst *assistiert* und nicht vollautomatisch: Der Browser ist sichtbar, läuft
mit einem dauerhaften Profil des Nutzers, und das Absenden bleibt ihm
überlassen. Das Werkzeug übernimmt nur das Abtippen und das Anhängen der
vorbereiteten Fotos.

Damit ist keine Umgehung der Bot-Erkennung nötig und niemand veröffentlicht
unbeaufsichtigt. Kleinanzeigen untersagt automatisierten Zugriff; auch dieser
Weg bleibt eine Grauzone, die der Nutzer bewusst wählen muss.
"""

from __future__ import annotations

import os
import queue
import threading
from dataclasses import dataclass, field
from pathlib import Path

POST_AD_URL = "https://www.kleinanzeigen.de/p-anzeige-aufgeben.html"

# Pro Feld mehrere Kandidaten: Kleinanzeigen hat die Formularnamen ueber die
# Jahre mehrfach geaendert. Der erste Treffer gewinnt, danach greift die
# label-basierte Suche, die Umbenennungen am ehesten ueberlebt.
FIELD_SELECTORS = {
    "title": (
        "#postad-title",
        "input[name='title']",
        "#pstad-title",
    ),
    "description": (
        "#pstad-descrptn",
        "textarea[name='description']",
        "#postad-description",
    ),
    "price": (
        "#pstad-price",
        "input[name='price']",
        "#postad-price",
    ),
}
FIELD_LABELS = {
    "title": ("Anzeigentitel", "Titel"),
    "description": ("Beschreibung",),
    "price": ("Preis", "Preis (€)", "VB"),
}
PRICE_TYPE_SELECTORS = {
    "FIXED": ("#priceType-FIXED", "input[value='FIXED']"),
    "NEGOTIABLE": ("#priceType-NEGOTIABLE", "input[value='NEGOTIABLE']"),
    "GIVE_AWAY": ("#priceType-GIVE_AWAY", "input[value='GIVE_AWAY']"),
}
PHOTO_INPUT_SELECTORS = (
    "input[type='file'][accept*='image']",
    "input[type='file']",
)


# Welcher Browser gesteuert wird. "msedge" und "chrome" nutzen die bereits
# installierte Anwendung - dann muss Playwright keinen eigenen Browser laden.
# (engine, channel); channel=None bedeutet den mitgelieferten Browser.
BROWSER_CHOICES = {
    'msedge': ('chromium', 'msedge'),
    'chrome': ('chromium', 'chrome'),
    'chromium': ('chromium', None),
    'firefox': ('firefox', None),
}
DEFAULT_BROWSER = 'msedge' if os.name == 'nt' else 'chromium'


class PlaywrightMissing(RuntimeError):
    """Playwright ist nicht installiert."""


class BrowserMissing(RuntimeError):
    """Der gewaehlte Browser ist nicht auffindbar."""


@dataclass
class FormData:
    """Die Angaben, die in das Formular übertragen werden."""

    title: str = ""
    description: str = ""
    price: str = ""
    price_type: str = "NEGOTIABLE"
    photos: list = field(default_factory=list)


@dataclass
class FillReport:
    """Was tatsächlich gefüllt wurde - und was nicht."""

    filled: list = field(default_factory=list)
    skipped: list = field(default_factory=list)

    def add(self, field_name: str, success: bool):
        (self.filled if success else self.skipped).append(field_name)

    @property
    def complete(self) -> bool:
        return not self.skipped


def price_type_from_label(label: str, free_label: str, fixed_label: str) -> str:
    """Uebersetzt die Auswahl der Oberflaeche in den Formularwert."""
    normalized = str(label or "").strip().casefold()
    if normalized == free_label.casefold():
        return "GIVE_AWAY"
    if normalized == fixed_label.casefold():
        return "FIXED"
    return "NEGOTIABLE"


def import_playwright():
    """Laedt Playwright erst bei Bedarf; es ist eine optionale Abhaengigkeit."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise PlaywrightMissing(
            "Playwright ist nicht installiert. Installation:\n"
            "    python -m pip install playwright\n"
            "    python -m playwright install chromium"
        ) from error
    return sync_playwright


class KleinanzeigenFormAssistant:
    """Öffnet das Formular und füllt es auf ausdrückliche Anforderung.

    Der Ablauf ist zweigeteilt, weil die Kategorie den Rest des Formulars
    bestimmt: erst öffnet der Nutzer die passende Kategorie selbst, danach
    überträgt ``fill`` die Angaben in die dann sichtbaren Felder.
    """

    def __init__(self, profile_dir, headless=False, browser=DEFAULT_BROWSER):
        self.profile_dir = Path(profile_dir)
        self.headless = headless
        self.browser = browser if browser in BROWSER_CHOICES else DEFAULT_BROWSER
        self._playwright = None
        self._context = None
        self.page = None

    def start(self, url: str = POST_AD_URL):
        """Startet den sichtbaren Browser mit dem dauerhaften Profil."""
        sync_playwright = import_playwright()
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        engine_name, channel = BROWSER_CHOICES[self.browser]
        self._playwright = sync_playwright().start()
        engine = getattr(self._playwright, engine_name)
        options = {
            "headless": self.headless,
            "viewport": None,
        }
        if channel:
            # Nutzt die vorhandene Installation, kein eigener Download.
            options["channel"] = channel
        elif engine_name == "chromium":
            options["args"] = ["--start-maximized"]
        try:
            # Dauerhaftes Profil: die Anmeldung bleibt erhalten, der Nutzer
            # meldet sich einmal von Hand an. Es werden keine Passwoerter im
            # Werkzeug hinterlegt.
            self._context = engine.launch_persistent_context(
                str(self.profile_dir), **options
            )
        except Exception as error:
            self._playwright.stop()
            self._playwright = None
            raise BrowserMissing(self.missing_hint(self.browser, error)) from error
        self.page = (
            self._context.pages[0] if self._context.pages
            else self._context.new_page()
        )
        self.page.goto(url, wait_until="domcontentloaded")
        return self.page

    @staticmethod
    def missing_hint(browser, error):
        """Nennt den konkreten Grund statt der rohen Playwright-Meldung."""
        if browser in ('msedge', 'chrome'):
            names = {'msedge': 'Microsoft Edge', 'chrome': 'Google Chrome'}
            return (
                f"{names[browser]} wurde nicht gefunden. Entweder installieren "
                f"oder im Auswahlfeld einen anderen Browser waehlen.\n\n{error}"
            )
        engine = BROWSER_CHOICES[browser][0]
        return (
            f"Der mitgelieferte Browser fehlt. Einmalig nachinstallieren:\n"
            f"    python -m playwright install {engine}\n\n{error}"
        )

    def close(self):
        try:
            if self._context is not None:
                self._context.close()
        except Exception:
            pass
        try:
            if self._playwright is not None:
                self._playwright.stop()
        except Exception:
            pass
        self._context = None
        self._playwright = None
        self.page = None

    def _first_visible(self, selectors):
        """Sucht den ersten sichtbaren Treffer unter mehreren Selektoren."""
        for selector in selectors:
            try:
                locator = self.page.locator(selector).first
                if locator.count() and locator.is_visible():
                    return locator
            except Exception:
                continue
        return None

    def _by_label(self, labels):
        for label in labels:
            try:
                locator = self.page.get_by_label(label, exact=False).first
                if locator.count() and locator.is_visible():
                    return locator
            except Exception:
                continue
        return None

    def _locate(self, field_name):
        return (
            self._first_visible(FIELD_SELECTORS.get(field_name, ()))
            or self._by_label(FIELD_LABELS.get(field_name, ()))
        )

    def fill(self, data: FormData) -> FillReport:
        """Überträgt die Angaben; abgesendet wird bewusst nichts."""
        if self.page is None:
            raise RuntimeError("Der Browser wurde noch nicht gestartet.")
        report = FillReport()

        for field_name in ("title", "description", "price"):
            value = getattr(data, field_name)
            if not value:
                continue
            locator = self._locate(field_name)
            if locator is None:
                report.add(field_name, False)
                continue
            try:
                locator.fill(str(value))
                report.add(field_name, True)
            except Exception:
                report.add(field_name, False)

        if data.price_type:
            choice = self._first_visible(
                PRICE_TYPE_SELECTORS.get(data.price_type, ())
            )
            if choice is None:
                report.add("price_type", False)
            else:
                try:
                    choice.check()
                    report.add("price_type", True)
                except Exception:
                    report.add("price_type", False)

        existing = [str(path) for path in data.photos if Path(path).is_file()]
        if existing:
            upload = None
            for selector in PHOTO_INPUT_SELECTORS:
                try:
                    candidate = self.page.locator(selector).first
                    if candidate.count():
                        upload = candidate
                        break
                except Exception:
                    continue
            if upload is None:
                report.add("photos", False)
            else:
                try:
                    # Dateiauswahl direkt am Eingabefeld: kein simuliertes
                    # Klicken im Betriebssystem-Dialog noetig.
                    upload.set_input_files(existing)
                    report.add("photos", True)
                except Exception:
                    report.add("photos", False)
        return report


class BrowserSession:
    """Hält Playwright in genau einem Thread.

    Die Sync-API von Playwright bindet ihren Event-Loop an den Thread, der sie
    erzeugt hat. Wird sie aus einem anderen Thread benutzt, bricht die
    Verbindung zum Browser ab - sichtbar als „coroutine ... was never awaited",
    „Task was destroyed but it is pending" und schliesslich EPIPE.

    Deshalb besitzt ein einziger Arbeits-Thread den Browser, und alle Aufrufe
    laufen als Auftraege durch dessen Warteschlange. Die Rueckmeldungen kommen
    aus diesem Thread; die Oberflaeche muss sie selbst in ihren eigenen
    zurueckreichen.
    """

    def __init__(self, profile_dir, browser=DEFAULT_BROWSER, headless=False):
        self.assistant = KleinanzeigenFormAssistant(
            profile_dir, headless=headless, browser=browser
        )
        self._jobs = queue.Queue()
        self._thread = threading.Thread(
            target=self._worker, daemon=True, name="kleinanzeigen-browser"
        )
        self._thread.start()

    def _worker(self):
        while True:
            job = self._jobs.get()
            if job is None:
                break
            action, on_success, on_error = job
            try:
                result = action(self.assistant)
            except Exception as error:
                if on_error is not None:
                    on_error(error)
            else:
                if on_success is not None:
                    on_success(result)
        # Schliessen gehoert in denselben Thread wie das Oeffnen.
        self.assistant.close()

    def submit(self, action, on_success=None, on_error=None):
        """Reiht einen Aufruf ein, der im Browser-Thread ausgeführt wird."""
        self._jobs.put((action, on_success, on_error))

    def shutdown(self):
        """Beendet den Thread und schliesst den Browser geordnet."""
        self._jobs.put(None)

    def is_running(self):
        return self._thread.is_alive()
