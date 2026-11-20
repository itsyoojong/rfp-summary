import hashlib
import streamlit as st

from src.pdf_loader import extract_pdf_pages
from src.rfp_analyzer import (
    analyze_rfp_document,
    validate_budget_condition,
    validate_analysis_result
)

from src.result_merger import merge_group_results
from src.source_validator import validate_sources
from src.value_validator import validate_budget_amount
from src.session_results import disk_cache_enabled, set_document, persist_groups
from src.candidate_resolver import resolve_candidates
from src.diagnostics import analysis_stage, log_failure, user_message
from src.result_ui import render_analysis_result

CHUNK_SIZE = 3

st.set_page_config(
    page_title="RFP Insight",
    layout="wide"
)

st.title("RFP Insight")
st.write("AI 기반 제안요청서 분석 서비스")

st.caption(
    "분석 시 선택한 페이지의 텍스트가 Groq API로 전송됩니다. "
    "전송이 허용된 문서만 업로드하세요."
)
if disk_cache_enabled():
    st.warning("로컬 디스크 캐시가 활성화되어 있습니다. 서버에 분석 결과가 저장·복원됩니다.")
else:
    st.caption(
        "분석 결과는 현재 세션에서만 유지하며 앱의 디스크 캐시에 저장·복원하지 않습니다. "
        "세션 종료 후에는 결과가 유지되지 않을 수 있습니다."
    )

uploaded_file = st.file_uploader(
    "분석할 PDF 파일을 업로드하세요.",
    type=["pdf"]
)

if uploaded_file is not None:

    try:
        with analysis_stage("upload_read"):
            document_id = hashlib.sha256(uploaded_file.getvalue()).hexdigest()
    except Exception as error:
        st.error(user_message(error))
        st.stop()

    try:
        with analysis_stage("cache_restore"):
            set_document(st.session_state, document_id, CHUNK_SIZE)
    except (OSError, ValueError):
        st.warning("로컬 캐시를 읽지 못했습니다. 현재 세션에서 새로 분석할 수 있습니다.")

    stage = "pdf_extract"
    metadata = {}
    try:
        with analysis_stage(stage):
            uploaded_file.seek(0)
            pages = extract_pdf_pages(uploaded_file)
        metadata = {"page_count": len(pages)}
        if not pages:
            st.warning("분석할 PDF 페이지가 없습니다.")
            st.stop()

        st.success("PDF 파일을 읽었습니다.")
        st.write("전체 페이지:", len(pages))

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

            if st.button("AI 분석 시작"):

                try:
                    stage = "page_analysis"
                    with st.spinner("분석 중입니다."):
                        result = analyze_rfp_document([pages[selected_page - 1]])

                    with analysis_stage("session_save", page_start=selected_page, page_end=selected_page):
                        validate_analysis_result(result)
                        st.session_state.page_results[selected_page] = result

                except Exception as error:
                    log_failure(stage, error, **metadata)
                    st.error(user_message(error))

            if selected_page in st.session_state.page_results:

                result = st.session_state.page_results[selected_page]

                st.subheader("페이지 분석 결과")
                with st.expander("페이지 분석 JSON 확인"):
                    st.json(result)

                warnings = validate_budget_condition(result)
                for warning in warnings:
                    st.warning(warning)

        else:
            st.warning("텍스트를 추출하지 못했습니다.")

        st.divider()
        st.subheader("그룹별 RFP 분석")

        chunk_size = CHUNK_SIZE

        stage = "group_creation"
        page_groups = [
            pages[i:i + chunk_size]
            for i in range(0, len(pages), chunk_size)
        ]

        if st.session_state.pop("cache_save_failed", False):
            st.warning("로컬 캐시 저장에 실패했습니다. 세션 결과는 유지됩니다.")

        total_groups = len(page_groups)
        completed = len(st.session_state.group_results)

        if disk_cache_enabled() and st.session_state.group_results:

            if st.button("현재 분석 결과 로컬 저장"):

                try:
                    persist_groups(
                        document_id,
                        CHUNK_SIZE,
                        st.session_state.group_results
                    )

                    st.success("분석 결과가 로컬에 저장됐습니다.")

                except OSError as error:
                    log_failure("cache_save", error, **metadata)
                    st.error("로컬 캐시 저장에 실패했습니다. 세션 결과는 유지됩니다.")

        st.write(f"분석 진행: {completed}/{total_groups}개 그룹")
        st.progress(completed / total_groups)

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
                    stage = "group_analysis"
                    metadata = {"page_count": len(pages), "group_number": next_index + 1,
                                "page_start": start_page, "page_end": end_page}
                    with st.spinner("Groq 분석 중입니다."):

                        result = analyze_rfp_document(
                            current_group, group_number=next_index + 1
                        )

                    with analysis_stage("session_save", **metadata):
                        validate_analysis_result(result)
                        st.session_state.group_results[next_index] = result

                    try:
                        persist_groups(document_id, CHUNK_SIZE, st.session_state.group_results)
                    except OSError as error:
                        log_failure("cache_save", error, **metadata)
                        st.session_state["cache_save_failed"] = True

                    st.rerun()

                except Exception as error:
                    log_failure(stage, error, **metadata)
                    st.error(user_message(error))

        else:
            st.success("모든 그룹 분석이 완료됐습니다.")

        st.divider()

        if st.session_state.group_results:

            stage = "candidate_merge"
            merged_result = merge_group_results(
                st.session_state.group_results
            )

            stage = "candidate_resolve"
            resolved_result = resolve_candidates(
                merged_result,
                pages
            )

            stage = "budget_validation"
            budget_warnings = validate_budget_amount(merged_result)
            for candidate in merged_result["사업비"]["candidates"]:
                budget_warnings.extend(validate_budget_condition({"사업비": candidate}))

            stage = "source_validation"
            validation = validate_sources(merged_result, pages)

            stage = "result_display"
            render_analysis_result(
                resolved_result, merged_result, pages, validation,
                budget_warnings, st.session_state.group_results,
                CHUNK_SIZE, complete=completed == total_groups
            )

        else:
            st.info("분석된 그룹이 아직 없습니다.")

    except Exception as error:
        log_failure(stage, error, **metadata)
        st.error(user_message(error))

else:
    set_document(st.session_state, None, CHUNK_SIZE)
