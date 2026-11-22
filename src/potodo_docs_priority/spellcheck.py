"""Read spelling findings produced by translation repositories."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import polib

from .priority import normalize_language

EntryKey = tuple[str | None, str, str]


def entry_key(entry: polib.POEntry) -> EntryKey:
    """Identify an entry independently of PO layout and source locations."""
    return entry.msgctxt, entry.msgid, entry.msgid_plural


def translation_digest(entry: polib.POEntry) -> str:
    """Implement the schema-v1 translation fingerprint shared with the producer."""
    value = {
        "msgstr": entry.msgstr,
        "msgstr_plural": {str(key): text for key, text in entry.msgstr_plural.items()},
    }
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CatalogSpelling:
    """Verified findings and freshness for one local catalog."""

    entries: frozenset[EntryKey] = frozenset()
    status: str = "not checked"


@dataclass(frozen=True)
class SpellcheckReport:
    """A complete or explicitly unavailable spelling report."""

    data: dict

    @classmethod
    def load(cls, path: Path, language: str) -> SpellcheckReport:
        """Treat a missing artifact as unavailable, never as a clean check."""
        if not path.is_file():
            return cls({"status": "unavailable", "catalogs": {}})
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema_version") != 1:
            raise ValueError(f"Unsupported spellcheck report schema in {path}")
        if normalize_language(data["language"]) != normalize_language(language):
            raise ValueError(f"Spellcheck report language does not match {language}")
        if data.get("status") not in {"complete", "error"}:
            raise ValueError(f"Invalid spellcheck report status in {path}")
        return cls(data)

    @property
    def url(self) -> str | None:
        """Link only to HTTP(S) reports in generated HTML."""
        url = self.data.get("run_url")
        return (
            url
            if isinstance(url, str) and url.startswith(("https://", "http://"))
            else None
        )

    @property
    def notice(self) -> str:
        """Explain scope and freshness without claiming unchecked entries are clean."""
        if self.data["status"] != "complete":
            return (
                "Spellcheck unavailable or incomplete; spelling readiness is unknown."
            )
        return (
            f"Spellcheck: {self.data['created_at']} (documentation {self.data['docs_version']}). "
            "Only findings matching the current translation affect completion. "
            "Catalogs marked stale or not checked have unverified spelling readiness. "
            "The check covers prose, excluding literal blocks."
        )

    def catalog(self, path: Path, resource: str) -> CatalogSpelling:
        """Keep matching findings after unrelated edits, but mark coverage stale."""
        if self.data["status"] != "complete":
            return CatalogSpelling()
        record = self.data["catalogs"].get(resource)
        if record is None:
            return CatalogSpelling()
        current_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        status = "checked" if current_hash == record.get("checkout_sha256") else "stale"
        local_entries = {
            entry_key(entry): translation_digest(entry)
            for entry in polib.pofile(str(path)).translated_entries()
        }
        matches = set()
        for finding in record["findings"]:
            key = finding["msgctxt"], finding["msgid"], finding["msgid_plural"]
            if local_entries.get(key) == finding["msgstr_sha256"]:
                matches.add(key)
        return CatalogSpelling(frozenset(matches), status)
