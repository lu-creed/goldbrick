"""个股列表内存筛选逻辑的单元测试（不依赖数据库）。"""

from app.services.daily_universe import DailyUniverseFilters, _item_passes_filters


def _sample_row():
    return {
        "ts_code": "600000.SH",
        "name": "浦发银行",
        "market": "主板",
        "exchange": "SSE",
        "open": 10.0,
        "high": 10.5,
        "low": 9.8,
        "close": 10.2,
        "volume": 1_000_000,
        "amount": 10_200_000.0,
        "turnover_rate": 1.25,
        "pct_change": 2.0,
    }


def test_code_contains_case_insensitive():
    row = _sample_row()
    assert _item_passes_filters(row, DailyUniverseFilters(code_contains="600000"))
    assert _item_passes_filters(row, DailyUniverseFilters(code_contains="600000.sh"))
    assert not _item_passes_filters(row, DailyUniverseFilters(code_contains="000001"))


def test_name_substring():
    row = _sample_row()
    assert _item_passes_filters(row, DailyUniverseFilters(name_contains="浦发"))
    assert not _item_passes_filters(row, DailyUniverseFilters(name_contains="万科"))


def test_pct_range_excludes_null_pct():
    row = {**_sample_row(), "pct_change": None}
    assert not _item_passes_filters(row, DailyUniverseFilters(pct_min=0.0))


def test_turnover_null_excluded_when_filter():
    row = {**_sample_row(), "turnover_rate": None}
    assert not _item_passes_filters(row, DailyUniverseFilters(turnover_min=0.1))
