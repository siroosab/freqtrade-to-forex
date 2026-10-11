import pandas as pd
import pytest
import talib.abstract as ta

from freqtrade.forex.features import add_chained_ema_indicators


def test_add_chained_ema_indicators_uses_fixed_first_period_and_counts_later_emas() -> None:
    dataframe = pd.DataFrame(
        {"close": [1.0, 2.0, 3.0, 4.0, 5.0, 4.0, 3.0, 2.0, 1.0]}
    )

    result = add_chained_ema_indicators(dataframe, ema_count=3, timeperiod=3)

    expected_ema_2 = pd.Series(ta.EMA(dataframe["close"], timeperiod=2), name="ema_x_2")
    expected_ema_3 = pd.Series(ta.EMA(expected_ema_2, timeperiod=3), name="ema_x_3")
    expected_ema_4 = pd.Series(ta.EMA(expected_ema_3, timeperiod=3), name="ema_x_4")
    pd.testing.assert_series_equal(result["ema_x_2"], expected_ema_2)
    pd.testing.assert_series_equal(result["ema_x_3"], expected_ema_3)
    pd.testing.assert_series_equal(result["ema_x_4"], expected_ema_4)
    assert result["count_hi"].tolist() == [0, 0, 0, 0, 0, 0, 2, 2, 2]
    assert result["count_low"].tolist() == [0, 0, 0, 1, 1, 2, 0, 0, 0]


def test_add_chained_ema_indicators_supports_only_the_first_ema() -> None:
    dataframe = pd.DataFrame({"close": [1.0, 2.0, 3.0]})

    result = add_chained_ema_indicators(dataframe, ema_count=1, timeperiod=3)

    assert "ema_x_2" in result
    assert "ema_x_3" not in result
    assert result["count_hi"].tolist() == [0, 0, 0]
    assert result["count_low"].tolist() == [0, 0, 0]


@pytest.mark.parametrize(
    ("ema_count", "timeperiod", "message"),
    [
        (0, 3, "ema_count must be a positive integer"),
        (3, 0, "timeperiod must be a positive integer"),
    ],
)
def test_add_chained_ema_indicators_rejects_non_positive_parameters(
    ema_count: int,
    timeperiod: int,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        add_chained_ema_indicators(
            pd.DataFrame({"close": [1.0, 2.0, 3.0]}),
            ema_count=ema_count,
            timeperiod=timeperiod,
        )
