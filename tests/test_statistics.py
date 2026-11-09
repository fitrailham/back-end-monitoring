from datetime import datetime, timedelta, timezone

import pytest

from app.errors import ApiException
from app.services import statistics_service as svc

START = datetime(2026, 11, 8, tzinfo=timezone.utc)


def samples(start=START, minutes=60, baseline=100):
    return [{"plant_code": "A", "recorded_at": start + timedelta(minutes=i),
             "total_yield_kwh": baseline + i / 5, "yield_today_kwh": i / 5,
             "timestamp_source": "device", "data_source": "FUSIONSOLAR", "id": i}
            for i in range(0, minutes + 1, 10)]


def test_interval_energy_is_difference_not_sum_or_last_counter():
    data = svc.interval_statistics(["A"], START, START + timedelta(hours=1), "hour", "UTC", samples(), START + timedelta(days=1))
    bucket = data["buckets"][0]
    assert bucket["yield_kwh"] == 12
    assert bucket["quality"] == "derived" and data["complete"] is True
    assert bucket["received_samples"] == bucket["expected_samples"] == 6


def test_missing_bucket_is_null_and_future_is_pending():
    data = svc.interval_statistics(["A"], START, START + timedelta(hours=3), "hour", "UTC", samples(), START + timedelta(hours=2))
    first, missing, future = data["buckets"]
    assert first["quality"] == "derived"
    assert missing["yield_kwh"] is None and missing["quality"] == "missing"
    assert future["quality"] == "pending" and future["expected_samples"] == 0
    assert future["coverage"]["missing"] == 0


def test_collector_time_does_not_create_verified_energy():
    data = samples()
    for row in data:
        row["timestamp_source"] = "collector"
    bucket = svc.interval_statistics(["A"], START, START + timedelta(hours=1), "hour", "UTC", data, START + timedelta(days=1))["buckets"][0]
    assert bucket["yield_kwh"] is None and bucket["coverage"]["unverified"] == 1


def test_counter_reset_and_gap_are_marked_without_negative_or_interpolated_energy():
    data = samples()
    for row in data[3:]:
        row["total_yield_kwh"] -= 100
    bucket = svc.interval_statistics(["A"], START, START + timedelta(hours=1), "hour", "UTC", data, START + timedelta(days=1))["buckets"][0]
    assert bucket["quality"] == "partial" and bucket["yield_kwh"] == 10
    assert "COUNTER_RESET" in bucket["reason"]
    gapped = [samples()[0], samples()[-1]]
    bucket = svc.interval_statistics(["A"], START, START + timedelta(hours=1), "hour", "UTC", gapped, START + timedelta(days=1))["buckets"][0]
    assert bucket["yield_kwh"] is None and "SAMPLE_GAP" in bucket["reason"]


def test_daily_counter_reset_not_used_as_interval_production():
    data = samples()
    for row in data:
        row["total_yield_kwh"] = None
    data[-1]["yield_today_kwh"] = 0
    result = svc.interval_statistics(["A"], START, START + timedelta(hours=1), "hour", "UTC", data, START + timedelta(days=1))
    assert result["buckets"][0]["yield_kwh"] is None


def test_duplicate_timestamp_does_not_double_count_and_missing_plant_makes_partial():
    data = samples()
    data.insert(1, dict(data[0]))
    result = svc.interval_statistics(["A", "B"], START, START + timedelta(hours=1), "hour", "UTC", data, START + timedelta(days=1))
    bucket = result["buckets"][0]
    assert bucket["yield_kwh"] == 12 and bucket["quality"] == "partial"
    assert bucket["coverage"]["valid"] == 1 and bucket["coverage"]["missing"] == 1


def test_utc_hour_boundaries_and_dst_repeated_hour():
    start = datetime(2026, 11, 1, 4, tzinfo=timezone.utc)
    end = start + timedelta(hours=5)
    data = svc.interval_statistics([], start, end, "hour", "America/New_York", [], end)
    assert len(data["buckets"]) == 5
    assert data["buckets"][1]["label"] == data["buckets"][2]["label"] == "01:00"
    labels = [b["label_at"] for b in data["buckets"]]
    assert "T01:00:00-04:00" in labels[1] and "T01:00:00-05:00" in labels[2]


@pytest.mark.parametrize("grain, start, end, count", [
    ("day", "2026-12-31T00:00:00+08:00", "2027-01-02T00:00:00+08:00", 2),
    ("month", "2026-12-01T00:00:00+08:00", "2027-02-01T00:00:00+08:00", 2),
    ("year", "2025-01-01T00:00:00+08:00", "2027-01-01T00:00:00+08:00", 2),
])
def test_calendar_month_and_year_boundaries(grain, start, end, count):
    start, end = datetime.fromisoformat(start), datetime.fromisoformat(end)
    data = svc.interval_statistics([], start, end, grain, "Asia/Makassar", [], end)
    assert len(data["buckets"]) == count
    assert data["buckets"][0]["start_at"] == start and data["buckets"][-1]["end_at"] == end


@pytest.mark.parametrize("start,end,zone,grain", [
    (START.replace(tzinfo=None), START + timedelta(hours=1), "UTC", "hour"),
    (START, START, "UTC", "hour"),
    (START, START + timedelta(hours=1), "WIB", "hour"),
    (START, START + timedelta(days=32), "UTC", "hour"),
])
def test_invalid_ranges_fail_explicitly(start, end, zone, grain):
    with pytest.raises(ApiException) as error:
        svc.interval_statistics([], start, end, grain, zone, [])
    assert error.value.status == 422
