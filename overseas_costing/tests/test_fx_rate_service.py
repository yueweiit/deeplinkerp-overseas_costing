"""
中文用途：付款日汇率服务测试。
"""

import json

import pytest

from overseas_costing.services import fx_rate_service


@pytest.fixture(autouse=True)
def use_plain_messages(monkeypatch):
    monkeypatch.setattr(fx_rate_service, "_", lambda message: message)


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")

    def close(self):
        pass


class FakeCurrencyExchangeRepository:
    def __init__(self, *, exact=None, latest=None):
        self.exact = dict(exact or {})
        self.latest = dict(latest or {})
        self.exact_calls = []
        self.latest_calls = []

    def find_exact_buying_rate(self, currency, rate_date):
        self.exact_calls.append((currency, rate_date))
        return self.exact.get(currency)

    def find_latest_buying_rate(self, currency, on_or_before):
        self.latest_calls.append((currency, on_or_before))
        return self.latest.get(currency)


class FakePersistentCurrencyExchangeRepository(FakeCurrencyExchangeRepository):
    def __init__(self, *, version, exact=None, latest=None):
        super().__init__(exact=exact, latest=latest)
        self.version = dict(version)
        self.inserted = []
        self.version_updates = []
        self.audit_rows = []

    def get_version_for_update(self, version_name):
        assert version_name == self.version["name"]
        return dict(self.version)

    def get_buying_rate(self, record_name):
        for row in [*self.exact.values(), *self.latest.values()]:
            if row and row.get("name") == record_name:
                return dict(row)
        return None

    def insert_api_buying_rate(self, currency, rate_date, exchange_rate):
        row = {
            "name": f"{rate_date}-{currency}-CNY-Buying",
            "date": rate_date,
            "exchange_rate": exchange_rate,
        }
        self.exact[currency] = row
        self.inserted.append((currency, rate_date, exchange_rate))
        return dict(row)

    def update_version(self, version_name, updates):
        assert version_name == self.version["name"]
        self.version.update(updates)
        self.version_updates.append(dict(updates))

    def insert_audit_log(self, **values):
        self.audit_rows.append(values)


def test_normalize_payment_date_accepts_tax_certificate_date() -> None:
    assert fx_rate_service.normalize_payment_date("01/04/2026") == "2026-04-01"
    assert fx_rate_service.normalize_payment_date("2026-08-04") == "2026-08-04"


def test_payment_date_fx_context_rejects_api_rate_from_another_day() -> None:
    def fake_opener(_request, timeout):
        assert timeout > 0
        return FakeResponse({"cnyPerUnit": 6.7, "rateDate": "2026-10-29"})

    result = fx_rate_service.build_fx_context_from_payment_date("2026-10-30", opener=fake_opener)
    assert result["ok"] is False
    assert "fx_usd_to_rmb" not in result
    assert {error["action"] for error in result["errors"]} == {"rate_date_mismatch"}


def test_resolve_fx_rate_date_prefers_real_payment_date() -> None:
    result = fx_rate_service.resolve_fx_rate_date(
        payment_date="01/04/2026",
        approval_finished_at="2026-04-21 17:16:00",
    )

    assert result["ok"] is True
    assert result["normalized_date"] == "2026-04-01"
    assert result["date_source"] == "payment_date"
    assert result["date_source_label"] == "真实付款日"
    assert result["is_estimated_rate"] is False


def test_build_fx_context_for_costing_uses_approval_finished_at_as_estimate() -> None:
    def fake_opener(request, timeout):
        if "currency=USD" in request.full_url:
            return FakeResponse({"currency": "USD", "rateDate": "2026-04-21", "cnyPerUnit": 6.9})
        if "currency=MXN" in request.full_url:
            return FakeResponse({"currency": "MXN", "rateDate": "2026-04-21", "cnyPerUnit": 0.38})
        return FakeResponse({"error": "rate_not_found", "message": "数据库中没有对应汇率"})

    result = fx_rate_service.build_fx_context_for_costing(
        payment_date="",
        approval_finished_at="2026-04-21 17:16:00",
        endpoint="http://fx.example.test/api/fx-rate",
        opener=fake_opener,
    )

    assert result["ok"] is True
    assert result["normalized_payment_date"] == ""
    assert result["normalized_approval_finished_at"] == "2026-04-21"
    assert result["normalized_fx_rate_date"] == "2026-04-21"
    assert result["fx_date_source"] == "approval_finished_at"
    assert result["fx_date_source_label"] == "付款审批完成日（暂估）"
    assert result["is_estimated_rate"] is True
    assert result["fx_usd_to_rmb"] == pytest.approx(6.9)
    assert result["fx_rmb_to_mxn"] == pytest.approx(1 / 0.38)


