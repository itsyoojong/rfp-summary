import hashlib
import streamlit as st
from groq import APIStatusError


from src.pdf_loader import extract_pdf_pages
from src.rfp_analyzer import (
    analyze_rfp_text,
    analyze_rfp_document,
    validate_budget_condition
)

# from src.rfp_analyzer import (
#     analyze_rfp_text,
#     analyze_rfp_document
# )
# import streamlit as st
# from src.pdf_loader import extract_pdf_pages
# import os
# import json
# from pathlib import Path
# from dotenv import load_dotenv
# from groq import Groq


st.set_page_config(
    page_title="RFP Insight",
    layout="wide"
)

st.title("RFP Insight")
st.write("AI 기반 제안요청서 분석 서비스")  






# # 프로젝트 폴더의 .env 파일 불러오기
# load_dotenv(Path(__file__).resolve().parent / ".env")
# # 웹페이지 기본 설정
# st.set_page_config(
#     page_title="RFP Insight",
#     page_icon="📄",
#     layout="wide"
# )

# st.title("RFP Insight")
# st.write("AI 기반 제안요청서 분석 서비스")

# # PDF 파일 업로드
# uploaded_file = st.file_uploader(
#     "분석할 RFP 문서를 업로드하세요.",
#     type=["pdf"]
# )

# 1. Session State 초기화
if "document_id" not in st.session_state:
    st.session_state.document_id = None

if "page_results" not in st.session_state:
    st.session_state.page_results = {}

if "document_result" not in st.session_state:
    st.session_state.document_result = None


# 2. PDF 업로드
uploaded_file = st.file_uploader(
    "분석할 PDF 파일을 업로드하세요.",
    type=["pdf"]
)

if uploaded_file is not None:

    # PDF 파일 내용으로 식별값 생성
    document_id = hashlib.sha256(
        uploaded_file.getvalue()
    ).hexdigest()

    # 새로운 PDF면 이전 분석 결과 초기화
    if st.session_state.document_id != document_id:
        st.session_state.document_id = document_id
        st.session_state.page_results = {}
        st.session_state.document_result = None

    try:
        # 3. PDF 읽기
        uploaded_file.seek(0)
        pages = extract_pdf_pages(uploaded_file)

        st.success("PDF 파일을 읽었습니다.")
        st.write("전체 페이지:", len(pages))

        # 4. 페이지 선택
        selected_page = st.selectbox(
            "확인할 페이지를 선택하세요.",
            options=range(1, len(pages) + 1)
        )

        page_text = pages[selected_page - 1]["text"]

        if page_text.strip():

            st.text_area(
                "추출된 텍스트",
                value=page_text,
                height=300
            )

            # 5. 개별 페이지 분석
            if st.button("AI 분석 시작"):

                try:
                    with st.spinner("분석 중입니다."):
                        result = analyze_rfp_document([pages[selected_page - 1]])
                        #result = analyze_rfp_text(page_text)

                    # 결과를 해당 페이지 번호로 저장
                    st.session_state.page_results[
                        selected_page
                    ] = result

                except Exception as error:
                    st.error(
                        f"분석 실패: {type(error).__name__}"
                    )

            # 저장된 분석 결과 표시
            if selected_page in st.session_state.page_results:

                result = st.session_state.page_results[selected_page]
                
                st.subheader("페이지 분석 결과")
                st.json(result)
                
                warnings = validate_budget_condition(result)
                for warning in warnings:
                    st.warning(warning)

                #st.json(
                #    st.session_state.page_results[selected_page]
                #)

        else:
            st.warning("텍스트를 추출하지 못했습니다.")

        # 6. 전체 문서 분석
        st.divider()
        st.subheader("첫번째 그룹  분석")

        if st.button("첫번째 그룹 분석 시작"):

            try:
                with st.spinner("문서 그룹을 분석 중입니다."):
                    #result = analyze_rfp_document(pages)
                    # 3페이지씩 나누어 그룹 생성
                    chunk_size = 3

                    page_groups = [
                        pages[i:i + chunk_size]
                        for i in range(0, len(pages), chunk_size)
                    ]

                    # 먼저 첫 번째 그룹만 분석
                    result = analyze_rfp_document(page_groups[0])

                    st.write("전체 그룹 수:", len(page_groups))

                st.session_state.document_result = result

            # except Exception as error:
            #     st.error(
            #         f"전체 분석 실패: {type(error).__name__}"
            #     )
                # 1. Groq API 오류를 구체적으로 처리
            except APIStatusError as error:
                st.error("Groq API 요청 실패")
                st.write("HTTP 상태 코드:", error.status_code)
                st.code(str(error)[:500])

    # 2. 그 외 모든 오류 처리
            except Exception as error:
                st.error("전체 문서 분석 중 오류 발생")
                st.write("오류 유형:", type(error).__name__)

        # 저장된 전체 분석 결과 표시
        if st.session_state.document_result is not None:
            result = st.session_state.document_result


            st.subheader("전체 문서 분석 결과")

            st.json(result)
            warnings = validate_budget_condition(result)
            
            for warning in warnings:
                st.warning(warning)

    except Exception as error:
        st.error(
            f"PDF 처리 오류: {type(error).__name__}"
        )