from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    gemini_api_key: str = ""
    gemini_api_keys: str = ""  # comma-separated extras

    host: str = "0.0.0.0"
    port: int = 8000

    company_profile_path: Path = Path("company_profile.md")
    database_path: Path = Path("data/tenders.db")
    max_upload_chars: int = 300_000

    # Folder of PDFs/DOCX for COI, GST, ISO, etc. — dashboard vault hints (optional)
    document_vault_path: str | None = None

    # When True, retry thin PDF extracts with OCR if pytesseract+pdf2image+Tesseract are installed
    enable_pdf_ocr: bool = True

    def all_gemini_keys(self) -> list[str]:
        keys: list[str] = []
        if self.gemini_api_key.strip():
            keys.append(self.gemini_api_key.strip())
        for part in self.gemini_api_keys.split(","):
            k = part.strip()
            if k and k not in keys:
                keys.append(k)
        return [k for k in keys if len(k) > 20]


def load_company_profile(settings: Settings) -> str:
    p = settings.company_profile_path
    if not p.is_absolute():
        p = Path.cwd() / p
    if p.exists():
        try:
            return p.read_text(encoding="utf-8", errors="replace")[:12000]
        except OSError:
            pass
    return (
        "Company profile file not found or unreadable. "
        "Add company_profile.md in the project root with certifications, turnover, and key projects."
    )
