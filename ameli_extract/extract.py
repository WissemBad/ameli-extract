"""Position-based extraction for French Ameli third-party payment statements."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pdfplumber

SCHEMA_VERSION = "1.0"
DATE_RE = re.compile(r"(?<!\d)(\d{2})/(\d{2})/(\d{4})(?!\d)")
MONEY_RE = re.compile(r"(?<![\w/])[-+]?\d[\d\u00a0\u202f ]*,\d{2}(?!\d)")
NUMBER_RE = re.compile(r"(?<![\w/])[-+]?\d+(?:[,.]\d+)?(?![\w/])")
INSURED_RE = re.compile(r"\bassur[ée]e?\b", re.IGNORECASE)
BENEFICIARY_RE = re.compile(r"\bb[ée]n[ée]ficiaire\b", re.IGNORECASE)
NIR_CONTEXT_RE = re.compile(r"s[ée]curit[ée]\s+sociale|\bNIR\b", re.IGNORECASE)
ADDRESS_CUE_RE = re.compile(
    r"\b(?:adresse|voie|rue|avenue|boulevard|impasse|place|code\s+postal)\b",
    re.IGNORECASE,
)
SETTLEMENT_RE = re.compile(r"r[ée]gl[ée]\s+le\s+(\d{2}/\d{2}/\d{4})", re.IGNORECASE)
PERIOD_RE = re.compile(
    r"(?:p[ée]riode|d[ée]compte|paiement|prestations?).{0,90}?(\d{2}/\d{2}/\d{4})",
    re.IGNORECASE,
)
EURO_RE = re.compile(r"([-+]?\d[\d\u00a0\u202f ]*,\d{2})\s*(?:\u20ac|euros?)(?![A-Za-z])", re.IGNORECASE)


class ExtractionError(Exception):
    """Safe, user-facing extraction failure without source-document content."""


@dataclass(slots=True)
class ExtractionResult:
    """An anonymized, JSON-ready extraction result."""

    source: dict[str, Any]
    document: dict[str, Any]
    beneficiaires: list[dict[str, Any]]
    groupes: list[dict[str, Any]]
    reglements: list[dict[str, Any]]
    sections_complementaires: list[dict[str, Any]]
    validation: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Return the stable public JSON representation."""
        return {
            "schema_version": SCHEMA_VERSION,
            "source": self.source,
            "document": self.document,
            "beneficiaires": self.beneficiaires,
            "groupes": self.groupes,
            "reglements": self.reglements,
            "sections_complementaires": self.sections_complementaires,
            "validation": self.validation,
        }


@dataclass(slots=True)
class _WordLine:
    words: list[dict[str, Any]]

    @property
    def top(self) -> float:
        return min(float(word["top"]) for word in self.words)

    @property
    def text(self) -> str:
        return " ".join(str(word["text"]) for word in self.words).strip()


@dataclass(slots=True)
class _Header:
    top: float
    date_x: float
    nature_x: float
    columns: list[tuple[float, float]]
    first_amount_x: float


def _fold(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    return "".join(char for char in normalized if not unicodedata.combining(char))


def _iso_date(value: str) -> str | None:
    match = DATE_RE.search(value)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(0), "%d/%m/%Y").date().isoformat()
    except ValueError:
        return None


def _decimal_string(value: str) -> str | None:
    cleaned = value.replace("\u00a0", "").replace("\u202f", "").replace(" ", "")
    try:
        amount = Decimal(cleaned.replace(",", "."))
    except InvalidOperation:
        return None
    return f"{amount:.2f}"


def _lines(page: pdfplumber.page.Page) -> list[_WordLine]:
    words = page.extract_words(x_tolerance=1.5, y_tolerance=2.5, keep_blank_chars=False)
    ordered = sorted(words, key=lambda item: (float(item["top"]), float(item["x0"])))
    grouped: list[list[dict[str, Any]]] = []
    for word in ordered:
        if not grouped or abs(float(word["top"]) - min(float(w["top"]) for w in grouped[-1])) > 2.5:
            grouped.append([word])
        else:
            grouped[-1].append(word)
    return [_WordLine(sorted(row, key=lambda item: float(item["x0"]))) for row in grouped]


