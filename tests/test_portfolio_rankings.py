from datetime import date, datetime, timedelta, timezone

from app.services import fleet_service, portfolio_service
from app.repositories import fleet_repository
from test_frontend_api import sample_row


def test_rankings_same_period_stable_ties_and_exclusions(monkeypatch):
    start = datetime(2026, 11, 8, tzinfo=timezone.utc)
    rows = [sample_row(code, code) for code in ("B", "A", "ZERO", "MISSING")]
    rows[2]["capacity_kwp"] = 0
    readings = [{"plant_code": code, "recorded_at": start + timedelta(minutes=10*i),
                 "total_yield_kwh": 1000 + i, "timestamp_source": "device", "data_source": "FUSIONSOLAR"}
                for code in ("A", "B") for i in range(145)]
    monkeypatch.setattr(fleet_service, "get_rows", lambda: rows)
    monkeypatch.setattr(fleet_repository, "counter_samples", lambda *args: readings)
    monkeypatch.setattr(portfolio_service, "utc_now", lambda: start + timedelta(days=2))
    result = portfolio_service.rankings(date(2026, 11, 8), "UTC", 1, "specific_energy")
    assert result["eligible_plants"] == 2 and result["excluded_plants"] == 2
    assert result["total"] == 2 and len(result["data"]) == 1
    assert result["data"][0]["plant_code"] == "A" and result["data"][0]["yield_kwh"] == 144
    assert result["exclusion_reasons"] == {"CAPACITY_MISSING_OR_NON_POSITIVE": 1, "INCOMPLETE_PERIOD": 1}


def test_current_date_ranking_requires_comparable_verified_cutoff(monkeypatch):
    start = datetime(2026, 11, 8, tzinfo=timezone.utc)
    now = start + timedelta(minutes=30)
    rows = [sample_row("A"), sample_row("B")]
    readings = [{"plant_code": code, "recorded_at": start + timedelta(minutes=10*i),
                 "total_yield_kwh": 1000 + i, "timestamp_source": "device", "data_source": "FUSIONSOLAR"}
                for code in ("A", "B") for i in range(4)]
    monkeypatch.setattr(fleet_service, "get_rows", lambda: rows)
    monkeypatch.setattr(fleet_repository, "counter_samples", lambda *args: readings)
    monkeypatch.setattr(portfolio_service, "utc_now", lambda: now)
    result = portfolio_service.rankings(date(2026, 11, 8), "UTC", 5, "yield")
    assert result["eligible_plants"] == 2
    assert all(row["yield_kwh"] == 3 for row in result["data"])
