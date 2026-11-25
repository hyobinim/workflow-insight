import csv
from datetime import date, datetime
from io import StringIO
from zoneinfo import ZoneInfo

import pandas as pd


REQUIRED_COLUMNS = (
    "request_id", "department", "title", "status",
    "requested_at", "due_at", "completed_at",
)
VALID_STATUSES = ("대기", "진행 중", "완료")


class CsvValidationError(ValueError):
    """화면에서 사용자가 읽을 수 있는 CSV 오류입니다."""


def load_requests(content: bytes) -> pd.DataFrame:
    """CSV를 검증하고 날짜 컬럼을 변환한 데이터프레임을 반환합니다."""
    try:
        csv_text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise CsvValidationError("CSV를 UTF-8 인코딩으로 다시 저장해 주세요.") from error

    # CSV 파서로 따옴표가 포함된 값을 읽고, 오류 안내에 실제 시작 줄을 사용합니다.
    reader = csv.reader(StringIO(csv_text, newline=""), strict=True)
    rows = []
    line_numbers = []
    try:
        header = next(reader, [])
        if not header:
            raise CsvValidationError("1행에 CSV 컬럼명을 입력해 주세요.")
        missing_columns = [column for column in REQUIRED_COLUMNS if column not in header]
        if missing_columns:
            raise CsvValidationError(f"1행에 필수 컬럼을 추가해 주세요. 누락: {', '.join(missing_columns)}")
        if len(set(header)) != len(header):
            raise CsvValidationError("1행에서 중복된 컬럼명을 찾아 하나만 남겨 주세요.")

        while True:
            line_number = reader.line_num + 1
            row = next(reader, None)
            if row is None:
                break
            if not row:
                continue
            if len(row) != len(header):
                raise CsvValidationError(
                    f"{line_number}행의 값 개수가 컬럼 개수와 다릅니다. 쉼표와 따옴표를 확인해 주세요."
                )
            rows.append(row)
            line_numbers.append(line_number)
    except csv.Error as error:
        raise CsvValidationError(f"{reader.line_num}행 근처의 CSV 따옴표를 확인해 주세요.") from error

    # 문자열로 읽어 업무 ID 앞자리의 0을 보존합니다.
    data = pd.DataFrame(rows, columns=header, index=line_numbers, dtype="string")
    data = data.loc[:, list(REQUIRED_COLUMNS)].apply(lambda column: column.str.strip())

    def check(invalid: pd.Series, column: str, guidance: str) -> None:
        if invalid.any():
            line_number = data.index[invalid][0]
            raise CsvValidationError(f"{line_number}행 `{column}` — {guidance}")

    for column in REQUIRED_COLUMNS[:-1]:
        check(data[column].eq(""), column, "필수 값을 입력해 주세요.")
    check(data["request_id"].duplicated(), "request_id", "중복된 ID입니다. 업무마다 고유한 ID를 입력해 주세요.")
    check(~data["status"].isin(VALID_STATUSES), "status", "대기, 진행 중, 완료 중 하나를 입력해 주세요.")

    completed = data["status"].eq("완료")
    no_completion_date = data["completed_at"].eq("")
    check(completed & no_completion_date, "completed_at", "완료 업무의 완료일을 입력해 주세요.")
    check(~completed & ~no_completion_date, "completed_at", "대기 또는 진행 중인 업무의 완료일은 비워 주세요.")

    # 날짜의 표기와 달력상 유효성을 함께 확인한 후 날짜 타입으로 바꿉니다.
    for column in ("requested_at", "due_at", "completed_at"):
        source = data[column]
        parsed = pd.to_datetime(source, format="%Y-%m-%d", errors="coerce")
        invalid_date = ~source.str.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}") | parsed.isna()
        check(source.ne("") & invalid_date, column, "유효한 날짜를 YYYY-MM-DD 형식으로 입력해 주세요.")
        data[column] = parsed

    check(data["due_at"] < data["requested_at"], "due_at", "마감일은 요청일과 같거나 이후여야 합니다.")
    check(data["completed_at"] < data["requested_at"], "completed_at", "완료일은 요청일과 같거나 이후여야 합니다.")
    return data.reset_index(drop=True)


def korea_today(now: datetime | None = None) -> date:
    """실행 환경의 기본 시간대와 관계없이 서울의 오늘 날짜를 구합니다."""
    korea = ZoneInfo("Asia/Seoul")
    return datetime.now(korea).date() if now is None else now.astimezone(korea).date()


def filter_requests(
    data: pd.DataFrame, departments: list[str], statuses: list[str]
) -> pd.DataFrame:
    """선택이 비어 있으면 전체를 포함하고, 선택한 조건은 모두 적용합니다."""
    filtered = data
    if departments:
        filtered = filtered.loc[filtered["department"].isin(departments)]
    if statuses:
        filtered = filtered.loc[filtered["status"].isin(statuses)]
    return filtered