def test_fetch_cny_rate_uses_currency_and_payment_date() -> None:
    requested_urls = []

    def fake_opener(request, timeout):
        requested_urls.append(request.full_url)
        assert timeout == 8
        return FakeResponse(
            {
                "currency": "USD",
                "rateDate": "2026-08-04",
                "cnyPerUnit": 6.76,
                "sourceUrl": "https://example.test/fx",
                "fetchedAt": "2026-08-03 16:05:01",
            }
        )

    result = fx_rate_service.fetch_cny_rate(
        currency="美元USD",
        payment_date="2026-08-04",
        endpoint="http://fx.example.test/api/fx-rate",
        opener=fake_opener,
    )

    assert result["ok"] is True
    assert result["currency"] == "USD"
    assert result["requested_date"] == "2026-08-04"
    assert result["cny_per_unit"] == pytest.approx(6.76)
    assert "currency=USD" in requested_urls[0]
    assert "date=2026-08-04" in requested_urls[0]


def test_build_fx_context_from_payment_date_maps_legacy_version_fields() -> None:
    def fake_opener(request, timeout):
        if "currency=USD" in request.full_url:
            return FakeResponse({"currency": "USD", "rateDate": "2026-08-04", "cnyPerUnit": 6.8})
        if "currency=MXN" in request.full_url:
            return FakeResponse({"currency": "MXN", "rateDate": "2026-08-04", "cnyPerUnit": 0.4})
        return FakeResponse({"error": "rate_not_found", "message": "数据库中没有对应汇率"})

    result = fx_rate_service.build_fx_context_from_payment_date(
        "2026-08-04",
        endpoint="http://fx.example.test/api/fx-rate",
        opener=fake_opener,
    )

    assert result["ok"] is True
    assert result["fx_usd_to_rmb"] == pytest.approx(6.8)
    assert result["fx_mxn_to_rmb"] == pytest.approx(0.4)
    assert result["fx_rmb_to_mxn"] == pytest.approx(2.5)
    assert result["rate_snapshots"]["USD"]["rate_date"] == "2026-08-04"


def test_fetch_cny_rate_reports_missing_payment_date() -> None:
    result = fx_rate_service.fetch_cny_rate(currency="USD", payment_date="")

    assert result["ok"] is False
    assert result["action"] == "missing_payment_date"


def test_resolve_costing_fx_preserves_complete_version_snapshot() -> None:
    repository = FakeCurrencyExchangeRepository()

    result = fx_rate_service.resolve_costing_fx(
        version_fx={"fx_usd_to_rmb": 6.8, "fx_rmb_to_mxn": 2.5},
        calculation_date="2026-09-20",
        repository=repository,
        opener=lambda *_args, **_kwargs: pytest.fail("existing version rates must not call the API"),
    )

    assert result["ok"] is True
    assert result["fx_usd_to_rmb"] == pytest.approx(6.8)
    assert result["fx_rmb_to_mxn"] == pytest.approx(2.5)
    assert result["rates"]["USD"]["source"] == "version_snapshot"
    assert result["rates"]["MXN"]["source"] == "version_snapshot"
    assert repository.exact_calls == []
    assert repository.latest_calls == []


