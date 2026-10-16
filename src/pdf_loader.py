from pypdf import PdfReader


def extract_pdf_pages(pdf_file):
    reader = PdfReader(pdf_file)

    pages = []

    for page_number, page in enumerate(reader.pages, start=1):

        text = page.extract_text(
            extraction_mode="layout",
            layout_mode_space_vertically=False
        ) or ""

        pages.append({
            "page_number": page_number,
            "text": text
        })

    return pages