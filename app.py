from datetime import date
from html import escape
from itertools import groupby
from pathlib import Path

import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from analysis import (
    CsvValidationError,
    VALID_STATUSES,
    calculate_kpis,
    filter_requests,
    generate_insights,
    korea_today,
    load_requests,
    mark_overdue,
    summarize_department_delays,
    summarize_monthly_trends,
    summarize_workload,
)


SYNTHETIC_SOURCE = "2026년 상반기 합성 CSV"
SYNTHETIC_FILENAME = "synthetic_requests_2026_h1.csv"
SYNTHETIC_SNAPSHOT_DATE = date(2026, 6, 30)
DATA_DIR = Path(__file__).resolve().parent / "data"
DEEP_MAUVE = "#865F78"
DUSTY_ROSE = "#AA8299"
SOFT_ROSE = "#C6A5B6"
MUTED_BLUE = "#8B91B8"
LAVENDER = "#B6B8D0"
CHART_INK = "#302A31"
CHART_GRID = "#EEE9EB"
CHART_FONT = "Pretendard, Malgun Gothic, Apple SD Gothic Neo, Noto Sans KR, Noto Sans CJK KR, sans-serif"


def show_chart(chart, *, horizontal=False, legend=False, bar_radius=0, bar_gap=0.30, card=False):
    """데이터와 축 범위는 유지하고 차트의 외형만 통일합니다."""
    chart.update_layout(
        template="plotly_white",
        paper_bgcolor="#FFFFFF", plot_bgcolor="#FFFFFF",
        font=dict(family=CHART_FONT, size=12, weight=400, color=CHART_INK),
        # 제목은 부서명 축의 여백과 독립적으로 차트 왼쪽에 맞춥니다.
        title=dict(x=0.03, xref="container", font=dict(size=18, weight=600)),
        margin=dict(l=14, r=14 if card else 26, t=104 if card else (96 if legend else 60), b=12 if card else 26),
        autosize=True, bargap=bar_gap, bargroupgap=0.10,
        barcornerradius=bar_radius,
        hoverlabel=dict(bgcolor="#FFFFFF", bordercolor=LAVENDER, font=dict(size=12, color=CHART_INK)),
        # 가로 범례는 좁은 화면에서 여러 줄로 배치될 수 있습니다.
        legend=dict(orientation="h", x=0, y=1.04, xanchor="left", yanchor="bottom",
                    title_text="", font=dict(size=13, weight=500)),
    )
    for axis, show_grid in ((chart.update_xaxes, horizontal), (chart.update_yaxes, not horizontal)):
        axis(
            automargin=True, showline=False, zeroline=False, ticks="",
            showgrid=show_grid, gridcolor=CHART_GRID,
            tickfont=dict(size=12, color=CHART_INK),
            title_font=dict(size=13, weight=500, color=CHART_INK), title_standoff=8,
        )
    st.plotly_chart(
        chart, theme=None, width="stretch",
        config={"responsive": True, "displaylogo": False, "displayModeBar": False if card else "hover"},
    )


