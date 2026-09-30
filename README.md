# AmeliExtract

AmeliExtract converts text-based Ameli third-party payment statements to JSON on your computer. It keeps care rows, amounts, rates, treatment notes, professionals, providers, CPAM information and settlements while removing the insured person's name, NIR and postal address. The beneficiary's date of birth is retained. The tool does not upload files or run OCR.

## Install

Use Python 3.11 or later:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

For development and synthetic PDF tests, install `python -m pip install -e ".[dev]"`.

## Use

Convert one PDF to an explicitly chosen JSON file:

```powershell
ameli-extract .\statement.pdf -o .\statement.json
```

Convert all PDFs immediately inside a folder. Exports receive neutral numbered names and do not copy the original PDF filename:

```powershell
ameli-extract .\statements -o .\exports
```

Existing JSON files are left in place unless `--force` is supplied. A folder run continues after an unreadable or unrecognized file and exits with status 1 when any file needs attention. A successful run exits 0; command or path errors exit 2. Error messages and progress output never include extracted document contents.

The Python API is:

```python
from ameli_extract import extract_pdf

result = extract_pdf("statement.pdf")
structured_data = result.to_dict()
```

`result.to_dict()` includes `schema_version`, `source` (SHA-256 and page count only), `document`, `beneficiaires`, `groupes`, `reglements`, `sections_complementaires` and `validation`. Each care row has its date, description, the six printed amount/rate values, indications and source page/line. Dates use ISO format. Money is a decimal string such as `"12.50"`; absent printed values are `null`. See [`examples/sample.json`](examples/sample.json) for a fully fictional result.

Use neutral beneficiary IDs to associate care without exporting names. The original file path and name are not written into the JSON. The SHA-256 digest helps distinguish duplicate files; treat it and every exported JSON as private.

## Privacy and supported documents

The first version supports text-based PDFs with the Ameli payment table layout. It rejects scans and unknown layouts rather than guessing. PDF files, exported JSON, names, NIRs, addresses and real healthcare data must never be committed. Tests and examples use invented identities and amounts only.

The extraction preserves non-identity text where the layout lets the program identify the insured-person block and addressee address. Review `validation.statut` and `validation.anomalies` before relying on a result. `a_verifier` means a field, total or identity detail could not be safely confirmed. A date of birth and health information remain sensitive even when direct identifiers have been removed.

## Development

```powershell
python -m unittest discover -s tests -v
python -m ameli_extract --help
```
