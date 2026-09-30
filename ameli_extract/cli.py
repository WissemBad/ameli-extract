"""Command-line interface."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .extract import ExtractionError, extract_pdf


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ameli-extract",
        description="Convert Ameli payment PDFs to anonymized local JSON files.",
    )
    parser.add_argument("input", type=Path, help="PDF file or a folder of PDF files")
    parser.add_argument("-o", "--output", type=Path, required=True, help="JSON path or destination folder")
    parser.add_argument("--force", action="store_true", help="Replace an existing JSON file")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    input_path: Path = args.input
    output_path: Path = args.output
    if input_path.is_dir():
        if output_path.exists() and not output_path.is_dir():
            print("La sortie d'un dossier doit être un dossier.", file=sys.stderr)
            return 2
        try:
            output_path.mkdir(parents=True, exist_ok=True)
        except OSError:
            print("Le dossier de sortie ne peut pas être créé.", file=sys.stderr)
            return 1
        files = sorted(input_path.glob("*.pdf"), key=lambda path: path.name.casefold())
        if not files:
            print("Aucun PDF trouvé dans le dossier.", file=sys.stderr)
            return 1
        failures = 0
        for index, pdf_path in enumerate(files, start=1):
            target = output_path / f"document_{index:03}.json"
            try:
                _write_one(pdf_path, target, args.force)
            except (ExtractionError, OSError, FileExistsError) as exc:
                failures += 1
                print(f"Document {index}: {exc}", file=sys.stderr)
            else:
                print(f"Document {index}: exporté.", file=sys.stderr)
        return 1 if failures else 0
    if not input_path.is_file():
        print("Le PDF d'entrée est introuvable.", file=sys.stderr)
        return 2
    if output_path.exists() and output_path.is_dir():
        print("Pour un PDF, le chemin de sortie doit désigner un fichier JSON.", file=sys.stderr)
        return 2
    try:
        _write_one(input_path, output_path, args.force)
    except ExtractionError as exc:
        print(f"Échec de l'extraction : {exc}", file=sys.stderr)
        return 1
    except FileExistsError as exc:
        print(f"Échec de l'extraction : {exc}", file=sys.stderr)
        return 1
    except OSError:
        print("Le fichier de sortie n'a pas pu être écrit.", file=sys.stderr)
        return 1
    print("Export JSON créé.", file=sys.stderr)
    return 0


def _write_one(source: Path, destination: Path, force: bool) -> None:
    if destination.exists() and not force:
        raise FileExistsError("Le fichier de sortie existe déjà. Ajoutez --force pour le remplacer.")
    try:
        result = extract_pdf(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n"
        temporary = destination.with_name(destination.name + ".tmp")
        try:
            temporary.write_text(serialized, encoding="utf-8")
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
    except (ExtractionError, FileExistsError):
        raise
    except OSError as exc:
        raise ExtractionError("Le fichier ou le dossier de sortie n'a pas pu être écrit.") from exc


if __name__ == "__main__":
    raise SystemExit(main())