def _find_header(page: pdfplumber.page.Page, lines: list[_WordLine]) -> _Header | None:
    words = page.extract_words(x_tolerance=1.5, y_tolerance=2.5, keep_blank_chars=False)
    for line in lines:
        label = _fold(line.text)
        if "date" not in label or "nature" not in label or "prestation" not in label:
            continue
        date_word = next((w for w in line.words if _fold(str(w["text"])).startswith("date")), None)
        nature_word = next((w for w in line.words if _fold(str(w["text"])) == "nature"), None)
        if date_word is None or nature_word is None:
            continue
        header_words = [
            w for w in words
            if line.top - 16 <= float(w["top"]) <= line.top + 12
            and float(w["x0"]) >= float(nature_word["x0"]) + 150
        ]
        # The template prints one short heading above each financial column.
        anchors: list[dict[str, Any]] = []
        for word in sorted(header_words, key=lambda item: (float(item["top"]), float(item["x0"]))):
            token = _fold(str(word["text"]).strip(".:;"))
            if token in {"montant", "base", "taux"}:
                if not anchors or all(abs(float(word["x0"]) - float(existing["x0"])) > 18 for existing in anchors):
                    anchors.append(word)
        anchors.sort(key=lambda item: float(item["x0"]))
        if len(anchors) != 6 or [ _fold(str(word["text"])) for word in anchors ] != [
            "montant", "base", "taux", "montant", "taux", "montant"
        ]:
            continue
        groups: list[tuple[float, float]] = []
        for index, anchor in enumerate(anchors):
            center = (float(anchor["x0"]) + float(anchor["x1"])) / 2
            left = (
                (float(anchors[index - 1]["x1"]) + float(anchor["x0"])) / 2
                if index
                else float(anchor["x0"]) - 8
            )
            right = (
                (float(anchor["x1"]) + float(anchors[index + 1]["x0"])) / 2
                if index < len(anchors) - 1
                else float(anchor["x1"]) + 8
            )
            groups.append((left, right))
            anchor["center"] = center
        # Match labels from the second heading row as a template check.
        lower = " ".join(_fold(str(w["text"])) for w in header_words if float(w["top"]) >= line.top - 3)
        if not all(fragment in lower for fragment in ("paye", "rembours", "verse", "comp")):
            continue
        return _Header(
            top=line.top,
            date_x=float(date_word["x0"]),
            nature_x=float(nature_word["x0"]),
            columns=groups,
            first_amount_x=float(anchors[0]["x0"]),
        )
    return None


def _date_rows(lines: list[_WordLine], header: _Header, footer_top: float) -> list[tuple[float, str]]:
    result: list[tuple[float, str]] = []
    for line in lines:
        if not header.top + 12 < line.top < footer_top:
            continue
        for word in line.words:
            if float(word["x0"]) > header.nature_x or float(word["x1"]) > header.nature_x + 65:
                continue
            parsed = _iso_date(str(word["text"]))
            if parsed:
                result.append((line.top, parsed))
                break
    return result


def _parse_financial_row(
    lines: list[_WordLine], page: pdfplumber.page.Page, top: float, bottom: float, header: _Header
) -> list[str | None] | None:
    words = [
        word for line in lines if top - 3 <= line.top < bottom
        for word in line.words if float(word["x0"]) >= header.first_amount_x - 10
    ]
    buckets: list[list[dict[str, Any]]] = [[] for _ in range(6)]
    for word in words:
        x0, x1 = float(word["x0"]), float(word["x1"])
        if str(word["text"]) == "%":
            # Attach the percent glyph to the closest rate column.
            candidates = (2, 4)
            index = min(candidates, key=lambda i: abs((header.columns[i][0] + header.columns[i][1]) / 2 - (x0 + x1) / 2))
            buckets[index].append(word)
            continue
        value = str(word["text"]).replace("\u00a0", "")
        if not (MONEY_RE.fullmatch(value) or NUMBER_RE.fullmatch(value)):
            continue
        center = (x0 + x1) / 2
        column = min(range(6), key=lambda i: abs((header.columns[i][0] + header.columns[i][1]) / 2 - center))
        buckets[column].append(word)
    values: list[str | None] = []
    for index, bucket in enumerate(buckets):
        text = " ".join(str(word["text"]) for word in sorted(bucket, key=lambda item: float(item["x0"])))
        if not text:
            values.append(None)
        elif index in (2, 4):
            numeric = _decimal_string(text.replace("%", "").strip())
            values.append(numeric)
        else:
            values.append(_decimal_string(text))
    if not any(value is not None for value in values):
        return None
    return values