def test_legacy_default_rates_are_refreshed_only_when_trial_is_saved() -> None:
    repository = FakePersistentCurrencyExchangeRepository(
        version={
            "name": "VER-001", "batch": "BATCH-001", "status": "Active",
            "fx_usd_to_rmb": 7.178751, "fx_rmb_to_mxn": 2.6, "extra_json": "{}",
        },
        exact={
            "USD": {"name": "usd-rate", "date": "2026-10-30", "exchange_rate": 6.6583},
            "MXN": {"name": "mxn-rate", "date": "2026-10-30", "exchange_rate": 0.3523916886},
        },
    )
    resolution = fx_rate_service.resolve_costing_fx(
        version_fx=repository.version,
        calculation_date="2026-10-30",
        repository=repository,
        opener=lambda *_args, **_kwargs: pytest.fail("the exact database rates are available"),
    )

    assert resolution["legacy_default_currencies"] == ["USD", "MXN"]
    assert repository.version["fx_usd_to_rmb"] == 7.178751
    assert repository.version["fx_rmb_to_mxn"] == 2.6
    saved = fx_rate_service.persist_costing_fx_resolution(
        resolution=resolution, batch_name="BATCH-001", version_name="VER-001",
        current_date="2026-10-30", repository=repository,
    )
    assert saved["changed_fields"] == ["fx_usd_to_rmb", "fx_rmb_to_mxn"]
    assert repository.version["fx_usd_to_rmb"] == pytest.approx(6.6583)
    assert repository.version["fx_rmb_to_mxn"] == pytest.approx(2.837751)
    assert len(repository.audit_rows) == 2

    repository.exact = {
        "USD": {"name": "usd-next", "date": "2026-10-31", "exchange_rate": 6.7},
        "MXN": {"name": "mxn-next", "date": "2026-10-31", "exchange_rate": 0.35},
    }
    refreshed = fx_rate_service.resolve_costing_fx(
        version_fx=repository.version, calculation_date="2026-10-31",
        repository=repository,
        opener=lambda *_args, **_kwargs: pytest.fail("the next-day database rates are available"),
    )
    assert refreshed["legacy_default_currencies"] == []
    assert refreshed["stale_snapshot_rates"]["USD"] == pytest.approx(6.6583)
    assert refreshed["rates"]["USD"]["rate_date"] == "2026-10-31"
    saved_next = fx_rate_service.persist_costing_fx_resolution(
        resolution=refreshed, batch_name="BATCH-001", version_name="VER-001",
        current_date="2026-10-31", repository=repository,
    )
    assert saved_next["changed_fields"] == ["fx_usd_to_rmb", "fx_rmb_to_mxn"]
    assert repository.version["fx_usd_to_rmb"] == pytest.approx(6.7)
    assert repository.version["fx_rmb_to_mxn"] == pytest.approx(1 / 0.35, rel=1e-6)


def test_stale_fx_snapshot_rejects_concurrent_version_change(monkeypatch) -> None:
    monkeypatch.setattr(fx_rate_service, "_", lambda message: message)
    repository = FakePersistentCurrencyExchangeRepository(
        version={
            "name": "VER-001", "batch": "BATCH-001", "status": "Active",
            "fx_usd_to_rmb": 6.8, "fx_rmb_to_mxn": 2.5,
            "extra_json": json.dumps({"costing_fx_snapshot": {
                "calculation_date": "2026-10-20",
                "rates": {"USD": {"cny_per_unit": 6.8, "source": "currency_exchange"},
                          "MXN": {"cny_per_unit": 0.4, "source": "currency_exchange"}},
            }}),
        },
        exact={"USD": {"name": "usd-30", "date": "2026-10-30", "exchange_rate": 6.7},
               "MXN": {"name": "mxn-30", "date": "2026-10-30", "exchange_rate": 0.35}},
    )
    resolution = fx_rate_service.resolve_costing_fx(
        version_fx=repository.version, calculation_date="2026-10-30", repository=repository,
    )
    repository.version["fx_usd_to_rmb"] = 6.9
    with pytest.raises(ValueError, match="版本汇率已变化"):
        fx_rate_service.persist_costing_fx_resolution(
            resolution=resolution, batch_name="BATCH-001", version_name="VER-001",
            current_date="2026-10-30", repository=repository,
        )


