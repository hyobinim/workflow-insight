"""고정된 배정과 난수 시드로 WorkFlow Insight 합성 CSV를 만듭니다."""

import argparse
import csv
from collections import deque
from datetime import date, timedelta
from pathlib import Path
from random import Random


SEED = 20260630
YEAR = 2026
SNAPSHOT = date(YEAR, 6, 30)
DAY = timedelta(days=1)
OUTPUT = Path(__file__).resolve().parents[1] / "data" / "synthetic_requests_2026_h1.csv"
COLUMNS = ("request_id", "department", "title", "status", "requested_at", "due_at", "completed_at")

# 부서별 완료, 완료 지연, 미완료, 현재 지연 건수와 가상의 업무 유형입니다.
DEPARTMENTS = {
    "운영팀": (115, 22, 35, 22, ("운영 절차 수정", "공용 양식 관리")),
    "시스템팀": (100, 29, 30, 22, ("테스트 계정 발급", "설정 오류 확인")),
    "지원팀": (100, 11, 15, 7, ("공용 장비 요청", "문서 권한 정리")),
    "콘텐츠팀": (78, 17, 22, 15, ("안내문 수정", "교육 자료 검토")),
    "데이터팀": (75, 16, 15, 9, ("집계 기준 확인", "데이터 품질 점검")),
    "기획팀": (60, 10, 15, 9, ("요청 요건 정리", "일정 검토")),
}
# 각 행은 요청월 완료 건수, 진행 중, 대기, 완료월 완료 건수, 완료월 완료 지연 건수입니다.
MONTHS = (
    (86, 3, 1, 76, 12),
    (89, 4, 2, 89, 15),
    (89, 7, 4, 87, 16),
    (89, 10, 6, 91, 18),
    (90, 18, 12, 92, 20),
    (85, 37, 28, 93, 24),
)
TARGET_DAYS = (6, 7, 7, 8, 8, 9)


def date_between(rng, first, last):
    """양 끝 날짜를 포함해 하루를 무작위로 고릅니다."""
    return first + rng.randint(0, (last - first).days) * DAY


def completed_dates(rng, request_month, completion_month, duration):
    """요청월과 완료월을 모두 지키는 요청일과 완료일을 고릅니다."""
    period = duration * DAY
    first = max(date(YEAR, request_month, 1), date(YEAR, completion_month, 1) - period)
    last = min(date(YEAR, request_month + 1, 1), date(YEAR, completion_month + 1, 1) - period) - DAY
    requested = date_between(rng, first, last)
    return requested, requested + period


def make_rows():
    rng = Random(SEED)
    completed_pools = {False: [], True: []}
    unfinished_pools = {False: [], True: []}
    for department, (completed, late, unfinished, overdue, _) in DEPARTMENTS.items():
        completed_pools[True].extend([department] * late)
        completed_pools[False].extend([department] * (completed - late))
        unfinished_pools[True].extend([department] * overdue)
        unfinished_pools[False].extend([department] * (unfinished - overdue))
    for pool in (*completed_pools.values(), *unfinished_pools.values()):
        rng.shuffle(pool)

    rows = []
    pending_completions = deque()
    same_day_added = False
    deadline_day_added = False
    for month, (cohort_completed, in_progress, waiting, month_completed, month_late) in enumerate(MONTHS, 1):
        # 먼저 접수된 코호트부터 완료월 물량을 채워 월을 넘긴 완료를 만듭니다.
        pending_completions.extend([month] * cohort_completed)
        request_months = [pending_completions.popleft() for _ in range(month_completed)]
        late_flags = [True] * month_late + [False] * (month_completed - month_late)
        rng.shuffle(late_flags)
        for request_month, late in zip(request_months, late_flags, strict=True):
            department = completed_pools[late].pop()
            target = TARGET_DAYS[month - 1]
            offset = 1 if department == "데이터팀" else -1 if department == "지원팀" else 0
            duration = target + offset + rng.randint(-2, 2)
            # 경계 사례는 같은 달 완료 업무에 배정합니다.
            if not late and request_month == month:
                if not same_day_added:
                    duration = 0
                    same_day_added = True
                elif not deadline_day_added:
                    deadline_day_added = True
                    requested, finished = completed_dates(rng, request_month, month, duration)
                    rows.append((department, "완료", requested, finished, finished))
                    continue
            requested, finished = completed_dates(rng, request_month, month, duration)
            if late:
                due = finished - rng.randint(1, min(duration, 4)) * DAY
            else:
                if department == "데이터팀":
                    slack = rng.randint(max(0, 7 - duration), 21 - duration)
                elif department == "지원팀":
                    slack = rng.randint(max(0, 2 - duration), max(0, 7 - duration))
                else:
                    slack = rng.randint(0, 5)
                due = finished + slack * DAY
            rows.append((department, "완료", requested, due, finished))

        # 기준일 당일 또는 이후에 마감되는 미완료 48건은 6월 접수에 배정합니다.
        states = ["진행 중"] * in_progress + ["대기"] * waiting
        rng.shuffle(states)
        for index, status in enumerate(states):
            upcoming = month == 6 and index < 48
            department = unfinished_pools[not upcoming].pop()
            if upcoming:
                due = SNAPSHOT if index < 8 else SNAPSHOT + rng.randint(1, 21) * DAY
                first = max(date(YEAR, month, 1), due - 21 * DAY)
                requested = date_between(rng, first, min(SNAPSHOT, due - 2 * DAY))
            else:
                lead = rng.randint(2, 7) if department == "지원팀" else rng.randint(7, 21)
                first = date(YEAR, month, 1)
                last = min(date(YEAR, month + 1, 1) - DAY, SNAPSHOT - (lead + 1) * DAY)
                requested = date_between(rng, first, last)
                due = requested + lead * DAY
            rows.append((department, status, requested, due, None))

    # 요청일 순서로 정렬한 뒤 고유 ID를 붙입니다.
    rows.sort(key=lambda row: row[2])
    for number, (department, status, requested, due, finished) in enumerate(rows, 1):
        title = rng.choice(DEPARTMENTS[department][4])
        yield (f"SYN-{number:04d}", department, title, status, requested, due, finished)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT, help="출력 CSV 경로")
    output = parser.parse_args().output
    with output.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file, lineterminator="\n")
        writer.writerow(COLUMNS)
        writer.writerows(make_rows())
    print(f"Generated: {output.name}")


if __name__ == "__main__":
    main()
