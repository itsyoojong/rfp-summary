from src.rfp_analyzer import (
    analyze_rfp_text,
    analyze_rfp_document
)
import streamlit as st
from src.pdf_loader import extract_pdf_pages
import os
import json
from pathlib import Path
from dotenv import load_dotenv
from groq import Groq





# 프로젝트 폴더의 .env 파일 불러오기
load_dotenv(Path(__file__).resolve().parent / ".env")
# 웹페이지 기본 설정
st.set_page_config(
    page_title="RFP Insight",
    page_icon="📄",
    layout="wide"
)

st.title("RFP Insight")
st.write("AI 기반 제안요청서 분석 서비스")

# PDF 파일 업로드
uploaded_file = st.file_uploader(
    "분석할 RFP 문서를 업로드하세요.",
    type=["pdf"]
)

if uploaded_file is not None:

    try:
#         # 업로드된 PDF 읽기
#         reader = PdfReader(uploaded_file)

#         # 페이지별 텍스트 추출
#         pages = []

#         for page_number, page in enumerate(
#             reader.pages, start=1
#         ):
#             text = page.extract_text(
#     extraction_mode="layout",
#     layout_mode_space_vertically=False
# ) or ""

#             pages.append({
#                 "page_number": page_number,
#                 "text": text
#             })
        pages = extract_pdf_pages(uploaded_file)

        st.success("PDF 파일을 읽었습니다.")

        # 문서 기본 정보
        st.write("전체 페이지:", len(pages))

        # 페이지 선택
        selected_page = st.selectbox(
            "확인할 페이지를 선택하세요.",
            options=range(1, len(pages) + 1)
        )

        # 선택한 페이지의 텍스트 출력
        page_text = pages[selected_page - 1]["text"]

        if page_text.strip():
            st.text_area(
                "추출된 텍스트",
                value=page_text,
                height=300
                
            )
                        # 현재 선택한 페이지를 AI로 분석
        if st.button("AI 분석 시작"):

            try:
                with st.spinner("AI가 문서를 분석하고 있습니다."):

                    result = analyze_rfp_text(page_text)

                st.subheader("AI 분석 결과")
                st.json(result)

            except Exception as error:
                st.error("AI 분석 중 오류가 발생했습니다.")
                st.write("오류 유형:", type(error).__name__)
        else:
            st.warning("이 페이지에서 텍스트를 추출하지 못했습니다.")
            
        # 전체 문서 분석: 기존 페이지 분석 if문 바깥
        st.divider()
        st.subheader("문서 전체 분석")

        if st.button("전체 RFP 분석 시작"):
            try:
                with st.spinner("전체 문서를 분석 중입니다."):
                    result = analyze_rfp_document(pages)

                st.success("전체 문서 분석 완료")
                st.json(result)

            except Exception as error:
                st.error("전체 문서 분석에 실패했습니다.")
                st.write("오류 유형:", type(error).__name__)

    except Exception as error:
        st.error("PDF 처리 중 오류가 발생했습니다.")
        st.write("오류 유형:", type(error).__name__)    
        