def test_version_snapshot_with_mismatched_api_date_is_refreshed() -> None:
    repository = FakeCurrencyExchangeRepository(exact={
        "USD": {"name": "usd-30", "date": "2026-10-30", "exchange_rate": 6.7},
        "MXN": {"name": "mxn-30", "date": "2026-10-30", "exchange_rate": 0.35},
    })
    version = {
        "fx_usd_to_rmb": 6.8, "fx_rmb_to_mxn": 2.5,
        "extra_json": json.dumps({"costing_fx_snapshot": {
            "calculation_date": "2026-10-30",
            "rates": {"USD": {"cny_per_unit": 6.8, "source": "fx_api", "rate_date": "2026-10-29"},
                      "MXN": {"cny_per_unit": 0.4, "source": "fx_api", "rate_date": "2026-10-29"}},
        }}),
    }
    result = fx_rate_service.resolve_costing_fx(
        version_fx=version, calculation_date="2026-10-30", repository=repository,
    )
    assert result["stale_snapshot_rates"] == {"USD": 6.8, "MXN": 2.5}
    assert result["fx_usd_to_rmb"] == pytest.approx(6.7)
    assert result["rates"]["MXN"]["rate_date"] == "2026-10-30"


def test_resolve_costing_fx_uses_database_then_api_for_only_missing_currency() -> None:
    repository = FakeCurrencyExchangeRepository(
        exact={
            "USD": {
                "name": "2026-09-20-USD-CNY-Buying",
                "date": "2026-09-20",
                "exchange_rate": 6.71,
            }
        }
    )
    requested_urls = []

    def fake_opener(request, timeout):
        requested_urls.append(request.full_url)
        return FakeResponse(
            {
                "currency": "MXN",
                "rateDate": "2026-09-20",
                "cnyPerUnit": 0.39,
                "sourceUrl": "https://example.test/fx",
                "fetchedAt": "2026-09-20 08:00:00",
            }
        )

    result = fx_rate_service.resolve_costing_fx(
        version_fx={},
        calculation_date="2026-09-20",
        repository=repository,
        endpoint="http://fx.example.test/api/fx-rate",
        opener=fake_opener,
    )

    assert result["ok"] is True
    assert result["fx_usd_to_rmb"] == pytest.approx(6.71)
    assert result["fx_rmb_to_mxn"] == pytest.approx(2.564103)
    assert result["rates"]["USD"]["source"] == "currency_exchange"
    assert result["rates"]["MXN"]["source"] == "fx_api"
    assert result["rates"]["MXN"]["cache_pending"] is True
    assert len(requested_urls) == 1
    assert "currency=MXN" in requested_urls[0]


def test_resolve_costing_fx_falls_back_to_latest_database_rate_without_age_limit() -> None:
    repository = FakeCurrencyExchangeRepository(
        latest={
            "USD": {"name": "old-usd", "date": "2024-01-02", "exchange_rate": 6.5},
            "MXN": {"name": "old-mxn", "date": "2023-12-29", "exchange_rate": 0.4},
        }
    )

    def fake_opener(_request, timeout):
        assert timeout == 8
        return FakeResponse({"error": "rate_not_found", "message": "database miss"})

    result = fx_rate_service.resolve_costing_fx(
        version_fx={},
        calculation_date="2026-09-20",
        repository=repository,
        endpoint="http://fx.example.test/api/fx-rate",
        opener=fake_opener,
    )

    assert result["ok"] is True
    assert result["fx_usd_to_rmb"] == pytest.approx(6.5)
    assert result["fx_rmb_to_mxn"] == pytest.approx(2.5)
    assert result["is_estimated"] is True
    assert result["rates"]["USD"]["rate_date"] == "2024-01-02"
    assert result["rates"]["MXN"]["rate_date"] == "2023-12-29"
    assert result["rates"]["USD"]["source"] == "historical_currency_exchange"
    assert result["rates"]["MXN"]["source"] == "historical_currency_exchange"


def test_resolve_costing_fx_keeps_partial_api_success_and_falls_back_only_missing_currency() -> None:
    repository = FakeCurrencyExchangeRepository(
        latest={"MXN": {"name": "old-mxn", "date": "2025-06-30", "exchange_rate": 0.4}}
    )

    def fake_opener(request, timeout):
        if "currency=USD" in request.full_url:
            return FakeResponse({"currency": "USD", "rateDate": "2026-09-20", "cnyPerUnit": 6.71})
        return FakeResponse({"error": "rate_not_found", "message": "MXN unavailable"})

    result = fx_rate_service.resolve_costing_fx(
        version_fx={},
        calculation_date="2026-09-20",
        repository=repository,
        endpoint="http://fx.example.test/api/fx-rate",
        opener=fake_opener,
    )

    assert result["ok"] is True
    assert result["rates"]["USD"]["source"] == "fx_api"
    assert result["rates"]["MXN"]["source"] == "historical_currency_exchange"
    assert repository.latest_calls == [("MXN", "2026-09-20")]


