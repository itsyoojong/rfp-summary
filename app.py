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

        st.divider()
        st.subheader("통합 분석 결과")

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

            if completed < total_groups:
                st.info(
                    "아직 모든 페이지를 분석하지 않았습니다. "
                    "현재까지의 중간 결과입니다."
                )
            else:
                st.success("모든 그룹의 결과를 수집했습니다.")

            stage = "result_display"
            final_results = {
                field: {
                    "status": result["status"],
                    "selected": result["selected"],
                    "선택 후보 인용 검증": (
                        "검토 필요" if any(
                            candidate["issues"] or not candidate["valid_pages"]
                            for candidate in result["candidates"]
                            if candidate["value"] == result["selected"]
                        ) or result["selected"] is None else "지정 페이지 인용 일치"
                    )
                }
                for field, result in resolved_result.items()
            }

            st.json(final_results)

            with st.expander("전체 추출 후보 확인"):
                st.json(merged_result)

            st.subheader("사업비 금액 검증")

            stage = "budget_validation"
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

            stage = "source_validation"
            validation = validate_sources(merged_result, pages)

            st.subheader("원문 근거 검증")

            st.write(
                f"원문 근거 검증: "
                f"{validation['checked']}개 중 "
                f"{validation['matched']}개 일치, "
                f"{len(validation['issues'])}개 확인 필요"
            )

            if validation["issues"]:
                with st.expander("원문 검증 실패 목록"):
                    st.json(validation["issues"])
            if validation["issues"]:

                for index, issue in enumerate(validation["issues"]):

                    field = issue["field"]
                    page_number = issue["page"]

                    with st.expander(
                        f"{field} / PDF {page_number}페이지 상세 비교"
                    ):

                        st.write("AI가 추출한 정보")

                        candidates = merged_result[field]["candidates"]

                        for candidate in candidates:

                            for source in candidate["sources"]:

                                if source["page"] == page_number:

                                    st.write("추출값:", candidate["value"])
                                    st.write("AI 인용문:")
                                    st.code(source["quote"])

                        original_page = next(
                            (
                                page["text"]
                                for page in pages
                                if page["page_number"] == page_number
                            ),
                            None
                        )

                        if original_page is not None:
                            st.text_area(
                                "실제 PDF에서 추출한 원문",
                                value=original_page,
                                height=250,
                                key=f"source_page_{index}"
                            )
                        else:
                            st.warning("해당 PDF 페이지를 찾을 수 없습니다.")

            for field, entry in merged_result.items():

                candidate_count = len(entry["candidates"])

                if candidate_count <= 1:
                    continue

                decision = resolved_result[field]

                if decision["selected"] is not None:

                    st.info(
                        f"{field}: 후보 {candidate_count}개 중 "
                        "대표값 선택을 완료했습니다."
                    )

                else:

                    st.warning(
                        f"{field}: 후보 {candidate_count}개가 발견됐지만 "
                        "대표값을 확정하지 못했습니다. 원문 검토가 필요합니다."
                    )

            st.caption(
                "인용문과 지정 페이지는 추출 텍스트와 공백을 무시해 대조합니다. "
                "대표값 선택은 모든 인용문의 일치를 보장하지 않으며, 불일치 근거는 별도로 검토하세요. "
                "사업비 조건 검증은 일부 조건만 검사합니다."
            )

        else:
            st.info("분석된 그룹이 아직 없습니다.")

    except Exception as error:
        log_failure(stage, error, **metadata)
        st.error(user_message(error))

else:
    set_document(st.session_state, None, CHUNK_SIZE)