def show_department_delays(summary, department_order, snapshot_date):
    st.subheader("담당 부서별 지연율 비교")
    st.caption("비율별 분모가 1~9건이면 소표본으로 표시합니다. 지연율만으로 부서의 처리 능력을 판단하기는 어렵습니다.")
    if snapshot_date:
        st.caption(f"상반기 합성 CSV의 상태 기록일은 {snapshot_date:%Y-%m-%d}입니다. 기준일 변경은 CSV의 상태를 유지하고 현재 지연만 다시 판정합니다.")

    # 비교 기준과 마우스를 올렸을 때 보이는 분자·분모를 차트마다 지정합니다.
    comparison_metrics = (
        ("overdue_rate", "overdue", "unfinished", "미완료 업무 중 지연 비율", DEEP_MAUVE),
        ("completed_late_rate", "completed_late", "completed", "완료 업무 중 지연 비율", DUSTY_ROSE),
    )
    display = summary.set_index("department").reindex(department_order).reset_index()
    for rate, numerator, denominator, label, color in comparison_metrics:
        chart_data = display.dropna(subset=[rate]).copy()
        if chart_data.empty:
            st.markdown(f"**담당 부서별 {label}**")
            st.info("선택한 범위에 이 비율을 계산할 업무가 없습니다. 상세 집계표에는 ‘—’로 표시합니다.")
            continue
        chart_data["label"] = [
            f"{value:.1f}% ({count:,}/{size:,}건)" + (" · 소표본" if size < 10 else "")
            for value, count, size in zip(
                chart_data[rate], chart_data[numerator], chart_data[denominator], strict=True
            )
        ]
        chart = px.bar(
            chart_data, x=rate, y="department", orientation="h", text="label",
            title=f"담당 부서별 {label}",
            labels={rate: f"{label} (%)", "department": "담당 부서"},
            hover_data={rate: False, "label": True},
        )
        chart.update_traces(
            marker_color=color, textposition="auto", cliponaxis=False,
            insidetextfont_color="#FFFFFF" if color == DEEP_MAUVE else CHART_INK,
            outsidetextfont_color=CHART_INK,
            hovertemplate=f"<b>%{{y}}</b><br>{label} %{{text}}<extra></extra>",
        )
        chart.update_xaxes(range=[0, 100], ticksuffix="%")
        chart.update_yaxes(
            type="category", categoryorder="array", categoryarray=department_order,
            autorange="reversed", automargin=True,
        )
        chart.update_layout(height=max(320, 42 * len(department_order) + 120))
        show_chart(chart, horizontal=True, bar_radius=4)

    st.markdown("**담당 부서별 업무량 및 지연 상세 집계**")
    for rate, _, denominator, _, _ in comparison_metrics:
        display[rate] = [
            "—" if size == 0 else f"{value:.1f}%" + (" · 소표본" if size < 10 else "")
            for value, size in zip(display[rate], display[denominator], strict=True)
        ]
    columns = {
        "department": "담당 부서",
        "total": "전체 업무 건수",
        "completed": "완료 업무 건수",
        "unfinished": "미완료 업무 건수",
        "overdue": "현재 지연 업무 건수",
        "overdue_rate": "미완료 업무 중 지연 비율",
        "completed_late": "완료 지연 업무 건수",
        "completed_late_rate": "완료 업무 중 지연 비율",
    }
    st.dataframe(display[list(columns)].rename(columns=columns), hide_index=True)


def add_trend_series(chart, months, values, *, name, color, hover, top_fill=None, customdata=None, ceiling=None):
    """선과 별도로 관측이 이어지는 구간에만 그라데이션을 그립니다."""
    observed = [value for value in values if value is not None]
    # 0 또는 상한에 닿은 계열은 직선으로 연결해 경계를 넘는 곡선을 피합니다.
    touches_boundary = observed and (min(observed) <= 0 or (ceiling is not None and max(observed) >= ceiling))
    line = dict(color=color, width=2.4, shape="linear" if touches_boundary else "spline", smoothing=0.45)
    fade = top_fill or color
    rgb = ", ".join(str(int(fade[i:i + 2], 16)) for i in (1, 3, 5))
    for present, segment in groupby(zip(months, values, strict=True), key=lambda point: point[1] is not None):
        points = list(segment)
        if present and len(points) >= 2:
            segment_months, segment_values = zip(*points)
            chart.add_trace(go.Scatter(
                x=segment_months, y=segment_values, mode="lines",
                line={**line, "width": 0},
                fill="tozeroy", fillgradient=dict(type="vertical", colorscale=[
                    [0, f"rgba({rgb}, 0)"], [1, f"rgba({rgb}, 0.20)"],
                ]),
                name=name, legendgroup=name, showlegend=False, hoverinfo="skip", zorder=-1,
            ))
    chart.add_trace(go.Scatter(
        x=months, y=values, name=name, legendgroup=name, mode="lines+markers",
        marker=dict(color=color, size=6, symbol="circle"), line=line,
        connectgaps=False, customdata=customdata, hovertemplate=hover + "<extra></extra>",
    ))


