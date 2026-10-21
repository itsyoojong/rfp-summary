import hashlib
import streamlit as st
from groq import APIStatusError


from src.pdf_loader import extract_pdf_pages
from src.rfp_analyzer import (
    analyze_rfp_text,
    analyze_rfp_document,
    validate_budget_condition
)

from src.result_merger import merge_group_results
from src.source_validator import validate_sources

from src.value_validator import validate_budget_amount

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
    
if "group_results" not in st.session_state:
    st.session_state.group_results = {}

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
        st.session_state.group_results = {}

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


        # 6. 그룹별 문서 분석
        st.divider()
        st.subheader("그룹별 RFP 분석")

        # 3페이지씩 그룹 생성
        chunk_size = 3

        page_groups = [
            pages[i:i + chunk_size]
            for i in range(0, len(pages), chunk_size)
        ]

        total_groups = len(page_groups)
        completed = len(st.session_state.group_results)

        # 진행 상황 표시
        st.write(f"분석 진행: {completed}/{total_groups}개 그룹")
        st.progress(completed / total_groups)

        # 아직 분석하지 않은 그룹이 있다면
        if completed < total_groups:

            next_index = completed
            current_group = page_groups[next_index]

            start_page = current_group[0]["page_number"]
            end_page = current_group[-1]["page_number"]

            st.write(
                f"다음 분석: {start_page}~{end_page}페이지"
            )

            if st.button("다음 그룹 분석"):

                try:
                    with st.spinner("Groq 분석 중입니다."):

                        result = analyze_rfp_document(
                            current_group
                        )

                    # 분석 결과 저장
                    st.session_state.group_results[
                        next_index
                    ] = result

                    # 화면 갱신
                    st.rerun()

                except APIStatusError as error:
                    st.error("Groq API 요청 실패")
                    st.write("상태 코드:", error.status_code)
                    st.code(str(error)[:500])

                except Exception as error:
                    st.error(
                        f"분석 오류: {type(error).__name__}"
                    )

        else:
            st.success("모든 그룹 분석이 완료됐습니다.")



        # 7. 그룹별 분석 결과 출력
        st.subheader("그룹별 분석 결과")

        for group_index in sorted(st.session_state.group_results):

            result = st.session_state.group_results[group_index]

            group_start = group_index * chunk_size + 1
            group_end = min(
                (group_index + 1) * chunk_size,
                len(pages)
            )

            with st.expander(
                f"그룹 {group_index + 1}: "
                f"{group_start}~{group_end}페이지"
            ):
                st.json(result)

                warnings = validate_budget_condition(result)

                for warning in warnings:
                    st.warning(warning)

        # 8. 그룹별 결과 통합
        st.divider()
        st.subheader("통합 분석 결과")

        if st.session_state.group_results:

            merged_result = merge_group_results(
                st.session_state.group_results
            )

            if completed < total_groups:
                st.info(
                    "아직 모든 페이지를 분석하지 않았습니다. "
                    "현재까지의 중간 결과입니다."
                )
            else:
                st.success("모든 그룹의 결과를 수집했습니다.")

            st.json(merged_result)
                        # 9. 사업비 금액 검증
            st.subheader("사업비 금액 검증")

            budget_warnings = validate_budget_amount(
                merged_result
            )

            budget_candidates = merged_result.get(
                "사업비", {}
            ).get("candidates", [])

            if not budget_candidates:
                st.info("아직 사업비 데이터가 없습니다.")

            elif budget_warnings:
                for warning in budget_warnings:
                    st.warning(warning)

            elif any(
                not candidate.get("sources")
                for candidate in budget_candidates
            ):
                st.warning(
                    "근거가 없는 사업비 후보가 있습니다."
                )

            else:
                st.success(
                    "사업비 금액 검증: 현재 비교 가능한 "
                    "금액에서 불일치가 발견되지 않았습니다."
                )
            
            # 원문 근거 검증
            # validation = validate_sources(merged_result, pages)
            # if validation["issues"]:
            #     st.subheader("원문 불일치 상세 정보")
            #     st.json(validation["issues"])

            # st.subheader("원문 근거 검증 결과")

            # st.write("검사한 인용문:", validation["checked"])
            # st.write("원문 일치:", validation["matched"])
            # st.write("확인 필요:", len(validation["issues"]))

            # for issue in validation["issues"]:
            #     st.warning(
            #         f"{issue['field']} / "
            #         f"{issue['page']}페이지: "
            #         f"{issue['reason']}"
            #     )
            
            validation = validate_sources(merged_result, pages)

            st.subheader("원문 근거 검증")

            # st.write("검사한 인용문:", validation["checked"])
            # st.write("원문 일치:", validation["matched"])
            # st.write("불일치 개수:", len(validation["issues"]))
            st.write(
                f"원문 근거 검증: "
                f"{validation['checked']}개 중 "
                f"{validation['matched']}개 일치, "
                f"{len(validation['issues'])}개 확인 필요"
            )

            st.subheader("불일치 상세 내용")
            st.json(validation["issues"])
            # 불일치 원인 확인
            if validation["issues"]:
            
                for index, issue in enumerate(validation["issues"]):
                
                    field = issue["field"]
                    page_number = issue["page"]

                    with st.expander(
                        f"{field} / PDF {page_number}페이지 상세 비교"
                    ):

                        # 1. AI가 반환한 후보와 인용문 확인
                        st.write("AI가 추출한 정보")

                        candidates = merged_result[field]["candidates"]

                        for candidate in candidates:
                        
                            for source in candidate["sources"]:
                            
                                if source["page"] == page_number:
                                
                                    st.write("추출값:", candidate["value"])
                                    st.write("AI 인용문:")
                                    st.code(source["quote"])

                        # 2. 실제 PDF 페이지 확인
                        original_page = next(
                            (
                                page["text"]
                                for page in pages
                                if page["page_number"] == page_number
                            ),
                            None
                        )

                        if original_page is not None:
                            # 실제 PDF 5페이지의 핵심 부분 확인
                            if page_number == 5:
                            
                                st.write("PDF 5페이지 원문 확인")
                            
                                keyword = "제안요청서"
                            
                                position = original_page.find(keyword)
                            
                                if position != -1:
                                    start = max(0, position - 100)
                                    end = min(len(original_page), position + 400)
                            
                                    st.code(original_page[start:end])
                            
                                else:
                                    st.code(original_page[:800])
                            st.text_area(
                                "실제 PDF에서 추출한 원문",
                                value=original_page,
                                height=250,
                                key=f"debug_page_{index}"
                            )
                            # PDF 원문에서 사업명 근처의 내용 찾기
                            keyword = "K-sure"

                            position = original_page.find(keyword)

                            if position != -1:
                                start = max(0, position - 100)
                                end = position + 200

                                st.code(original_page[start:end])
                            else:
                                st.warning("해당 페이지에서 K-sure를 찾지 못했습니다.")

                        else:
                            st.warning("해당 PDF 페이지를 찾을 수 없습니다.")

            # 복수 후보 확인
            for field, entry in merged_result.items():

                if len(entry["candidates"]) > 1:
                    st.warning(
                        f"{field}: 서로 다른 후보가 발견됐습니다. "
                        "원문 확인이 필요합니다."
                    )

            st.caption(
                "근거 인용문과 페이지 번호는 "
                "아직 원본 PDF와 자동 대조하지 않았습니다."
            )

        else:
            st.info("분석된 그룹이 아직 없습니다.")

    except Exception as error:
        st.error(
            f"PDF 처리 오류: {type(error).__name__}"
        )