def test_resolve_costing_fx_does_not_cache_api_rate_from_a_different_day_as_today() -> None:
    repository = FakeCurrencyExchangeRepository(
        exact={"USD": {"name": "usd", "date": "2026-09-20", "exchange_rate": 6.71}},
        latest={"MXN": {"name": "old-mxn", "date": "2026-09-18", "exchange_rate": 0.4}},
    )

    result = fx_rate_service.resolve_costing_fx(
        version_fx={},
        calculation_date="2026-09-20",
        repository=repository,
        endpoint="http://fx.example.test/api/fx-rate",
        opener=lambda _request, timeout: FakeResponse(
            {"currency": "MXN", "rateDate": "2026-09-19", "cnyPerUnit": 0.39}
        ),
    )

    assert result["ok"] is True
    assert result["rates"]["MXN"]["source"] == "historical_currency_exchange"
    assert result["rates"]["MXN"]["rate_date"] == "2026-09-18"
    assert result["rates"]["MXN"]["cache_pending"] is False


def test_resolve_costing_fx_returns_blocking_errors_when_no_rate_exists() -> None:
    repository = FakeCurrencyExchangeRepository()

    def fake_opener(_request, timeout):
        assert timeout == 8
        return FakeResponse({"error": "rate_not_found", "message": "database miss"})

    result = fx_rate_service.resolve_costing_fx(
        version_fx={},
        calculation_date="2026-09-20",
        repository=repository,
        endpoint="http://fx.example.test/api/fx-rate",
        opener=fake_opener,
    )

    assert result["ok"] is False
    assert result["fx_usd_to_rmb"] is None
    assert result["fx_rmb_to_mxn"] is None
    assert [row["currency"] for row in result["blocking_errors"]] == ["USD", "MXN"]


def test_persist_costing_fx_resolution_caches_api_rates_and_updates_version_snapshot() -> None:
    repository = FakePersistentCurrencyExchangeRepository(
        version={
            "name": "VER-001",
            "batch": "BATCH-001",
            "status": "Active",
            "fx_usd_to_rmb": 0,
            "fx_rmb_to_mxn": 0,
            "extra_json": '{"keep": 1}',
        }
    )
    resolution = {
        "ok": True,
        "calculation_date": "2026-09-20",
        "fx_usd_to_rmb": 6.71,
        "fx_rmb_to_mxn": 2.564103,
        "is_estimated": False,
        "blocking_errors": [],
        "rates": {
            "USD": {
                "currency": "USD", "cny_per_unit": 6.71, "source": "fx_api",
                "rate_date": "2026-09-20", "cache_pending": True,
                "source_url": "https://example.test/usd", "fetched_at": "2026-09-20 08:00:00",
            },
            "MXN": {
                "currency": "MXN", "cny_per_unit": 0.39, "source": "fx_api",
                "rate_date": "2026-09-20", "cache_pending": True,
                "source_url": "https://example.test/mxn", "fetched_at": "2026-09-20 08:00:00",
            },
        },
    }

    result = fx_rate_service.persist_costing_fx_resolution(
        resolution=resolution,
        batch_name="BATCH-001",
        version_name="VER-001",
        current_date="2026-09-20",
        repository=repository,
        operator="finance@example.com",
    )

    assert repository.inserted == [
        ("USD", "2026-09-20", 6.71),
        ("MXN", "2026-09-20", 0.39),
    ]
    assert repository.version["fx_usd_to_rmb"] == pytest.approx(6.71)
    assert repository.version["fx_rmb_to_mxn"] == pytest.approx(2.564103)
    saved_extra = json.loads(repository.version["extra_json"])
    assert saved_extra["keep"] == 1
    assert saved_extra["costing_fx_snapshot"]["calculation_date"] == "2026-09-20"
    assert saved_extra["costing_fx_snapshot"]["rates"]["MXN"]["record_name"] == "2026-09-20-MXN-CNY-Buying"
    assert [row["field_name"] for row in repository.audit_rows] == ["fx_usd_to_rmb", "fx_rmb_to_mxn"]
    assert result["changed_fields"] == ["fx_usd_to_rmb", "fx_rmb_to_mxn"]