def mark_overdue(data: pd.DataFrame, reference_date: date) -> pd.DataFrame:
    """마감일이 기준일보다 이전인 미완료 업무를 Boolean 값으로 표시합니다."""
    # 날짜만 비교하므로 마감일 당일에는 지연으로 표시하지 않습니다.
    past_due = data["due_at"] < pd.Timestamp(reference_date)
    unfinished = data["status"].isin(("대기", "진행 중"))
    return data.assign(is_overdue=past_due & unfinished)


def calculate_kpis(filtered: pd.DataFrame) -> tuple[int, float | None, float | None, int]:
    """한 필터 결과에서 업무 수, 완료율, 처리 기간, 현재 지연 건수를 계산합니다."""
    total = len(filtered)
    completed = filtered.loc[filtered["status"].eq("완료")]
    completion_rate = 100 * len(completed) / total if total else None

    # 날짜를 빼면 달력 일수가 나옵니다. 당일 완료는 0일로 평균에 포함됩니다.
    durations = (completed["completed_at"] - completed["requested_at"]).dt.days
    average_days = float(durations.mean()) if not completed.empty else None
    overdue_count = int(filtered["is_overdue"].sum())
    return total, completion_rate, average_days, overdue_count


def summarize_workload(filtered: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """담당 부서별 상태 건수와 전체 건수를 구합니다."""
    # 부서와 상태가 같은 행을 세고, 상태별 건수를 더해 부서 전체 건수를 구합니다.
    by_status = filtered.groupby(["department", "status"]).size().reset_index(name="count")
    totals = by_status.groupby("department", as_index=False)["count"].sum()
    totals = totals.sort_values(["count", "department"], ascending=[False, True])
    return totals, by_status


def summarize_department_delays(filtered: pd.DataFrame) -> pd.DataFrame:
    """같은 필터 결과에서 담당 부서별 지연 건수와 비율을 구합니다."""
    completed = filtered["status"].eq("완료")
    # 현재 지연 표시는 기준일에 맞춰 이미 계산했습니다. 완료 지연은 두 날짜로 판정합니다.
    counts = filtered.assign(
        completed=completed,
        completed_late=completed & filtered["completed_at"].gt(filtered["due_at"]),
    ).groupby("department", as_index=False).agg(
        total=("request_id", "size"),
        completed=("completed", "sum"),
        overdue=("is_overdue", "sum"),
        completed_late=("completed_late", "sum"),
    )
    counts["unfinished"] = counts["total"] - counts["completed"]
    # 분모 0은 결측값으로 남겨 화면에서 '—'로 표현할 수 있게 합니다.
    counts["overdue_rate"] = 100 * counts["overdue"] / counts["unfinished"].replace(0, float("nan"))
    counts["completed_late_rate"] = 100 * counts["completed_late"] / counts["completed"].replace(0, float("nan"))
    return counts


def summarize_monthly_trends(filtered: pd.DataFrame) -> pd.DataFrame:
    """요청월과 완료월 기준 집계를 합쳐 월마다 한 행을 만듭니다."""
    request_month = filtered["requested_at"].dt.to_period("M")
    requested = filtered.groupby(request_month).size().rename("requested")

    # 완료 지표는 완료 업무만 골라 실제 완료일이 속한 월에 배정합니다.
    completed = filtered.loc[filtered["status"].eq("완료")]
    completion_month = completed["completed_at"].dt.to_period("M")
    completed_monthly = completed.assign(
        processing_days=(completed["completed_at"] - completed["requested_at"]).dt.days,
        completed_late=completed["completed_at"].gt(completed["due_at"]),
    ).groupby(completion_month).agg(
        completed=("request_id", "size"),
        average_days=("processing_days", "mean"),
        completed_late=("completed_late", "sum"),
    )
    monthly = pd.concat([requested, completed_monthly], axis=1)
    if not monthly.empty:
        months = pd.period_range(monthly.index.min(), monthly.index.max(), freq="M")
        monthly = monthly.reindex(months)

    # 비어 있는 달도 유지합니다. 건수만 0으로 채우고 완료가 없으면 평균과 비율은 비워 둡니다.
    count_columns = ["requested", "completed", "completed_late"]
    monthly[count_columns] = monthly[count_columns].fillna(0).astype(int)
    denominator = monthly["completed"].where(monthly["completed"].ne(0))
    monthly["completed_late_rate"] = 100 * monthly["completed_late"] / denominator
    monthly.index = monthly.index.astype(str)
    return monthly.rename_axis("month").reset_index()


def generate_insights(
    kpis: tuple[int, float | None, float | None, int],
    workload: pd.DataFrame,
    department_delays: pd.DataFrame,
    monthly: pd.DataFrame,
) -> list[tuple[str, str, str]]:
    """이미 계산한 지표로 주제, 근거 수치, 해석을 만듭니다."""
    total, completion_rate, _, overdue = kpis
    if total == 0:
        return []

    completed = int(department_delays["completed"].sum())
    unfinished = int(department_delays["unfinished"].sum())
    insights = [
        ("전체 업무 현황", f"전체 {total:,}건 · 완료 {completed:,}건 · 완료율 {completion_rate:.1f}%",
         "선택한 업무 중 CSV에 완료로 기록된 비율입니다."),
    ]
    current_rate = f"{100 * overdue / unfinished:.1f}%" if unfinished else "—"
    insights.append((
        "현재 지연 현황",
        f"현재 지연 {overdue:,}건 / 미완료 {unfinished:,}건 · {current_rate}",
        "CSV에 미완료로 기록된 업무 중 선택 기준일보다 마감일이 앞선 업무입니다."
        if unfinished else "선택 범위에 미완료 업무가 없어 비율을 계산할 수 없습니다.",
    ))

    # 업무량 차이가 충분하고 양쪽 부서가 한 곳씩일 때만 비교 문장을 만듭니다.
    if len(workload) >= 2:
        high = workload.loc[workload["count"].eq(workload["count"].max())]
        low = workload.loc[workload["count"].eq(workload["count"].min())]
        if len(high) == len(low) == 1:
            most, least = high.iloc[0], low.iloc[0]
            gap = most["count"] - least["count"]
            if gap >= 10 and 5 * gap >= least["count"]:
                insights.append((
                    "담당 부서별 업무량",
                    f"{most['department']} {most['count']:,}건 · {least['department']} {least['count']:,}건",
                    f"업무가 가장 많은 부서와 가장 적은 부서의 차이는 {gap:,}건입니다.",
                ))

    # 비율마다 분모가 다르므로 비교할 수 있는 부서도 따로 고릅니다.
    for rate, numerator, denominator, title in (
        ("overdue_rate", "overdue", "unfinished", "담당 부서별 현재 지연율"),
        ("completed_late_rate", "completed_late", "completed", "담당 부서별 완료 지연율"),
    ):
        eligible = department_delays.loc[department_delays[denominator].ge(10)]
        if len(eligible) < 2:
            continue
        high = eligible.loc[eligible[rate].eq(eligible[rate].max())]
        low = eligible.loc[eligible[rate].eq(eligible[rate].min())]
        if len(high) != 1 or len(low) != 1:
            continue
        most, least = high.iloc[0], low.iloc[0]
        gap = most[rate] - least[rate]
        # 정수 건수의 교차 곱으로 10%p 경계를 비교해 부동소수점 반올림을 피합니다.
        difference = int(most[numerator]) * int(least[denominator]) - int(least[numerator]) * int(most[denominator])
        if 10 * difference >= int(most[denominator]) * int(least[denominator]):
            evidence = " · ".join(
                f"{row['department']} 전체 {row['total']:,}건, {row[numerator]:,}/{row[denominator]:,}건 ({row[rate]:.1f}%)"
                for row in (most, least)
            )
            insights.append((title, evidence, f"비율의 분모가 10건 이상인 부서 중 가장 큰 차이는 {gap:.1f}%p입니다."))

    if len(monthly) < 2:
        return insights
    previous, latest = monthly.iloc[-2], monthly.iloc[-1]
    # 마지막 두 달을 그대로 비교합니다. 완료가 없는 달을 건너뛰지 않습니다.
    if pd.Period(previous["month"], freq="M") + 1 != pd.Period(latest["month"], freq="M"):
        return insights

    for key, title, date_label in (
        ("requested", "월별 요청량 변화", "요청일"),
        ("completed", "월별 완료량 변화", "완료일"),
    ):
        before, after = previous[key], latest[key]
        change = after - before
        if min(before, after) >= 10 and abs(change) >= 10 and 5 * abs(change) >= before:
            direction = "증가" if change > 0 else "감소"
            insights.append((
                title,
                f"{previous['month']} {before:,}건 → {latest['month']} {after:,}건",
                f"CSV의 {date_label} 기준 월별 건수가 {abs(change):,}건({100 * abs(change) / before:.1f}%) {direction}했습니다.",
            ))

    if min(previous["completed"], latest["completed"]) >= 10:
        day_gap = latest["average_days"] - previous["average_days"]
        if abs(day_gap) >= 1.0:
            evidence = " → ".join(
                f"{row['month']} {row['average_days']:.1f}일 (완료 {row['completed']:,}건)"
                for row in (previous, latest)
            )
            direction = "늘었습니다" if day_gap > 0 else "줄었습니다"
            insights.append(("월별 평균 처리 기간 변화", evidence, f"완료월 기준 평균이 {abs(day_gap):.1f}일 {direction}."))
        late_gap = latest["completed_late_rate"] - previous["completed_late_rate"]
        late_difference = int(latest["completed_late"]) * int(previous["completed"]) - int(previous["completed_late"]) * int(latest["completed"])
        if 20 * abs(late_difference) >= int(previous["completed"]) * int(latest["completed"]):
            evidence = " → ".join(
                f"{row['month']} {row['completed_late']:,}/{row['completed']:,}건 ({row['completed_late_rate']:.1f}%)"
                for row in (previous, latest)
            )
            direction = "높아졌습니다" if late_gap > 0 else "낮아졌습니다"
            insights.append(("월별 완료 지연율 변화", evidence, f"완료월 기준 지연율이 {abs(late_gap):.1f}%p {direction}."))
    return insights