def show_monthly_trends(monthly, snapshot_date):
    st.subheader("월별 업무 추세")
    st.caption(
        "담당 부서·상태 필터가 월별 집계에도 적용됩니다. 예를 들어 진행 중만 선택하면 완료량은 0건입니다. "
        "요청량은 요청일, 완료 지표는 완료일 기준이며, 분석 기준일은 현재 지연 판정에 적용됩니다."
    )
    if snapshot_date:
        st.caption("상반기 합성 CSV는 가상 업무 기록입니다.")
    st.caption("월별 집계는 CSV에 기록된 요청과 완료를 보여줍니다. 월별 차이만으로 원인이나 조직의 성과를 판단하기는 어렵습니다.")

    months = monthly["month"].tolist()
    volume = go.Figure(layout=dict(title="월별 업무 요청량 및 완료량", height=365, showlegend=True))
    # 요청도 완료도 없는 달은 표의 집계값은 그대로 두고 선과 영역에만 결측으로 표시합니다.
    has_activity = (monthly["requested"] + monthly["completed"]).gt(0)
    for key, name, color in (("requested", "요청량", DEEP_MAUVE), ("completed", "완료량", SOFT_ROSE)):
        values = [int(value) if present else None for value, present in zip(monthly[key], has_activity, strict=True)]
        add_trend_series(volume, months, values, name=name, color=color,
                         hover=f"<b>%{{x}}</b><br>{name} %{{y:,}}건")
    volume.update_xaxes(title_text="월", type="category", categoryorder="array", categoryarray=months, tickangle=-35)
    volume.update_yaxes(title_text="업무 건수 (건)", rangemode="tozero", tickformat="d")
    show_chart(volume, legend=True)

    trend_metrics = (
        ("average_days", "월별 평균 처리 기간", "평균 처리 기간 (일)", "%{y:.1f}일", MUTED_BLUE, LAVENDER),
        ("completed_late_rate", "월별 완료 지연율", "완료 지연율 (%)", "%{y:.1f}%", DUSTY_ROSE, SOFT_ROSE),
    )
    for metric, title, axis_label, hover_value, color, fill in trend_metrics:
        trend = go.Figure(layout=dict(title=title, height=320, showlegend=False))
        hover = f"<b>%{{x}}</b><br>{axis_label} {hover_value}"
        is_rate = metric == "completed_late_rate"
        if is_rate:
            hover += "<br>완료 지연 %{customdata[0]}건 / 완료 %{customdata[1]}건"
            trend.update_yaxes(range=[0, 100], ticksuffix="%")
        else:
            trend.update_yaxes(rangemode="tozero")
        values = [float(value) if count else None for value, count in zip(monthly[metric], monthly["completed"], strict=True)]
        add_trend_series(
            trend, months, values, name=title, color=color, hover=hover, top_fill=fill,
            customdata=monthly[["completed_late", "completed"]].to_numpy(), ceiling=100 if is_rate else None,
        )
        trend.update_xaxes(title_text="월", type="category", categoryorder="array", categoryarray=months, tickangle=-35)
        trend.update_yaxes(title_text=axis_label)
        show_chart(trend)

    if not monthly["completed"].any():
        st.info("선택한 범위에 완료 업무가 없어 평균 처리 기간과 완료 지연율을 계산할 수 없습니다.")

    st.markdown("**월별 상세 집계**")
    display = monthly.copy()
    for metric, unit in (("average_days", "일"), ("completed_late_rate", "%")):
        display[metric] = [
            f"{value:.1f}{unit}" if count else "—"
            for value, count in zip(monthly[metric], monthly["completed"], strict=True)
        ]
    st.dataframe(display.rename(columns={
        "month": "월",
        "requested": "요청 건수",
        "completed": "완료 건수",
        "average_days": "평균 처리 기간",
        "completed_late": "완료 지연 건수",
        "completed_late_rate": "완료 지연율",
    }), hide_index=True)