def test_persist_costing_fx_resolution_preserves_existing_manual_values() -> None:
    repository = FakePersistentCurrencyExchangeRepository(
        version={
            "name": "VER-001", "batch": "BATCH-001", "status": "Active",
            "fx_usd_to_rmb": 6.8, "fx_rmb_to_mxn": 2.5, "extra_json": "{}",
        }
    )
    resolution = {
        "ok": True,
        "calculation_date": "2026-09-20",
        "fx_usd_to_rmb": 6.8,
        "fx_rmb_to_mxn": 2.5,
        "is_estimated": False,
        "blocking_errors": [],
        "rates": {
            "USD": {"currency": "USD", "cny_per_unit": 6.8, "source": "version_snapshot", "rate_date": ""},
            "MXN": {"currency": "MXN", "cny_per_unit": 0.4, "source": "version_snapshot", "rate_date": ""},
        },
    }

    result = fx_rate_service.persist_costing_fx_resolution(
        resolution=resolution,
        batch_name="BATCH-001",
        version_name="VER-001",
        current_date="2026-09-20",
        repository=repository,
    )

    assert result["changed_fields"] == []
    assert repository.version["fx_usd_to_rmb"] == pytest.approx(6.8)
    assert repository.version["fx_rmb_to_mxn"] == pytest.approx(2.5)
    assert repository.inserted == []
    assert repository.audit_rows == []


def test_persist_costing_fx_resolution_rejects_cross_day_preview() -> None:
    repository = FakePersistentCurrencyExchangeRepository(
        version={
            "name": "VER-001", "batch": "BATCH-001", "status": "Active",
            "fx_usd_to_rmb": 0, "fx_rmb_to_mxn": 0, "extra_json": "{}",
        }
    )
    resolution = {
        "ok": True,
        "calculation_date": "2026-09-20",
        "fx_usd_to_rmb": 6.71,
        "fx_rmb_to_mxn": 2.564103,
        "rates": {},
        "blocking_errors": [],
    }

    with pytest.raises(ValueError, match="汇率日期已变化"):
        fx_rate_service.persist_costing_fx_resolution(
            resolution=resolution,
            batch_name="BATCH-001",
            version_name="VER-001",
            current_date="2026-09-21",
            repository=repository,
        )

    assert repository.version_updates == []


def test_persist_costing_fx_resolution_rejects_database_rate_conflict() -> None:
    repository = FakePersistentCurrencyExchangeRepository(
        version={
            "name": "VER-001", "batch": "BATCH-001", "status": "Active",
            "fx_usd_to_rmb": 0, "fx_rmb_to_mxn": 0, "extra_json": "{}",
        },
        exact={
            "USD": {"name": "usd-rate", "date": "2026-09-20", "exchange_rate": 6.8},
        },
    )
    resolution = {
        "ok": True,
        "calculation_date": "2026-09-20",
        "fx_usd_to_rmb": 6.71,
        "fx_rmb_to_mxn": 2.564103,
        "rates": {
            "USD": {
                "currency": "USD", "cny_per_unit": 6.71, "source": "fx_api",
                "rate_date": "2026-09-20", "cache_pending": True,
            },
            "MXN": {
                "currency": "MXN", "cny_per_unit": 0.39, "source": "fx_api",
                "rate_date": "2026-09-20", "cache_pending": True,
            },
        },
        "blocking_errors": [],
    }

    with pytest.raises(ValueError, match="当日汇率已变化"):
        fx_rate_service.persist_costing_fx_resolution(
            resolution=resolution,
            batch_name="BATCH-001",
            version_name="VER-001",
            current_date="2026-09-20",
            repository=repository,
        )

    assert repository.version_updates == []


