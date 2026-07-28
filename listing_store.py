"""Lokale Produktakte, Plattformprofile und exportierbare Inseratpakete."""

from __future__ import annotations

import functools
import hashlib
import json
import re
import shutil
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PlatformProfile:
    key: str
    label_de: str
    title_limit: int
    description_limit: int


PLATFORM_PROFILES = {
    "kleinanzeigen": PlatformProfile(
        "kleinanzeigen", "Kleinanzeigen", 65, 4000
    ),
    "ebay": PlatformProfile("ebay", "eBay", 80, 4000),
    "ebay_detailed": PlatformProfile(
        "ebay_detailed", "eBay – ausführlich", 80, 500000
    ),
    "ebay_mobile": PlatformProfile(
        "ebay_mobile", "eBay – mobile Kurzvorschau", 80, 800
    ),
}


def synchronized(method):
    """Serialisiert Datenbankzugriffe über die geteilte Verbindung.

    Die Verbindung wird mit ``check_same_thread=False`` auch von den
    Netzwerk-Threads der Oberfläche genutzt. Ohne diese Sperre könnte ein
    ``commit()`` aus einem Thread die offene Transaktion eines anderen
    vorzeitig beenden.
    """

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper


def safe_filename(value: str, fallback: str = "Produkt") -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", str(value))
    value = re.sub(r"\s+", " ", value).strip(" .")
    return value[:120] or fallback


def product_identity(name: str, identifier: str = "") -> str:
    normalized = re.sub(r"\W+", " ", str(name).casefold()).strip()
    seed = f"{identifier.strip()}|{normalized}".encode("utf-8")
    return hashlib.sha256(seed).hexdigest()[:24]


def canonical_fact_key(key: str) -> str:
    normalized = re.sub(
        r"[^a-z0-9]+", " ",
        str(key).casefold()
        .replace("ä", "a").replace("ö", "o")
        .replace("ü", "u").replace("ß", "ss"),
    ).strip()
    aliases = {
        "speicher": "Speicherkapazität",
        "speicherkapazitat": "Speicherkapazität",
        "storage": "Speicherkapazität",
        "storage capacity": "Speicherkapazität",
        "farbe": "Farbe",
        "color": "Farbe",
        "marke": "Marke",
        "brand": "Marke",
        "modell": "Modell",
        "model": "Modell",
        "arbeitsspeicher": "Arbeitsspeicher",
        "ram": "Arbeitsspeicher",
        "isbn 13": "ISBN-13",
        "isbn13": "ISBN-13",
        "ean": "EAN/GTIN",
        "gtin": "EAN/GTIN",
    }
    return aliases.get(normalized, re.sub(r"\s+", " ", str(key)).strip())


