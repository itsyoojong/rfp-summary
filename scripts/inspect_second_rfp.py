import re
from pathlib import Path
from src.pdf_loader import extract_pdf_pages

PDF_PATH = Path("data/second_rfp.pdf")

# PDF의 모든 페이지 읽기
with PDF_PATH.open("rb") as file:
    pages = extract_pdf_pages(file)

# 정답 정보를 찾기 위한 검색어
keywords = [
    "사업명",
    "사업기간",
    "사업비",
    "사업예산",
    "사업목적",
    "제출기한",
    "제출마감",
    "제안서제출"
]

print(f"전체 페이지 수: {len(pages)}")

for page in pages:

    # PDF 공백 차이 때문에 검색이 실패하지 않도록 정규화
    text = re.sub(r"\s+", "", page["text"])

    found = [
        keyword
        for keyword in keywords
        if keyword in text
    ]

    if found:
        print(
            f"PDF {page['page_number']}페이지: "
            f"{', '.join(found)}"
        )