def test_persist_costing_fx_resolution_rejects_conflicting_concurrent_api_insert() -> None:
    class ConcurrentInsertRepository(FakePersistentCurrencyExchangeRepository):
        def insert_api_buying_rate(self, currency, rate_date, exchange_rate):
            return {
                "name": f"{rate_date}-{currency}-CNY-Buying",
                "date": rate_date,
                "exchange_rate": 6.8 if currency == "USD" else exchange_rate,
            }

    repository = ConcurrentInsertRepository(
        version={
            "name": "VER-001", "batch": "BATCH-001", "status": "Active",
            "fx_usd_to_rmb": 0, "fx_rmb_to_mxn": 0, "extra_json": "{}",
        }
    )
    resolution = {
        "ok": True,
        "calculation_date": "2026-09-20",
        "fx_usd_to_rmb": 6.71,
        "fx_rmb_to_mxn": 2.564103,
        "rates": {
            "USD": {
                "currency": "USD", "cny_per_unit": 6.71, "source": "fx_api",
                "rate_date": "2026-09-20", "cache_pending": True,
            },
            "MXN": {
                "currency": "MXN", "cny_per_unit": 0.39, "source": "fx_api",
                "rate_date": "2026-09-20", "cache_pending": True,
            },
        },
        "blocking_errors": [],
    }

    with pytest.raises(ValueError, match="并发写入.*重新预览"):
        fx_rate_service.persist_costing_fx_resolution(
            resolution=resolution,
            batch_name="BATCH-001",
            version_name="VER-001",
            current_date="2026-09-20",
            repository=repository,
        )

    assert repository.version_updates == []


def test_frappe_currency_exchange_repository_reads_buying_rates(monkeypatch) -> None:
    calls = []

    class FakeFrappe:
        @staticmethod
        def get_all(doctype, **kwargs):
            calls.append((doctype, kwargs))
            return [{"name": "RATE-1", "date": "2026-09-20", "exchange_rate": 6.71}]

    monkeypatch.setattr(fx_rate_service, "frappe", FakeFrappe)
    repository = fx_rate_service.FrappeCurrencyExchangeRepository()

    exact = repository.find_exact_buying_rate("USD", "2026-09-20")
    latest = repository.find_latest_buying_rate("MXN", "2026-09-20")

    assert exact["name"] == "RATE-1"
    assert latest["name"] == "RATE-1"
    assert calls[0][0] == "Currency Exchange"
    assert calls[0][1]["filters"] == {
        "from_currency": "USD", "to_currency": "CNY", "date": "2026-09-20", "for_buying": 1,
    }
    assert calls[1][1]["filters"]["date"] == ["<=", "2026-09-20"]
    assert calls[1][1]["order_by"] == "date desc, modified desc"


def test_frappe_currency_exchange_repository_inserts_buying_only_rate(monkeypatch) -> None:
    inserted = []

    class FakeDoc:
        name = "2026-09-20-USD-CNY-Buying"

        def __init__(self, payload):
            self.payload = payload

        def insert(self, ignore_permissions=False):
            assert ignore_permissions is True
            inserted.append(dict(self.payload))

    class FakeFrappe:
        DuplicateEntryError = type("DuplicateEntryError", (Exception,), {})

        @staticmethod
        def get_doc(payload):
            return FakeDoc(payload)

    monkeypatch.setattr(fx_rate_service, "frappe", FakeFrappe)
    repository = fx_rate_service.FrappeCurrencyExchangeRepository()
    monkeypatch.setattr(
        repository,
        "find_exact_buying_rate",
        lambda currency, rate_date: {
            "name": f"{rate_date}-{currency}-CNY-Buying",
            "date": rate_date,
            "exchange_rate": 6.71,
        },
    )

    result = repository.insert_api_buying_rate("USD", "2026-09-20", 6.71)

    assert inserted == [{
        "doctype": "Currency Exchange",
        "date": "2026-09-20",
        "from_currency": "USD",
        "to_currency": "CNY",
        "exchange_rate": 6.71,
        "for_buying": 1,
        "for_selling": 0,
    }]
    assert result["name"] == "2026-09-20-USD-CNY-Buying"