def show_insights(insights, csv_name, departments, statuses, reference_date, snapshot_date, small_sample=False):
    st.subheader("자동 분석 인사이트")
    st.caption(
        f"담당 부서 {', '.join(departments) or '전체'} · 상태 {', '.join(statuses) or '전체'}"
        f" · 지연 기준일 {reference_date:%Y-%m-%d} (한국 시간)"
    )
    if not insights:
        st.info("선택한 범위에 업무가 없어 인사이트를 생성할 수 없습니다.")
        return

    recalculated = snapshot_date is not None and reference_date > snapshot_date
    details = []
    # 핵심 수치는 카드에, 긴 해석은 필요할 때 열어 보는 영역에 둡니다.
    for start in range(0, len(insights), 2):
        for column, (topic, evidence, interpretation) in zip(
            st.columns(2), insights[start:start + 2]
        ):
            if recalculated and topic in ("현재 지연 현황", "담당 부서별 현재 지연율"):
                topic = "스냅샷 기준 지연 재계산" if topic == "현재 지연 현황" else "부서별 지연 재계산"
                interpretation += " 재계산 범위는 위의 데이터 기준일 안내를 참고하세요."
            with column.container(border=True):
                st.markdown(f"**{topic}**")
                st.text(evidence)
            details.append((topic, interpretation))
    if len(insights) == 2:
        st.caption("선택 범위에서 표시 기준을 충족한 부서별·월별 비교가 없습니다.")
    if small_sample:
        st.caption("소표본 주의 · 분모가 1~9건인 비율은 업무 한 건으로도 크게 달라질 수 있습니다.")
    with st.expander("해석과 집계 기준"):
        st.text(f"CSV {csv_name}")
        for topic, interpretation in details:
            st.markdown(f"**{topic}**")
            st.caption(interpretation)
        st.caption("비교 기준은 요약할 내용을 고르는 규칙입니다. 지표 차이만으로 원인이나 업무 성과를 판단하기는 어렵습니다.")


def open_synthetic_dashboard():
    # 화면과 데이터 선택을 버튼 콜백에서 함께 바꿉니다.
    st.session_state["page"] = "분석 대시보드"
    st.session_state["data_source"] = SYNTHETIC_SOURCE


def show_project_overview():
    try:
        data = load_requests((DATA_DIR / SYNTHETIC_FILENAME).read_bytes())
    except (OSError, CsvValidationError):
        data = None

    with st.container(key="overview_intro"):
        st.html(f"""
            <style>
            .st-key-overview_intro [data-testid="stBaseButton-primary"] {{
                background: {DEEP_MAUVE};
                border-color: {DEEP_MAUVE};
                color: #FFFFFF;
            }}
            .st-key-overview_intro [data-testid="stBaseButton-primary"]:hover {{
                background: #704B62;
                border-color: #704B62;
                color: #FFFFFF;
            }}
            @media (max-width: 600px) {{
                [data-testid="stMainBlockContainer"]:has(.st-key-overview_intro) {{
                    padding-top: 52px;
                }}
                .st-key-overview_intro h1 {{
                    font-size: 34px;
                    padding-block: 8px 6px;
                }}
                [data-testid="stMainBlockContainer"]:has(.st-key-overview_intro) h2 {{
                    font-size: 28px;
                    padding-block: 8px 6px;
                }}
            }}
            </style>
        """)
        st.title("WorkFlow Insight")
        st.write("담당 부서별 업무량과 지연 현황을 비교해, 업무 병목을 어디서부터 살펴볼지 파악하는 프로젝트입니다.")
        if data is not None and not data.empty:
            first_request, last_request = data["requested_at"].min(), data["requested_at"].max()
            period = f"{first_request.year}년 {first_request.month}월"
            if (first_request.year, first_request.month) != (last_request.year, last_request.month):
                last_year = f"{last_request.year}년 " if first_request.year != last_request.year else ""
                period += f"~{last_year}{last_request.month}월"
            st.caption(
                f"{period} 요청 · 가상 업무 데이터 · 담당 부서 {data['department'].nunique()}곳"
                f" · 상태 기준일 {SYNTHETIC_SNAPSHOT_DATE:%Y-%m-%d}"
            )
        st.button("분석 대시보드 열기", type="primary", on_click=open_synthetic_dashboard)

    st.header("해결하려는 문제")
    st.write("완료율 하나로는 업무가 어느 부서에 몰리는지, 기한이 지난 일이 아직 남아 있는지 파악하기 어렵습니다.")
    st.markdown(
        "- **업무는 어디에 몰려 있나?** 담당 부서별 업무량과 상태 구성을 비교합니다.\n"
        "- **마감을 넘긴 업무는 어떤 상태인가?** 미완료 상태로 남은 지연과 마감 후 완료를 구분합니다.\n"
        "- **월별 업무 흐름은 어떻게 달라졌나?** 요청량·완료량, 평균 처리 기간, 완료 지연율을 살펴봅니다."
    )

    # 개요는 대시보드와 같은 집계 함수를 전체 합성 데이터와 고정 기준일에 적용합니다.
    st.header("주요 분석 사례")
    if data is None or data.empty:
        st.info("가상 업무 데이터를 읽을 수 없어 분석 사례를 표시할 수 없습니다.")
    else:
        departments = summarize_department_delays(mark_overdue(data, SYNTHETIC_SNAPSHOT_DATE))
        overdue = int(departments["overdue"].sum())
        completed_late = int(departments["completed_late"].sum())
        monthly = summarize_monthly_trends(data)
        with st.container(border=True):
            st.markdown("**월별 요청량 변화**")
            last_months = monthly.loc[monthly["month"].le(last_request.strftime("%Y-%m"))].tail(2)
            if len(last_months) == 2:
                st.write(" → ".join(
                    f"{int(month[-2:])}월 요청 {count:,}건"
                    for month, count in last_months[["month", "requested"]].itertuples(index=False, name=None)
                ))
            st.caption("요청일 기준으로 월별 업무량을 비교합니다. 건수 차이만으로 원인이나 업무 처리 성과를 판단하기는 어렵습니다.")
        with st.container(border=True):
            st.markdown("**미완료 지연과 마감 후 완료**")
            st.write(f"미완료 지연 {overdue:,}건 · 마감 후 완료 {completed_late:,}건")
            st.caption("미완료 지연은 기준일에 마감이 지난 미완료 업무입니다. 마감 후 완료는 완료일이 마감일보다 늦은 업무입니다.")

    st.header("분석 방법과 기술")
    st.write("CSV의 필수 컬럼·상태·날짜를 검증하고, 담당 부서와 업무 상태별로 집계합니다. 차트와 규칙 기반 인사이트는 이 집계 결과를 사용합니다.")
    st.markdown(
        "- **Python**의 표준 라이브러리로 CSV를 읽고, 기한 판정과 인사이트 규칙을 구현합니다.\n"
        "- **Pandas**로 날짜를 변환하고 업무 데이터를 필터링해 부서별·월별로 집계합니다.\n"
        "- **Streamlit**으로 CSV 업로드, 필터, 대시보드 화면을 제공합니다.\n"
        "- **Plotly**로 막대와 추세 차트, 상세 툴팁을 표시합니다."
    )
    st.caption("데이터 한계 · 업무 상태 변경 이력이 없어 과거 시점의 미완료 잔량(백로그)을 추정할 수 없습니다.")


