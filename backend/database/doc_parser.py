import docx
import fitz  # PyMuPDF


class DocumentParser:
    @staticmethod
    def is_scanned_pdf(page_text: str) -> bool:
        """
        Simple heuristic: If a page has very little text, it's likely scanned/image.
        """
        return len(page_text.strip()) < 10

    @staticmethod
    def parse_pdf(file_path: str) -> str:
        """
        Extracts text from PDF. Raises error if scanned.
        """
        full_text = []

        try:
            with fitz.open(file_path) as doc:
                for page_num, page in enumerate(doc):
                    text = page.get_text()

                    # Check for scanned page
                    if DocumentParser.is_scanned_pdf(text):
                        # We can either skip or raise error.
                        # You requested to reject the file.
                        raise ValueError(
                            f"Page {page_num + 1} appears to be scanned. Scanned PDFs are not supported."
                        )

                    full_text.append(text)

            return "\n".join(full_text)

        except Exception as e:
            raise ValueError(f"Failed to parse PDF: {str(e)}") from e

    @staticmethod
    def parse_docx(file_path: str) -> str:
        """
        Extracts text from DOCX.
        """
        try:
            doc = docx.Document(file_path)
            # distinct paragraphs with newlines
            full_text = [para.text for para in doc.paragraphs if para.text.strip()]
            return "\n".join(full_text)
        except Exception as e:
            raise ValueError(f"Failed to parse DOCX: {str(e)}") from e

    @staticmethod
    def extract_content(file_path: str, filename: str) -> str:
        """
        Router for file types.
        """
        ext = filename.lower().split(".")[-1]

        if ext == "pdf":
            return DocumentParser.parse_pdf(file_path)
        elif ext in ["docx", "doc"]:
            return DocumentParser.parse_docx(file_path)
        else:
            raise ValueError(f"Unsupported file format: .{ext}")