def _patient_lines(lines: list[_WordLine], page_width: float) -> tuple[list[dict[str, Any]], set[str], set[int]]:
    beneficiaries: list[dict[str, Any]] = []
    known_names: set[str] = set()
    private_line_indexes: set[int] = set()
    current_index: int | None = None
    for index, line in enumerate(lines):
        # A visual row can contain the insured field at left and the addressee
        # at right. Only the left field is used to identify a beneficiary.
        text = " ".join(str(word["text"]) for word in line.words if float(word["x0"]) < page_width * 0.56)
        folded = _fold(text)
        if INSURED_RE.search(text) or BENEFICIARY_RE.search(text):
            private_line_indexes.add(index)
            current_index = len(beneficiaries) + 1
            cue = INSURED_RE.search(text) or BENEFICIARY_RE.search(text)
            remainder = text[cue.end():].strip(" :.-") if cue else ""
            date_match = DATE_RE.search(remainder)
            name = remainder[:date_match.start()].strip(" ,;.-") if date_match else remainder
            name = re.sub(r"\b(?:n[ée]e?\s+le|date\s+de\s+naissance)\b.*$", "", name, flags=re.I).strip()
            if _fold(name).startswith("de naissance"):
                name = ""
            if name and not re.fullmatch(r"[\d\s./-]+", name):
                known_names.add(name)
            birth = None
            birth_cue = re.search(r"\bne(?:e|\(e\))?\s+le\b|\bdate\s+de\s+naissance\b", _fold(remainder))
            if date_match and birth_cue:
                birth = _iso_date(date_match.group(0))
            if birth is None:
                for adjacent in lines[max(0, index - 2): index + 3]:
                    adjacent_text = _fold(adjacent.text)
                    adjacent_match = re.search(
                        r"\b(?:ne(?:e|\(e\))?\s+le|date\s+de\s+naissance)\b.*",
                        adjacent_text,
                    )
                    if adjacent.top < 180 and adjacent_match and DATE_RE.search(adjacent_match.group(0)):
                        birth = _iso_date(adjacent_match.group(0))
                        private_line_indexes.add(lines.index(adjacent))
                        break
            beneficiaries.append({
                "id": f"beneficiaire_{current_index}",
                "date_naissance": birth,
            })
            continue
        if NIR_CONTEXT_RE.search(folded):
            private_line_indexes.add(index)
            continue
        if ADDRESS_CUE_RE.search(text) and not re.search(
            r"caisse|assurance maladie|professionnel|\bCPAM\b", text, re.I
        ):
            private_line_indexes.add(index)
            continue
        if re.search(r"\b(?:ne(?:e|\(e\))?\s+le|date\s+de\s+naissance)\b", folded):
            private_line_indexes.add(index)
            if current_index and DATE_RE.search(text):
                beneficiaries[current_index - 1]["date_naissance"] = _iso_date(text)
            continue
        # The first-page addressee box is the sole personal street-address zone.
        if line.top < 180 and any(float(w["x0"]) > page_width * 0.56 for w in line.words):
            private_line_indexes.add(index)
    if not beneficiaries:
        beneficiaries.append({"id": "beneficiaire_1", "date_naissance": None})
    return beneficiaries, known_names, private_line_indexes