# 페이지 설정은 화면에 내용을 표시하기 전에 한 번 지정합니다.
st.set_page_config(page_title="WorkFlow Insight")

# 화면을 선택할 때마다 Streamlit이 이 파일을 위에서부터 다시 실행합니다.
page = st.sidebar.radio("화면 선택", ("프로젝트 개요", "분석 대시보드"), key="page")

if page == "프로젝트 개요":
    show_project_overview()
else:
    st.title("분석 대시보드")
    source = st.radio("데이터 선택", ("가상 예시 CSV", "CSV 업로드", SYNTHETIC_SOURCE), horizontal=True, key="data_source")
    snapshot_date = SYNTHETIC_SNAPSHOT_DATE if source == SYNTHETIC_SOURCE else None
    today = korea_today()
    default_date = snapshot_date or today
    date_scope = source

    # 예시 파일과 업로드 파일을 같은 검증 함수에 전달합니다.
    if source == "CSV 업로드":
        uploaded_file = st.file_uploader("가상의 업무 요청 CSV 파일", type="csv")
        if uploaded_file is None:
            st.stop()
        date_scope = f"{source}:{uploaded_file.file_id}"
        csv_name = uploaded_file.name
        content = uploaded_file.getvalue()
    else:
        csv_name = SYNTHETIC_FILENAME if snapshot_date else "sample_requests.csv"
        sample_path = DATA_DIR / csv_name
        try:
            content = sample_path.read_bytes()
        except OSError:
            st.error(f"예시 파일을 읽을 수 없습니다. data/{csv_name} 파일을 확인해 주세요.")
            st.stop()

    try:
        requests = load_requests(content)
    except CsvValidationError as error:
        st.error(str(error))
        st.stop()

    if requests.empty:
        st.info("CSV에 표시할 업무가 없습니다. 헤더 아래에 업무를 추가해 주세요.")
        show_insights([], csv_name, [], [], default_date, snapshot_date)
        st.stop()

    st.success("CSV 검증을 통과했습니다.")
    department_filter, status_filter = st.columns(2)
    departments = department_filter.multiselect("담당 부서", sorted(requests["department"].unique()))
    status_options = [status for status in VALID_STATUSES if status in requests["status"].values]
    statuses = status_filter.multiselect("업무 상태", status_options)
    st.caption("필터에서 선택하지 않은 항목은 전체를 포함합니다.")

    reference_date = default_date
    # 데이터마다 위젯 상태를 구분하고, 날짜 입력을 숨기면 선택 날짜를 초기화합니다.
    if st.checkbox("과거 기준일 선택", key=f"custom_date:{date_scope}"):
        reference_date = st.date_input(
            "분석 기준일", value=default_date, max_value=today, key=f"reference_date:{date_scope}"
        )
        st.caption("CSV에 기록된 업무 상태와 필터 결과를 유지하고, 마감일과 비교하는 날짜만 바꿉니다.")

    # 같은 기준일로 계산한 지연 표시를 KPI, 지연율, 목록에 함께 사용합니다.
    requests = mark_overdue(requests, reference_date)
    filtered = filter_requests(requests, departments, statuses)
    kpis = calculate_kpis(filtered)
    total, completion_rate, average_days, overdue_count = kpis
    # 두 비율의 지연 건수는 같고, 비교하는 업무 범위만 다릅니다.
    unfinished_count = int(filtered["status"].isin(("대기", "진행 중")).sum())
    unfinished_overdue_rate = f"{100 * overdue_count / unfinished_count:.1f}%" if unfinished_count else "—"
    total_overdue_share = f"{100 * overdue_count / total:.1f}%" if total else "—"

    st.caption(f"기준일 {reference_date:%Y-%m-%d} (한국 시간)")
    if snapshot_date and reference_date > snapshot_date:
        st.warning(
            "**데이터 기준일 안내**\n\n"
            "**주의: 이 데이터에는 2026년 6월 30일 이후의 업무 완료 내역이 반영되어 있지 않습니다.**\n\n"
            "아래 지연 수치는 6월 30일 당시 미완료였던 업무를 대상으로, 선택한 기준일까지 마감일이 지난 건수를 "
            "다시 계산한 결과입니다. 실제 최신 업무 현황과 다를 수 있습니다.\n\n"
            f"분석 기준일 {reference_date:%Y-%m-%d} (한국 시간)",
            icon="⚠️",
        )
    total_card, rate_card, duration_card, overdue_card = st.columns(4)
    total_card.metric("전체 업무 수", f"{total:,}건")
    rate_card.metric("완료율", "—" if completion_rate is None else f"{completion_rate:.1f}%")
    duration_card.metric("평균 처리 기간", "—" if average_days is None else f"{average_days:.1f}일")
    overdue_card.metric("현재 지연 업무 수", f"{overdue_count:,}건")
    unfinished_card, total_share_card = st.columns(2)
    unfinished_card.metric("미완료 업무 중 지연 비율", unfinished_overdue_rate)
    unfinished_card.caption(f"현재 지연 {overdue_count:,}건 / 미완료 {unfinished_count:,}건")
    unfinished_card.caption("아직 끝나지 않은 업무 중 마감일이 지난 비율입니다.")
    total_share_card.metric("전체 업무 중 지연 비중", total_overdue_share)
    total_share_card.caption(f"현재 지연 {overdue_count:,}건 / 전체 {total:,}건")
    total_share_card.caption("완료 업무까지 포함한 전체 업무에서 현재 지연 업무가 차지하는 비중입니다.")

    if filtered.empty:
        st.info("선택한 필터에 해당하는 업무가 없습니다.")
        show_insights([], csv_name, departments, statuses, reference_date, snapshot_date)
        st.stop()

    # 차트와 요약에 같은 집계값을 전달합니다.
    totals, by_status = summarize_workload(filtered)
    department_delays = summarize_department_delays(filtered)
    monthly = summarize_monthly_trends(filtered)
    small_sample = (
        department_delays["unfinished"].between(1, 9).any()
        or department_delays["completed"].between(1, 9).any()
        or monthly["completed"].between(1, 9).any()
    )
    show_insights(
        generate_insights(kpis, totals, department_delays, monthly),
        csv_name, departments, statuses, reference_date, snapshot_date, small_sample,
    )
    department_order = totals["department"].tolist()
    chart_labels = {"count": "업무 건수", "department": "담당 부서", "status": "업무 상태"}
    status_chart = px.bar(
        by_status, x="count", y="department", color="status", orientation="h",
        labels=chart_labels, title="부서별 업무량과 상태", barmode="overlay",
        category_orders={"status": list(VALID_STATUSES)},
        color_discrete_map={"대기": SOFT_ROSE, "진행 중": MUTED_BLUE, "완료": DEEP_MAUVE},
    )
    counts_by_department = totals.set_index("department")["count"]
    segment_starts = dict.fromkeys(department_order, 0)
    for trace in tuple(status_chart.data):
        # 상태 구간의 시작점을 앞 구간의 합으로 지정해 둥근 조각을 실제 건수 축 위에 배치합니다.
        starts = []
        for department, count in zip(trace.y, trace.x, strict=True):
            starts.append(segment_starts[department])
            segment_starts[department] += int(count)
        trace.base = starts
        trace.width = 0.39
        trace.showlegend = False
        trace.customdata = [
            [100 * count / counts_by_department[department]]
            for department, count in zip(trace.y, trace.x, strict=True)
        ]
        trace.hovertemplate = f"<b>%{{y}}</b><br>{trace.name} %{{x:,}}건 · 부서 내 %{{customdata[0]:.1f}}%<extra></extra>"
        trace.marker.line = dict(width=2, color="#FFFFFF")
        status_chart.add_trace(go.Scatter(
            x=[None], y=[None], mode="markers", name=trace.name, legendgroup=trace.legendgroup,
            marker=dict(color=trace.marker.color, size=9, symbol="circle"),
            showlegend=True, hoverinfo="skip",
        ))
    for department, count in totals[["department", "count"]].itertuples(index=False, name=None):
        status_chart.add_annotation(
            x=0, xref="paper", y=department, yshift=18, xanchor="left", yanchor="bottom",
            showarrow=False, text=escape(department), font=dict(size=14, weight=500),
        )
        status_chart.add_annotation(
            x=1, xref="paper", y=department, yshift=18, xanchor="right", yanchor="bottom",
            showarrow=False, text=f"{count:,}", font=dict(size=13, color="#665D65"),
        )
    status_chart.update_xaxes(range=[0, int(totals["count"].max())], visible=False, fixedrange=True)
    status_chart.update_yaxes(
        visible=False, fixedrange=True, type="category", categoryorder="array",
        categoryarray=department_order, range=[len(department_order) - 0.5, -0.68],
    )
    status_chart.update_layout(height=124 + 61 * len(department_order))
    with st.container(border=True):
        show_chart(status_chart, horizontal=True, legend=True, bar_radius=5, card=True)

    show_department_delays(department_delays, department_order, snapshot_date)
    show_monthly_trends(monthly, snapshot_date)

    st.subheader("현재 지연 업무")
    # KPI와 같은 지연 표시를 사용하고, 마감일이 가장 오래된 업무부터 보여줍니다.
    overdue = filtered.loc[filtered["is_overdue"]].sort_values(["due_at", "request_id"])
    if overdue.empty:
        st.info("선택한 범위에 현재 지연 업무가 없습니다.")
    else:
        st.dataframe(
            overdue[["request_id", "department", "title", "status", "due_at"]],
            hide_index=True,
            column_config={
                "request_id": "업무 ID",
                "department": "담당 부서",
                "title": "업무명",
                "status": "업무 상태",
                "due_at": st.column_config.DateColumn("마감일", format="YYYY-MM-DD"),
            },
        )

    st.subheader("데이터 미리보기")
    # 표에서 날짜는 CSV와 같은 형식으로 표시하고, 비어 있는 완료일은 빈칸으로 둡니다.
    preview = filtered.copy()
    for column in ("requested_at", "due_at", "completed_at"):
        preview[column] = preview[column].dt.strftime("%Y-%m-%d").fillna("")
    st.dataframe(preview, hide_index=True)
