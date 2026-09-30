from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from ameli_extract import ExtractionError, extract_pdf
from ameli_extract.cli import main


class SyntheticAmeliPdf(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def make_pdf(
        self,
        path: Path,
        *,
        two_pages: bool = False,
        extra_person: bool = False,
        blank_paid: bool = False,
        settlement: str = "30,00",
    ) -> None:
        document = canvas.Canvas(str(path), pagesize=A4)
        _width, height = A4

        def text(x: float, top: float, value: str, size: int = 8) -> None:
            document.setFont("Helvetica", size)
            document.drawString(x, height - top - size, value)

        def right(x: float, top: float, value: str, size: int = 8) -> None:
            document.setFont("Helvetica", size)
            document.drawRightString(x, height - top - size, value)

        page_count = 2 if two_pages else 1
        for page_number in range(page_count):
            text(42, 104, "Nee le 02/03/1980")
            text(42, 118, "Assuree Camille Dupont")
            text(42, 132, "Numero de securite sociale 280123456789012")
            text(42, 146, "Caisse d'assurance maladie de Lille")
            text(350, 118, "Camille Dupont")
            text(350, 132, "12 rue des Lilas 59000 Lille")
            text(341, 322, "montant")
            text(388, 322, "base")
            text(434, 322, "taux")
            text(464, 322, "montant")
            text(511, 322, "taux")
            text(539, 322, "montant")
            text(42, 333, "dates")
            text(132, 333, "nature des prestations")
            text(356, 333, "paye")
            text(388, 333, "rembours.")
            text(434, 333, "taux")
            text(476, 333, "verse")
            text(503, 333, "comp.")
            text(548, 333, "comp.")
            text(132, 356, "Professionnel : Cabinet medical fictif")
            if extra_person:
                text(132, 372, "Beneficiaire : Alex Exemple")

            first_date = 398
            text(42, first_date, "10/04/2026")
            text(132, first_date, "Consultation medecine generale (PH1)")
            text(132, first_date + 12, "Consultation realisee en cabinet")
            if not blank_paid:
                right(376.5, first_date, "30,00")
            right(422.5, first_date, "30,00")
            right(443, first_date, "70")
            text(445, first_date, "%")
            right(499.5, first_date, "21,00")
            right(520, first_date, "100")
            text(522, first_date, "%")
            right(574.5, first_date, "9,00")

            if not two_pages:
                second_date = 424
                text(42, second_date, "11/04/2026")
                text(132, second_date, "Pharmacie fictive")
                right(376.5, second_date, "0,00")
                right(422.5, second_date, "0,00")
                right(443, second_date, "100")
                text(445, second_date, "%")
                right(499.5, second_date, "0,00")
                right(520, second_date, "100")
                text(522, second_date, "%")
                right(574.5, second_date, "0,00")
            text(42, 475, "Regle le 12/04/2026 au destinataire Cabinet medical fictif")
            text(42, 491, f": {settlement} euro")
            document.showPage()
        document.save()

    def test_core_fields_anonymization_and_decimal_values(self) -> None:
        source = self.root / "confidential-input.pdf"
        self.make_pdf(source)

        result = extract_pdf(source).to_dict()
        payload = json.dumps(result, ensure_ascii=False)

        self.assertNotIn("Camille Dupont", payload)
        self.assertNotIn("280123456789012", payload)
        self.assertNotIn("Lilas", payload)
        self.assertNotIn(str(self.root), payload)
        self.assertEqual(result["beneficiaires"][0]["date_naissance"], "1980-03-02")
        self.assertIn("Lille", json.dumps(result["document"]["cpam"], ensure_ascii=False))
        rows = result["groupes"][0]["prestations"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["montant_paye"], "30.00")
        self.assertEqual(rows[0]["taux_remboursement"], "70.00")
        self.assertEqual(rows[0]["montant_complementaire"], "9.00")
        self.assertEqual(rows[0]["indications"], ["PH1"])
        self.assertIn("Consultation realisee en cabinet", rows[0]["nature"])
        self.assertEqual(rows[1]["montant_verse"], "0.00")
        self.assertEqual(result["validation"]["statut"], "complet")
        self.assertEqual(result["reglements"][0]["montant"], "30.00")
        self.assertEqual(result["groupes"][0]["professionnel"], "Cabinet medical fictif")

    def test_empty_paid_cell_stays_null_and_mismatched_total_needs_review(self) -> None:
        source = self.root / "missing-cell.pdf"
        self.make_pdf(source, blank_paid=True, settlement="31,00")
        result = extract_pdf(source).to_dict()

        self.assertIsNone(result["groupes"][0]["prestations"][0]["montant_paye"])
        self.assertEqual(result["validation"]["statut"], "a_verifier")
        self.assertEqual(result["validation"]["anomalies"][0]["code"], "total_reglement_different_des_prestations")

    def test_multpage_and_multiple_beneficiary_labels(self) -> None:
        source = self.root / "multiple.pdf"
        self.make_pdf(source, two_pages=True, extra_person=True)
        result = extract_pdf(source).to_dict()

        self.assertEqual(result["source"]["nombre_pages"], 2)
        self.assertEqual(len(result["groupes"]), 2)
        self.assertEqual([group["prestations"][0]["page"] for group in result["groupes"]], [1, 2])
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("Alex Exemple", serialized)
        self.assertNotIn("Camille Dupont", serialized)
        self.assertIn("beneficiaire_2", serialized)

    def test_bad_pdf_is_rejected_without_echoing_path(self) -> None:
        source = self.root / "private-name.pdf"
        source.write_text("not a PDF", encoding="utf-8")
        with self.assertRaises(ExtractionError) as error:
            extract_pdf(source)
        self.assertNotIn(str(source), str(error.exception))

    def test_scanned_pdf_is_reported_as_unsupported(self) -> None:
        source = self.root / "scanned.pdf"
        document = canvas.Canvas(str(source), pagesize=A4)
        document.drawImage(str(self._blank_image()), 0, 0, width=200, height=200)
        document.save()
        with self.assertRaisesRegex(ExtractionError, "scans"):
            extract_pdf(source)

    def _blank_image(self) -> Path:
        from PIL import Image

        path = self.root / "page.png"
        Image.new("RGB", (64, 64), "white").save(path)
        return path

    def test_cli_output_conflicts_require_force(self) -> None:
        source = self.root / "input.pdf"
        self.make_pdf(source)
        output = self.root / "result.json"
        output.write_text("old", encoding="utf-8")
        self.assertEqual(main([str(source), "-o", str(output)]), 1)
        self.assertEqual(output.read_text(encoding="utf-8"), "old")
        self.assertEqual(main([str(source), "-o", str(output), "--force"]), 0)
        self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["schema_version"], "1.0")

    def test_cli_folder_continues_after_a_bad_file(self) -> None:
        source_folder = self.root / "input"
        source_folder.mkdir()
        self.make_pdf(source_folder / "first.pdf")
        (source_folder / "second.pdf").write_text("not a PDF", encoding="utf-8")
        destination = self.root / "exports"

        self.assertEqual(main([str(source_folder), "-o", str(destination)]), 1)
        self.assertTrue((destination / "document_001.json").is_file())
        self.assertFalse((destination / "document_002.json").exists())


if __name__ == "__main__":
    unittest.main()
