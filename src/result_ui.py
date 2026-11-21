"""분석 결과를 변경하지 않고 카드와 검증 상세로 표시한다."""
import re

import streamlit as st

FIELDS = ("사업명", "사업기간", "사업비", "사업목적", "제출기한")


def card_state(field, decision, entry, validation, budget_warnings):
    candidates = entry.get("candidates", [])
    selected = decision.get("selected")
    selected_evidence = [candidate for candidate in decision.get("candidates", [])
                         if candidate["value"] == selected]
    field_issues = [issue for issue in validation["issues"] if issue["field"] == field]
    has_sources = any(candidate.get("sources") for candidate in candidates)
    integrated = len(candidates) > 1 and selected is not None

    # 대표값 선택과 모든 인용문의 검증 성공은 별개의 판단이다.
    verified = (
        selected is not None and bool(selected_evidence)
        and all(candidate["valid_pages"] and not candidate["issues"]
                for candidate in selected_evidence)
        and not field_issues
        and not (field == "사업비" and budget_warnings)
    )
    status = "미확인" if not candidates else "근거 확인" if verified else "검토 필요"
    return {"status": status, "integrated": integrated, "has_sources": has_sources}


def _display_text(value, label, *, purpose=False):
    def paragraphs(text):
        # 구분 기호와 내용은 보존하고 표시용 문단 경계만 추가한다.
        return re.sub(r"(?<!\n)(?=[◯○●])", "\n\n", text) if purpose else text

    if purpose and len(value) > 320:
        st.caption("추출 내용 앞부분 · 요약이 아닙니다.")
        st.write(paragraphs(value[:320]) + "…")
        with st.expander(f"{label} 전체 내용"):
            st.write(paragraphs(value))
    else:
        st.write(paragraphs(value))


def _render_card(field, decision, entry, state):
    with st.container(border=True):
        st.subheader(field)
        if state["status"] == "근거 확인":
            st.success("근거 확인")
        elif state["status"] == "검토 필요":
            st.warning("검토 필요")
        else:
            st.caption("미확인")

        selected = decision.get("selected")
        if selected is not None:
            _display_text(selected, field, purpose=field == "사업목적")
        elif entry.get("candidates"):
            st.write("대표값을 확정하지 못했습니다.")
            with st.expander(f"{field} 미확정 추출 후보"):
                st.caption("아래 내용은 확정된 정보가 아닙니다.")
                for index, candidate in enumerate(entry["candidates"], 1):
                    st.caption(f"후보 {index}")
                    _display_text(candidate["value"], f"{field} 후보 {index}", purpose=field == "사업목적")
        else:
            st.write("문서에서 해당 정보를 추출하지 못했습니다.")

        if state["integrated"]:
            st.caption("복수 후보 통합 완료 · 모든 인용문의 일치를 의미하지 않습니다.")
        st.caption(f"선택 판단: {decision.get('status', '미확인')}")
        st.caption("원문 근거: 하단 ‘원문 인용문 및 페이지 확인’에서 확인 가능"
                   if state["has_sources"] else "원문 근거: 등록된 인용문 없음")


def render_analysis_result(resolved_result, merged_result, pages, validation,
                           budget_warnings, group_results, chunk_size, complete):
    st.subheader("RFP 핵심 분석 결과")
    if not complete:
        st.info("분석 진행 중 · 중간 결과입니다. 남은 그룹 분석에 따라 대표값과 상태가 달라질 수 있습니다.")
    else:
        st.caption("전체 그룹 분석 완료 · 검토 필요 항목은 원문을 확인하세요.")

    states = {field: card_state(field, resolved_result[field], merged_result[field],
                                validation, budget_warnings) for field in FIELDS}
    _render_card("사업명", resolved_result["사업명"], merged_result["사업명"], states["사업명"])
    period, budget = st.columns(2)
    with period:
        _render_card("사업기간", resolved_result["사업기간"], merged_result["사업기간"], states["사업기간"])
    with budget:
        _render_card("사업비", resolved_result["사업비"], merged_result["사업비"], states["사업비"])
    for field in ("사업목적", "제출기한"):
        _render_card(field, resolved_result[field], merged_result[field], states[field])

    st.divider()
    st.subheader("검증 결과")
    st.write(f"원문 인용: {validation['checked']}개 중 {validation['matched']}개 일치 · "
             f"확인 필요 {len(validation['issues'])}개")
    review = [field for field, state in states.items() if state["status"] == "검토 필요"]
    missing = [field for field, state in states.items() if state["status"] == "미확인"]
    if review:
        st.warning("원문 검토가 필요한 항목: " + ", ".join(review))
    if missing:
        st.caption("미확인 항목: " + ", ".join(missing))
    if budget_warnings:
        for warning in dict.fromkeys(budget_warnings):
            st.warning(warning)
    elif merged_result["사업비"]["candidates"]:
        st.caption("사업비: 비교 가능한 금액에서 불일치가 발견되지 않았습니다. "
                   "인용문 일치와 모든 VAT·상한 조건의 정확성을 보장하지 않습니다.")
    st.caption("인용문은 지정된 PDF 페이지의 추출 텍스트와 공백을 무시해 비교합니다. "
               "근거 확인은 추출값의 의미나 계약 조건 전체의 정확성을 보장하지 않습니다.")

    st.divider()
    st.subheader("상세 데이터")
    with st.expander("원문 인용문 및 페이지 확인"):
        for field in FIELDS:
            st.markdown(f"**{field}**")
            candidates = merged_result[field]["candidates"]
            if not any(candidate.get("sources") for candidate in candidates):
                st.caption("등록된 인용문이 없습니다.")
            for index, candidate in enumerate(candidates, 1):
                st.caption(f"후보 {index}")
                st.write(candidate["value"])
                for source in candidate["sources"]:
                    st.caption(f"PDF {source['page']}페이지 · AI 인용문")
                    st.text(source["quote"])

    with st.expander("원문 검증 실패 상세"):
        if not validation["issues"]:
            st.caption("검사한 인용문에서 불일치가 발견되지 않았습니다. 근거가 없는 항목은 검증되지 않습니다.")
        else:
            st.json(validation["issues"])
            page_map = {page["page_number"]: page["text"] for page in pages}
            for index, issue in enumerate(validation["issues"]):
                st.write(f"{issue['field']} / PDF {issue['page']}페이지: {issue['reason']}")
                if issue.get("actual_pages"):
                    st.write("인용문이 발견된 페이지:", issue["actual_pages"])
                if issue["page"] in page_map:
                    st.text_area("지정 페이지의 추출 원문", value=page_map[issue["page"]],
                                 height=200, key=f"source_page_{index}", disabled=True)
                else:
                    st.caption("해당 PDF 페이지가 없습니다.")

    with st.expander("최종 결과 JSON 확인"):
        st.json({field: {"status": decision["status"], "selected": decision["selected"],
                         "선택 후보 인용 검증": states[field]["status"]}
                 for field, decision in resolved_result.items()})
    with st.expander("전체 추출 후보 확인"):
        st.json(merged_result)
    with st.expander("대표값 선택 상세 JSON 확인"):
        st.json(resolved_result)
    with st.expander("그룹별 분석 결과"):
        for group_index in sorted(group_results):
            start = group_index * chunk_size + 1
            end = min((group_index + 1) * chunk_size, len(pages))
            st.markdown(f"**그룹 {group_index + 1}: {start}~{end}페이지**")
            st.json(group_results[group_index])