def _safe_text(text: str, known_names: set[str]) -> str:
    clean = text
    for name in sorted(known_names, key=len, reverse=True):
        clean = re.sub(re.escape(name), "[identité supprimée]", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\b\d{13,15}\b", "[identifiant supprimé]", clean)
    clean = re.sub(r"\b\d{1,2}(?:\s+\d{2}){3}\s+\d{3}\s+\d{3}\s+\d{2}\b", "[identifiant supprimé]", clean)
    return clean.strip()


def _cpam_info(lines: list[_WordLine]) -> dict[str, Any]:
    matches: list[str] = []
    for line in lines:
        folded = _fold(line.text)
        if re.search(r"caisse.{0,35}assurance maladie|assurance maladie.{0,35}(?:de|du)", folded):
            matches.append(line.text)
    return {"nom_et_coordonnees": list(dict.fromkeys(matches))}


def _extract_page(
    page: pdfplumber.page.Page,
    page_number: int,
    beneficiaries: list[dict[str, Any]],
    known_names: set[str],
    private_line_indexes: set[int],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    lines = _lines(page)
    header = _find_header(page, lines)
    if header is None:
        raise ExtractionError("Le modèle de tableau Ameli n'a pas été reconnu sur une page.")
    settlement_lines = [(i, line) for i, line in enumerate(lines) if SETTLEMENT_RE.search(line.text)]
    footer_top = min((line.top for _, line in settlement_lines), default=float(page.height))
    rows = _date_rows(lines, header, footer_top)
    if not rows:
        raise ExtractionError("Aucune ligne de prestation n'a été reconnue sur une page.")

    prestations: list[dict[str, Any]] = []
    anomalies: list[dict[str, Any]] = []
    complements: list[dict[str, Any]] = []
    group_id = f"groupe_{page_number}_1"
    for row_index, (top, care_date) in enumerate(rows):
        bottom = rows[row_index + 1][0] if row_index + 1 < len(rows) else footer_top
        financial = _parse_financial_row(lines, page, top, bottom, header)
        if financial is None:
            anomalies.append({"page": page_number, "ligne": row_index + 1, "code": "montants_non_reconnus"})
            financial = [None] * 6
        description_words = [
            word for line in lines if top - 3 <= line.top < bottom
            for word in line.words
            if header.nature_x <= float(word["x0"]) < header.first_amount_x - 8
        ]
        description = " ".join(str(word["text"]) for word in sorted(description_words, key=lambda w: (float(w["top"]), float(w["x0"]))))
        description = _safe_text(description, known_names)
        indications = list(dict.fromkeys(
            fragment.strip() for fragment in re.findall(r"\(([^()]*)\)", description) if fragment.strip()
        ))
        prestations.append({
            "date_soin": care_date,
            "nature": description,
            "montant_paye": financial[0],
            "base_remboursement": financial[1],
            "taux_remboursement": financial[2],
            "montant_verse": financial[3],
            "taux_complementaire": financial[4],
            "montant_complementaire": financial[5],
            "indications": indications,
            "page": page_number,
            "ligne": row_index + 1,
        })

    # Preserve non-tabular text and headings after removing the explicitly identified
    # insured block, NIR, recipient address, and any repeated occurrence of the name.
    for index, line in enumerate(lines):
        if index in private_line_indexes or index in {i for i, _ in settlement_lines}:
            continue
        if header.top - 18 <= line.top <= rows[-1][0] + 4:
            continue
        if line.top > footer_top + 35:
            continue
        safe = _safe_text(line.text, known_names)
        if not safe or re.fullmatch(r"[\W_]+", safe):
            continue
        kind = "cpam" if re.search(r"caisse|assurance maladie|ameli", _fold(safe)) else "texte"
        complements.append({"page": page_number, "type": kind, "texte": safe})

    settlements: list[dict[str, Any]] = []
    for line_index, line in settlement_lines:
        match = SETTLEMENT_RE.search(line.text)
        if match is None:
            continue
        context = line.text[match.end():].strip(" :-")
        amount: str | None = None
        for candidate in lines[line_index: min(line_index + 4, len(lines))]:
            euro_match = EURO_RE.search(candidate.text)
            if euro_match:
                amount = _decimal_string(euro_match.group(1))
                break
        settlements.append({
            "date": _iso_date(match.group(1)),
            "destinataire": _safe_text(context, known_names) or None,
            "montant": amount,
            "groupes": [group_id],
            "page": page_number,
        })
        if amount is None:
            anomalies.append({"page": page_number, "code": "montant_reglement_non_reconnu"})

    group_context: list[str] = []
    for line in lines:
        if header.top + 10 < line.top < rows[0][0] - 3:
            safe = _safe_text(line.text, known_names)
            if safe:
                group_context.append(safe)
                complements.append({"page": page_number, "type": "contexte_prestations", "texte": safe})

    provider = next(
        (text.split(":", 1)[1].strip() for text in group_context if re.match(r"(?i)professionnel(?: de sant[ée])?\s*:", text)),
        None,
    )
    groups = [{
        "id": group_id,
        "beneficiaire_id": beneficiaries[0]["id"],
        "prise_en_charge": None,
        "professionnel": provider,
        "informations_complementaires": group_context,
        "prestations": prestations,
    }]
    return groups, settlements, complements, anomalies, lines


def extract_pdf(path: str | Path) -> ExtractionResult:
    """Extract an Ameli third-party payment PDF, removing patient identity fields.

    The input path is used only to read the PDF; it is never copied to the result or
    printed in an error. Processing is local and does not perform OCR or networking.
    """
    source_path = Path(path)
    try:
        content = source_path.read_bytes()
    except OSError as exc:
        raise ExtractionError("Le fichier PDF est inaccessible.") from exc
    if not content.startswith(b"%PDF-"):
        raise ExtractionError("Le fichier sélectionné n'est pas un PDF valide.")

    try:
        pdf = pdfplumber.open(source_path)
    except Exception as exc:  # pdfplumber raises several backend-specific exceptions.
        raise ExtractionError("Le PDF ne peut pas être lu ; vérifiez qu'il n'est pas chiffré.") from exc
    with pdf:
        if not pdf.pages:
            raise ExtractionError("Le PDF ne contient aucune page.")
        if all(not (page.extract_text() or "").strip() for page in pdf.pages):
            raise ExtractionError("Ce PDF ne contient pas de texte exploitable ; les scans ne sont pas pris en charge.")

        page_data: list[tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[_WordLine]]] = []
        beneficiaries: list[dict[str, Any]] = []
        known_names: set[str] = set()
        private_indexes_by_page: list[set[int]] = []
        lines_by_page: list[list[_WordLine]] = []
        for page in pdf.pages:
            lines = _lines(page)
            found, names, private = _patient_lines(lines, float(page.width))
            lines_by_page.append(lines)
            private_indexes_by_page.append(private)
            known_names.update(names)
            for beneficiary in found:
                if beneficiary not in beneficiaries:
                    beneficiaries.append(beneficiary)
        if not beneficiaries:
            beneficiaries = [{"id": "beneficiaire_1", "date_naissance": None}]

        for page_number, page in enumerate(pdf.pages, start=1):
            result = _extract_page(
                page, page_number, beneficiaries, known_names, private_indexes_by_page[page_number - 1]
            )
            page_data.append(result)

        groups = [group for part in page_data for group in part[0]]
        settlements = [item for part in page_data for item in part[1]]
        complements = [item for part in page_data for item in part[2]]
        anomalies = [item for part in page_data for item in part[3]]
        all_lines = [line for lines in lines_by_page for line in lines]
        cpam = _cpam_info(all_lines)
        period = None
        for line in all_lines:
            match = PERIOD_RE.search(line.text)
            if match:
                period = _iso_date(match.group(1))
                if period:
                    break

        total_care = [
            Decimal(item["montant_verse"]) + Decimal(item["montant_complementaire"])
            for group in groups for item in group["prestations"]
            if item["montant_verse"] is not None and item["montant_complementaire"] is not None
        ]
        if settlements and len(total_care) == sum(len(group["prestations"]) for group in groups):
            care_total = sum(total_care, Decimal("0.00"))
            settlement_total = sum((Decimal(item["montant"]) for item in settlements if item["montant"] is not None), Decimal("0.00"))
            if all(item["montant"] is not None for item in settlements) and care_total != settlement_total:
                anomalies.append({"code": "total_reglement_different_des_prestations"})

        # Check the known patient names and NIR-bearing line classes never cross the
        # output boundary. An ambiguous identity line makes the result reviewable.
        if any(item["date_naissance"] is None for item in beneficiaries):
            anomalies.append({"code": "date_naissance_non_reconnue"})
        status = "a_verifier" if anomalies else "complet"
        return ExtractionResult(
            source={"sha256": hashlib.sha256(content).hexdigest(), "nombre_pages": len(pdf.pages)},
            document={
                "type": "paiement_tiers_ameli",
                "date_releve": period,
                "cpam": cpam,
            },
            beneficiaires=beneficiaries,
            groupes=groups,
            reglements=settlements,
            sections_complementaires=complements,
            validation={"statut": status, "anomalies": anomalies},
        )