class ListingStore:
    """Kleine SQLite-Akte; Bilder werden nur als Dateipfade referenziert."""

    def __init__(self, database_path):
        self.path = Path(database_path)
        # Reentrant: export_package und conflicts rufen andere Methoden auf.
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(
            self.path, timeout=10, check_same_thread=False
        )
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self._create_schema()

    def _create_schema(self):
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS products (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                identifier TEXT NOT NULL DEFAULT '',
                source_url TEXT NOT NULL DEFAULT '',
                state_json TEXT NOT NULL DEFAULT '{}',
                updated_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id TEXT NOT NULL REFERENCES products(id)
                    ON DELETE CASCADE,
                fact_key TEXT NOT NULL,
                value TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT '',
                source_url TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'found',
                observed_at INTEGER NOT NULL,
                UNIQUE(product_id, fact_key, value, source_url)
            );
            CREATE TABLE IF NOT EXISTS drafts (
                product_id TEXT NOT NULL REFERENCES products(id)
                    ON DELETE CASCADE,
                platform TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY(product_id, platform)
            );
            CREATE TABLE IF NOT EXISTS draft_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id TEXT NOT NULL,
                platform TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                version INTEGER NOT NULL,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS prices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id TEXT NOT NULL REFERENCES products(id)
                    ON DELETE CASCADE,
                source TEXT NOT NULL,
                amount REAL NOT NULL,
                currency TEXT NOT NULL DEFAULT 'EUR',
                condition_name TEXT NOT NULL DEFAULT '',
                price_kind TEXT NOT NULL DEFAULT 'active',
                shipping REAL,
                source_url TEXT NOT NULL DEFAULT '',
                observed_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id TEXT NOT NULL REFERENCES products(id)
                    ON DELETE CASCADE,
                path TEXT NOT NULL DEFAULT '',
                source_url TEXT NOT NULL DEFAULT '',
                checksum TEXT NOT NULL DEFAULT '',
                position INTEGER NOT NULL DEFAULT 0,
                is_own INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS cache (
                cache_key TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                expires_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_facts_product_key
                ON facts(product_id, fact_key);
            CREATE INDEX IF NOT EXISTS idx_prices_product
                ON prices(product_id, price_kind);
            """
        )
        self.connection.commit()

    @synchronized
    def close(self):
        self.connection.close()

    @synchronized
    def upsert_product(
        self, name: str, identifier: str = "", source_url: str = "",
        state: dict | None = None
    ) -> str:
        product_id = product_identity(name, identifier)
        now = int(time.time())
        existing = self.connection.execute(
            "SELECT state_json FROM products WHERE id=?", (product_id,)
        ).fetchone()
        state_json = (
            existing["state_json"]
            if state is None and existing else
            json.dumps(state or {}, ensure_ascii=False)
        )
        self.connection.execute(
            """
            INSERT INTO products(
                id, name, identifier, source_url, state_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,
                identifier=CASE WHEN excluded.identifier != ''
                    THEN excluded.identifier ELSE products.identifier END,
                source_url=CASE WHEN excluded.source_url != ''
                    THEN excluded.source_url ELSE products.source_url END,
                state_json=excluded.state_json,
                updated_at=excluded.updated_at
            """,
            (
                product_id, name, identifier, source_url,
                state_json, now,
            ),
        )
        self.connection.commit()
        return product_id

    @synchronized
    def product_state(self, product_id: str) -> dict:
        row = self.connection.execute(
            "SELECT state_json FROM products WHERE id=?", (product_id,)
        ).fetchone()
        if not row:
            return {}
        try:
            return json.loads(row["state_json"])
        except (TypeError, json.JSONDecodeError):
            return {}

    @synchronized
    def update_product_state(self, product_id: str, state: dict):
        self.connection.execute(
            "UPDATE products SET state_json=?, updated_at=? WHERE id=?",
            (
                json.dumps(state, ensure_ascii=False),
                int(time.time()), product_id,
            ),
        )
        self.connection.commit()

    @synchronized
    def add_fact(
        self, product_id: str, key: str, value: str, source: str = "",
        source_url: str = "", status: str = "found"
    ):
        key, value = canonical_fact_key(key), str(value).strip()
        if not key or not value:
            return
        self.connection.execute(
            """
            INSERT INTO facts(
                product_id, fact_key, value, source, source_url,
                status, observed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(product_id, fact_key, value, source_url)
            DO UPDATE SET source=excluded.source,
                status=CASE
                    WHEN facts.status='confirmed' THEN facts.status
                    ELSE excluded.status
                END,
                observed_at=excluded.observed_at
            """,
            (
                product_id, key, value, source, source_url,
                status, int(time.time()),
            ),
        )
        self.connection.commit()

    @synchronized
    def confirm_fact(self, product_id: str, key: str, value: str):
        with self.connection:
            self.connection.execute(
                """
                UPDATE facts SET status='rejected'
                WHERE product_id=? AND fact_key=? AND value<>?
                """,
                (product_id, key, value),
            )
            self.connection.execute(
                """
                UPDATE facts SET status='confirmed'
                WHERE product_id=? AND fact_key=? AND value=?
                """,
                (product_id, key, value),
            )

    @synchronized
    def facts(self, product_id: str) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT fact_key, value, source, source_url, status, observed_at
            FROM facts WHERE product_id=?
            ORDER BY fact_key COLLATE NOCASE,
                CASE status WHEN 'confirmed' THEN 0
                    WHEN 'found' THEN 1 ELSE 2 END,
                observed_at DESC
            """,
            (product_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    @synchronized
    def conflicts(self, product_id: str) -> dict[str, list[dict]]:
        grouped = {}
        for fact in self.facts(product_id):
            if fact["status"] == "rejected":
                continue
            grouped.setdefault(fact["fact_key"], []).append(fact)
        return {
            key: values for key, values in grouped.items()
            if len({entry["value"].casefold() for entry in values}) > 1
            and not any(entry["status"] == "confirmed" for entry in values)
        }

    @synchronized
    def confirmed_values(self, product_id: str) -> dict[str, str]:
        result = {}
        grouped = {}
        for fact in self.facts(product_id):
            if fact["status"] != "rejected":
                grouped.setdefault(fact["fact_key"], []).append(fact)
        for key, values in grouped.items():
            confirmed = [
                entry for entry in values if entry["status"] == "confirmed"
            ]
            unique = {entry["value"].casefold() for entry in values}
            if confirmed:
                result[key] = confirmed[0]["value"]
            elif len(unique) == 1:
                result[key] = values[0]["value"]
        return result

    @synchronized
    def save_draft(
        self, product_id: str, platform: str, title: str, description: str
    ):
        previous = self.connection.execute(
            "SELECT * FROM drafts WHERE product_id=? AND platform=?",
            (product_id, platform),
        ).fetchone()
        version = (previous["version"] + 1) if previous else 1
        now = int(time.time())
        with self.connection:
            if previous and (
                previous["title"] != title
                or previous["description"] != description
            ):
                self.connection.execute(
                    """
                    INSERT INTO draft_versions(
                        product_id, platform, title, description,
                        version, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        product_id, platform, previous["title"],
                        previous["description"], previous["version"], now,
                    ),
                )
            self.connection.execute(
                """
                INSERT INTO drafts(
                    product_id, platform, title, description,
                    version, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(product_id, platform) DO UPDATE SET
                    title=excluded.title,
                    description=excluded.description,
                    version=excluded.version,
                    updated_at=excluded.updated_at
                """,
                (
                    product_id, platform, title, description, version, now
                ),
            )

    @synchronized
    def load_drafts(self, product_id: str) -> dict[str, dict]:
        rows = self.connection.execute(
            "SELECT * FROM drafts WHERE product_id=?", (product_id,)
        ).fetchall()
        return {
            row["platform"]: {
                "title": row["title"],
                "description": row["description"],
                "version": row["version"],
            }
            for row in rows
        }

    @synchronized
    def add_price(
        self, product_id: str, source: str, amount: float,
        condition: str = "", kind: str = "active", shipping=None,
        source_url: str = ""
    ):
        now = int(time.time())
        duplicate = self.connection.execute(
            """
            SELECT 1 FROM prices
            WHERE product_id=? AND source=? AND amount=?
                AND price_kind=? AND source_url=?
                AND observed_at>=?
            LIMIT 1
            """,
            (
                product_id, source, float(amount), kind,
                source_url, now - 6 * 60 * 60,
            ),
        ).fetchone()
        if duplicate:
            return
        self.connection.execute(
            """
            INSERT INTO prices(
                product_id, source, amount, condition_name, price_kind,
                shipping, source_url, observed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                product_id, source, float(amount), condition, kind,
                shipping, source_url, now,
            ),
        )
        self.connection.commit()

    @synchronized
    def price_summary(self, product_id: str, kind="active") -> dict:
        amounts = [
            float(row[0]) for row in self.connection.execute(
                """
                SELECT amount + COALESCE(shipping, 0)
                FROM prices WHERE product_id=? AND price_kind=?
                ORDER BY amount + COALESCE(shipping, 0)
                """,
                (product_id, kind),
            ).fetchall()
        ]
        if not amounts:
            return {"count": 0, "kind": kind}
        middle = len(amounts) // 2
        median = (
            amounts[middle] if len(amounts) % 2
            else (amounts[middle - 1] + amounts[middle]) / 2
        )
        return {
            "count": len(amounts),
            "kind": kind,
            "minimum": min(amounts),
            "maximum": max(amounts),
            "median": median,
        }

    @synchronized
    def cache_put(self, key: str, payload, ttl_seconds: int):
        now = int(time.time())
        self.connection.execute(
            """
            INSERT INTO cache(cache_key, payload_json, expires_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                payload_json=excluded.payload_json,
                expires_at=excluded.expires_at,
                updated_at=excluded.updated_at
            """,
            (
                key, json.dumps(payload, ensure_ascii=False),
                now + max(1, int(ttl_seconds)), now,
            ),
        )
        self.connection.commit()

    @synchronized
    def cache_delete(self, key: str):
        self.connection.execute(
            "DELETE FROM cache WHERE cache_key=?", (key,)
        )
        self.connection.commit()

    @synchronized
    def cache_get(self, key: str):
        row = self.connection.execute(
            "SELECT payload_json, expires_at FROM cache WHERE cache_key=?",
            (key,),
        ).fetchone()
        if not row or row["expires_at"] < int(time.time()):
            if row:
                self.connection.execute(
                    "DELETE FROM cache WHERE cache_key=?", (key,)
                )
                self.connection.commit()
            return None
        return json.loads(row["payload_json"])

    @synchronized
    def add_image(
        self, product_id: str, path, source_url: str = "", is_own: bool = True
    ) -> int:
        """Merkt sich ein Bild als Dateipfad; Inhalte bleiben ausserhalb."""
        file_path = Path(path)
        checksum = ""
        if file_path.is_file():
            digest = hashlib.sha256()
            with open(file_path, "rb") as handle:
                for chunk in iter(lambda: handle.read(1 << 20), b""):
                    digest.update(chunk)
            checksum = digest.hexdigest()
        existing = self.connection.execute(
            """
            SELECT id FROM images
            WHERE product_id=? AND (path=? OR (checksum<>'' AND checksum=?))
            """,
            (product_id, str(file_path), checksum),
        ).fetchone()
        if existing:
            return int(existing["id"])
        position = self.connection.execute(
            "SELECT COALESCE(MAX(position), 0) + 1 FROM images WHERE product_id=?",
            (product_id,),
        ).fetchone()[0]
        cursor = self.connection.execute(
            """
            INSERT INTO images(
                product_id, path, source_url, checksum, position, is_own
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                product_id, str(file_path), source_url, checksum,
                position, 1 if is_own else 0,
            ),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    @synchronized
    def images(self, product_id: str, own_only: bool = False) -> list[dict]:
        rows = self.connection.execute(
            f"""
            SELECT id, path, source_url, checksum, position, is_own
            FROM images WHERE product_id=?
            {'AND is_own=1' if own_only else ''}
            ORDER BY position, id
            """,
            (product_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    @synchronized
    def remove_image(self, image_id: int):
        self.connection.execute("DELETE FROM images WHERE id=?", (image_id,))
        self.connection.commit()

    @synchronized
    def reorder_images(self, image_ids):
        """Schreibt die Anzeigereihenfolge; das erste Bild ist das Hauptbild."""
        with self.connection:
            for position, image_id in enumerate(image_ids, 1):
                self.connection.execute(
                    "UPDATE images SET position=? WHERE id=?",
                    (position, int(image_id)),
                )

    @synchronized
    def export_package(
        self, product_id: str, output_root, images=(), prepare=None
    ) -> Path:
        """Schreibt Texte, Nachweise und Bilder in einen Produktordner.

        ``prepare(source, target)`` bereitet eigene Fotos auf; ohne Angabe
        werden sie unveraendert kopiert.
        """
        product = self.connection.execute(
            "SELECT * FROM products WHERE id=?", (product_id,)
        ).fetchone()
        if not product:
            raise KeyError(product_id)
        folder = Path(output_root) / safe_filename(product["name"])
        folder.mkdir(parents=True, exist_ok=True)
        drafts = self.load_drafts(product_id)
        mapping = {
            "kleinanzeigen": "beitrag-kleinanzeigen.txt",
            "ebay": "beitrag-ebay.txt",
            "ebay_detailed": "beitrag-ebay-ausfuehrlich.txt",
            "ebay_mobile": "beitrag-ebay-mobil.txt",
        }
        for platform, filename in mapping.items():
            draft = drafts.get(platform)
            if draft:
                (folder / filename).write_text(
                    f"{draft['title']}\n\n{draft['description'].rstrip()}\n",
                    encoding="utf-8",
                )
        internal = folder / ".creationtool"
        internal.mkdir(exist_ok=True)
        metadata = {
            "product": dict(product),
            "facts": self.facts(product_id),
            "conflicts": self.conflicts(product_id),
            "drafts": drafts,
            "price_active": self.price_summary(product_id, "active"),
            "price_sold": self.price_summary(product_id, "sold"),
        }
        (internal / "produktdaten.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        sources = sorted({
            fact["source_url"] for fact in metadata["facts"]
            if fact["source_url"]
        })
        (internal / "quellen.txt").write_text(
            "\n".join(sources) + ("\n" if sources else ""),
            encoding="utf-8",
        )
        for index, image_path in enumerate(images, 1):
            source = Path(image_path)
            if not source.is_file():
                continue
            suffix = source.suffix.lower()
            if prepare is not None and suffix not in ('.gif',):
                # Aufbereitete Fotos werden als JPEG abgelegt; animierte GIFs
                # blieben dabei auf der Strecke und werden nur kopiert.
                suffix = '.jpg' if suffix not in ('.png', '.webp') else suffix
            target = folder / (
                f"{index:02d}-hauptbild{suffix}" if index == 1
                else f"{index:02d}-produktbild{suffix}"
            )
            if source.resolve() == target.resolve():
                continue
            if prepare is None:
                shutil.copy2(source, target)
            else:
                prepare(source, target)
        return folder
