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


class PlaywrightMissing(RuntimeError):
    """Playwright ist nicht installiert."""


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

    def __init__(self, profile_dir, headless=False):
        self.profile_dir = Path(profile_dir)
        self.headless = headless
        self._playwright = None
        self._context = None
        self.page = None

    def start(self, url: str = POST_AD_URL):
        """Startet den sichtbaren Browser mit dem dauerhaften Profil."""
        sync_playwright = import_playwright()
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = sync_playwright().start()
        # Dauerhaftes Profil: die Anmeldung bleibt erhalten, der Nutzer meldet
        # sich einmal von Hand an. Kein verstecktes Hinterlegen von Passwoertern.
        self._context = self._playwright.chromium.launch_persistent_context(
            str(self.profile_dir),
            headless=self.headless,
            viewport=None,
            args=["--start-maximized"],
        )
        self.page = (
            self._context.pages[0] if self._context.pages
            else self._context.new_page()
        )
        self.page.goto(url, wait_until="domcontentloaded")
        return self.page

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
