# pragma pylint: disable=missing-docstring, invalid-name, pointless-string-statement
# flake8: noqa: F401
# isort: skip_file
# --- Do not remove these imports ---
import logging

import numpy as np
import pandas as pd
from datetime import datetime, timedelta, timezone
from pandas import DataFrame
from typing import Optional, Union

from freqtrade.enums import RunMode
from freqtrade.strategy.hyperopt_summary_logger import log_hyperopt_summary
import json
from pathlib import Path

from freqtrade.strategy import (
    IStrategy,
    Trade,
    Order,
    PairLocks,
    informative,  # @informative decorator
    # Hyperopt Parameters
    BooleanParameter,
    CategoricalParameter,
    DecimalParameter,
    IntParameter,
    RealParameter,
    # timeframe helpers
    timeframe_to_minutes,
    timeframe_to_next_date,
    timeframe_to_prev_date,
    # Strategy helper functions
    merge_informative_pair,
    stoploss_from_absolute,
    stoploss_from_open,
)

# --------------------------------
# Add your lib to import here
import talib.abstract as ta
from technical import qtpylib
logger = logging.getLogger(__name__)

# This class is a sample. Feel free to customize it.
class SampleStrategy(IStrategy):
    position_adjustment_enable = True
    """
    This is a sample strategy to inspire you.
    More information in https://www.freqtrade.io/en/stable/strategy-customization/

    You can:
        :return: a Dataframe with all mandatory indicators for the strategies
    - Rename the class name (Do not forget to update class_name)
    - Add any methods you want to build your strategy
    - Add any lib you need to build your strategy

    You must keep:
    - the lib in the section "Do not remove these libs"
    - the methods: populate_indicators, populate_entry_trend, populate_exit_trend
    You should keep:
    - timeframe, minimal_roi, stoploss, trailing_*
    """

    # Strategy interface version - allow new iterations of the strategy interface.
    # Check the documentation or the Sample strategy to get the latest version.
    INTERFACE_VERSION = 3

    # Can this strategy go short?
    can_short: bool = True

    # Minimal ROI designed for the strategy.
    # This attribute will be overridden if the config file contains "minimal_roi".
    minimal_roi = {
        # "120": 0.0,  # exit after 120 minutes at break even
        "6000000": 0.00,
        "900": 0.01,
        "300": 0.02,
        "30": 0.04,
        "0": 0.05,
    }

    # Optimal stoploss designed for the strategy.
    # This attribute will be overridden if the config file contains "stoploss".
    stoploss = -0.50

    # Trailing stoploss
    trailing_stop = True
    trailing_only_offset_is_reached = True
    trailing_stop_positive =  0.012
    trailing_stop_positive_offset = 0.03  # Disabled / not configured

    # Optimal timeframe for the strategy.
    timeframe = "15m"

    # Run "populate_indicators()" only for new candle.
    process_only_new_candles = True

    # These values can be overridden in the config.
    use_exit_signal = True
    exit_profit_only = True
    ignore_roi_if_entry_signal = False

    # Hyperoptable parameters
    buy_rsi = IntParameter(low=12, high=55, default=10, space="buy", optimize=True, load=True)
    sell_rsi = IntParameter(low=45, high=85, default=90, space="buy", optimize=True, load=True)
    #short
    min_streak = CategoricalParameter([25, 55, 100, 125, 150], default=25, space="buy", optimize=True, load=True)
    #long
    max_streak = CategoricalParameter([25, 55, 100, 125, 150], default=25, space="buy", optimize=True, load=True)
    
    startup_candle_count: int = 999
    
    # Optional order type mapping.
    order_types = {
        "entry": "limit",
        "exit": "limit",
        "stoploss": "market",
        "stoploss_on_exchange": False,
    }

    # Optional order time in force.
    order_time_in_force = {"entry": "GTC", "exit": "GTC"}

    plot_config = {
        "main_plot": {
            "tema": {},
            "sar": {"color": "white"},
        },
        "subplots": {
            "MACD": {
                "macd": {"color": "blue"},
                "macdsignal": {"color": "orange"},
            },
            "RSI": {
                "rsi": {"color": "red"},
            },
        },
    }
    
    def informative_pairs(self):
        """
        Define additional, informative pair/interval combinations to be cached from the exchange.
        These pair/interval combinations are non-tradeable, unless they are part
        of the whitelist as well.
        For more information, please consult the documentation
        :return: List of tuples in the format (pair, interval)
            Sample: return [("ETH/USDT", "5m"),
                            ("BTC/USDT", "15m"),
                            ]
        """
        return []
    def get_last_order_price_change_percent(self, trade: Trade, current_rate: float, lev_prc: bool = False) -> float:
            """
            Compares the 'price' in the last order with the input current_rate and returns the percent change.
            If is_short is True, uses a special formula. Optionally multiplies by leverage if lev_prc is True.
            Args:
                trade (Trade): The trade object.
                current_rate (float): The current rate to compare with last order price.
                lev_prc (bool): If True, multiply percent_change by leverage.
            Returns:
                float: Percent change (positive means current_rate > last order price), or None if not available.
            """
            trade_json = trade.to_json()
            orders = trade_json.get('orders', [])
            if not orders or not isinstance(orders, list):
                return None
            last_order = orders[-1]
            last_price = last_order.get('price')
            is_short = trade_json.get('is_short', False)
            leverage = trade_json.get('leverage', 1)
            if last_price is None or current_rate is None:
                return None
            try:
                if is_short:
                    # Per your request, this always yields 0
                    percent_change = ((last_price - current_rate) / current_rate) * 100
                else:
                    percent_change = ((current_rate - last_price) / last_price) * 100
                if lev_prc:
                    percent_change *= leverage
                return percent_change
            except Exception as e:
                logger.error(f"Error calculating percent change for last order price: {e}")
                return None
    def get_minutes_since_last_order_filled(self, trade: Trade, current_time: datetime,orders_current=None) -> float:
            """
            Returns the difference in minutes between current_time and the 'order_filled_date' of the last order in trade.to_json().
            Args:
                trade (Trade): The trade object.
                current_time (datetime): The current datetime (may be offset-aware).
            Returns:
                float: The difference in minutes, or None if not available or parse error.
            """
            if orders_current is None:
                orders_current = -1
            trade_json = trade.to_json()
            orders = trade_json.get('orders', [])
            if not orders or not isinstance(orders, list):
                return None
            last_order = orders[orders_current]
            order_filled_date = last_order.get('order_filled_date')
            if not order_filled_date:
                return None
            try:
                from datetime import datetime as dt
                filled_dt = dt.strptime(order_filled_date, '%Y-%m-%d %H:%M:%S')
                # Make both datetimes offset-naive for subtraction
                if current_time.tzinfo is not None and current_time.utcoffset() is not None:
                    current_time_naive = current_time.replace(tzinfo=None)
                else:
                    current_time_naive = current_time
                diff = current_time_naive - filled_dt
                return diff.total_seconds() / 60.0
            except Exception as e:
                logger.error(f"Error parsing order_filled_date or calculating minutes: {e}")
                return None
    def leverage(
            self,
            pair: str,
            current_time: "datetime",
            current_rate: float,
            proposed_leverage: float,
            max_leverage: float,
            side: str,
            **kwargs,
        ) -> float: 
        return 3.0
    '''
    def custom_stake_amount(
            self,
            pair: str,
            current_time: datetime,
            current_rate: float,
            proposed_stake: float,
            min_stake: Optional[float],
            max_stake: float,
            leverage: float,
            entry_tag: Optional[str],
            side: str,
            **kwargs,
        ) -> float:
          usdt_total = self.wallets.get_total("USDT")
          
          return min(max(usdt_total/ 3, 10),15)#5*self.Adjust_Value.value
    '''
    '''
    @informative('1d')
    def populate_indicators_1d(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        #dataframe = self.indicators.calculate_indicators(dataframe)
        dataframe['rsi'] = ta.RSI(dataframe['close'], timeperiod=3)

        dataframe['X1'] = ta.EMA(dataframe['close'], timeperiod=3)
        dataframe['X2'] = ta.EMA(dataframe['X1'], timeperiod=5)
        dataframe['X3'] = ta.EMA(dataframe['X2'], timeperiod=7)
        dataframe['X4'] = ta.EMA(dataframe['X3'], timeperiod=9)
        dataframe['X5'] = ta.EMA(dataframe['X4'], timeperiod=11)
        dataframe['X6'] = ta.EMA(dataframe['X5'], timeperiod=13)
        dataframe['X7'] = ta.EMA(dataframe['X6'], timeperiod=15)
        
        
        
        
        return dataframe
    '''
    '''
    def adjust_trade_position(self, trade: Trade, current_time: datetime,
                              current_rate: float, current_profit: float,
                              min_stake: float | None, max_stake: float,
                              current_entry_rate: float, current_exit_rate: float,
                              current_entry_profit: float, current_exit_profit: float,
                              **kwargs
                              ) -> float | None | tuple[float | None, str | None]:
        dataframe, _ = self.dp.get_analyzed_dataframe(trade.pair,self.timeframe)
        last_candle = dataframe.iloc[-1]
        shift_candle = dataframe.iloc[-2]
        Rsi= last_candle["rsi"]
        Maxi= last_candle["max"]#long condition
        Mini= last_candle["min"]#short condition

        if  trade.is_short and Mini == False:
            return None
        if  not trade.is_short and Maxi == False:
            return None
        
        Rsi_shift= shift_candle["rsi"]
        current_profit=current_profit*100

        
        
        
        usdt_total = self.wallets.get_total("USDT")
        stak_total = self.wallets.get_total(trade.pair.split("/")[0])

        percent_change = self.get_last_order_price_change_percent(trade, current_rate, lev_prc=False)
        minutes_since_last_order = self.get_minutes_since_last_order_filled(trade, current_time)
        stak_value_Doller = round((trade.amount*current_rate)/ trade.leverage)
        #logger.info(f"Rsi_1h: {Rsi_1h}")
        
           
        if minutes_since_last_order is None or minutes_since_last_order < self.best_time_to_adjust.value:
            #logger.info(f"for {trade.pair} \nMinutes since last order filled: {minutes_since_last_order:.2f} minutes")
            return None
           
        if percent_change is not None and percent_change < self.percent_change_for_adj.value :#and Maxi==True
            usdt_free = self.wallets.get_free("USDT")
            
            #logger.info(f"for {trade.pair} \nusdt_free: {usdt_free:.2f}$\nstak_value_Doller:{stak_value_Doller}\n---------")
            
            return (min(max(stak_value_Doller/ 4, 10),25) ), "half_profit_2%"
            #return None

        
       

        
        return None  
        #return -(trade.stake_amount / 2), "half_profit_5%"
        #return None
    '''
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        pair = metadata.get("pair")
        buy_rsi = self._get_pair_param(pair, "buy", "buy_rsi", self.buy_rsi.value)
        dataframe["ZZZbuy_rsi"] = buy_rsi
        #logger.info(f"Pair: {pair}, Buy RSI: {buy_rsi}")

        summary = log_hyperopt_summary(self, dataframe, metadata)
        if summary:
            logger.info(summary)
        """
        Adds several different TA indicators to the given DataFrame

        Performance Note: For the best performance be frugal on the number of indicators
        you are using. Let uncomment only the indicator you are using in your strategies
        or your hyperopt configuration, otherwise you will waste your memory and CPU usage.
        :param dataframe: Dataframe with data from the exchange
        :param metadata: Additional information, like the currently traded pair
        :return: a Dataframe with all mandatory indicators for the strategies
        """

        # Momentum Indicators
        # ------------------------------------
        
        # ADX
        dataframe["adx"] = ta.ADX(dataframe)

        # # Plus Directional Indicator / Movement
        # dataframe['plus_dm'] = ta.PLUS_DM(dataframe)
        # dataframe['plus_di'] = ta.PLUS_DI(dataframe)

        # # Minus Directional Indicator / Movement
        # dataframe['minus_dm'] = ta.MINUS_DM(dataframe)
        # dataframe['minus_di'] = ta.MINUS_DI(dataframe)

        # # Aroon, Aroon Oscillator
        # aroon = ta.AROON(dataframe)
        # dataframe['aroonup'] = aroon['aroonup']
        # dataframe['aroondown'] = aroon['aroondown']
        # dataframe['aroonosc'] = ta.AROONOSC(dataframe)

        # # Awesome Oscillator
        # dataframe['ao'] = qtpylib.awesome_oscillator(dataframe)

        # # Keltner Channel
        # keltner = qtpylib.keltner_channel(dataframe)
        # dataframe["kc_upperband"] = keltner["upper"]
        # dataframe["kc_lowerband"] = keltner["lower"]
        # dataframe["kc_middleband"] = keltner["mid"]
        # dataframe["kc_percent"] = (
        #     (dataframe["close"] - dataframe["kc_lowerband"]) /
        #     (dataframe["kc_upperband"] - dataframe["kc_lowerband"])
        # )
        # dataframe["kc_width"] = (
        #     (dataframe["kc_upperband"] - dataframe["kc_lowerband"]) / dataframe["kc_middleband"]
        # )

        # # Ultimate Oscillator
        # dataframe['uo'] = ta.ULTOSC(dataframe)

        # # Commodity Channel Index: values [Oversold:-100, Overbought:100]
        # dataframe['cci'] = ta.CCI(dataframe)

        

        # # Inverse Fisher transform on RSI: values [-1.0, 1.0] (https://goo.gl/2JGGoy)
        # rsi = 0.1 * (dataframe['rsi'] - 50)
        # dataframe['fisher_rsi'] = (np.exp(2 * rsi) - 1) / (np.exp(2 * rsi) + 1)

        # # Inverse Fisher transform on RSI normalized: values [0.0, 100.0] (https://goo.gl/2JGGoy)
        # dataframe['fisher_rsi_norma'] = 50 * (dataframe['fisher_rsi'] + 1)

        # # Stochastic Slow
        # stoch = ta.STOCH(dataframe)
        # dataframe['slowd'] = stoch['slowd']
        # dataframe['slowk'] = stoch['slowk']

        # Stochastic Fast
        stoch_fast = ta.STOCHF(dataframe)
        dataframe["fastd"] = stoch_fast["fastd"]
        dataframe["fastk"] = stoch_fast["fastk"]

        # # Stochastic RSI
        # Please read https://github.com/freqtrade/freqtrade/issues/2961 before using this.
        # STOCHRSI is NOT aligned with tradingview, which may result in non-expected results.
        # stoch_rsi = ta.STOCHRSI(dataframe)
        # dataframe['fastd_rsi'] = stoch_rsi['fastd']
        # dataframe['fastk_rsi'] = stoch_rsi['fastk']

        # MACD
        macd = ta.MACD(dataframe)
        dataframe["macd"] = macd["macd"]
        dataframe["macdsignal"] = macd["macdsignal"]
        dataframe["macdhist"] = macd["macdhist"]

        # MFI
        dataframe["mfi"] = ta.MFI(dataframe)

        # # ROC
        # dataframe['roc'] = ta.ROC(dataframe)

        # Overlap Studies
        # ------------------------------------

        # Bollinger Bands
        bollinger = qtpylib.bollinger_bands(qtpylib.typical_price(dataframe), window=20, stds=2)
        dataframe["bb_lowerband"] = bollinger["lower"]
        dataframe["bb_middleband"] = bollinger["mid"]
        dataframe["bb_upperband"] = bollinger["upper"]
        dataframe["bb_percent"] = (dataframe["close"] - dataframe["bb_lowerband"]) / (
            dataframe["bb_upperband"] - dataframe["bb_lowerband"]
        )
        dataframe["bb_width"] = (dataframe["bb_upperband"] - dataframe["bb_lowerband"]) / dataframe[
            "bb_middleband"
        ]

        # Bollinger Bands - Weighted (EMA based instead of SMA)
        # weighted_bollinger = qtpylib.weighted_bollinger_bands(
        #     qtpylib.typical_price(dataframe), window=20, stds=2
        # )
        # dataframe["wbb_upperband"] = weighted_bollinger["upper"]
        # dataframe["wbb_lowerband"] = weighted_bollinger["lower"]
        # dataframe["wbb_middleband"] = weighted_bollinger["mid"]
        # dataframe["wbb_percent"] = (
        #     (dataframe["close"] - dataframe["wbb_lowerband"]) /
        #     (dataframe["wbb_upperband"] - dataframe["wbb_lowerband"])
        # )
        # dataframe["wbb_width"] = (
        #     (dataframe["wbb_upperband"] - dataframe["wbb_lowerband"]) /
        #     dataframe["wbb_middleband"]
        # )

        # # EMA - Exponential Moving Average
        # dataframe['ema3'] = ta.EMA(dataframe, timeperiod=3)
        # dataframe['ema5'] = ta.EMA(dataframe, timeperiod=5)
        # dataframe['ema10'] = ta.EMA(dataframe, timeperiod=10)
        dataframe['ema25'] = ta.EMA(dataframe, timeperiod=15)
        dataframe['ema50'] = ta.EMA(dataframe, timeperiod=30)
        # dataframe['ema100'] = ta.EMA(dataframe, timeperiod=100)

               

        # # SMA - Simple Moving Average
        # dataframe['sma3'] = ta.SMA(dataframe, timeperiod=3)
        #dataframe['sma5'] = ta.SMA(dataframe, timeperiod=5)
        #dataframe['sma10'] = ta.SMA(dataframe, timeperiod=10)
        #dataframe['sma25'] = ta.SMA(dataframe, timeperiod=25)
        #dataframe['sma50'] = ta.SMA(dataframe, timeperiod=50)
        # dataframe['sma100'] = ta.SMA(dataframe, timeperiod=100)

        # Parabolic SAR
        dataframe["sar"] = ta.SAR(dataframe)

        # TEMA - Triple Exponential Moving Average
        dataframe["tema"] = ta.TEMA(dataframe, timeperiod=9)

        # Cycle Indicator
        # ------------------------------------
        # Hilbert Transform Indicator - SineWave
        hilbert = ta.HT_SINE(dataframe)
        dataframe["htsine"] = hilbert["sine"]
        dataframe["htleadsine"] = hilbert["leadsine"]

        # Pattern Recognition - Bullish candlestick patterns
        # ------------------------------------
        # # Hammer: values [0, 100]
        # dataframe['CDLHAMMER'] = ta.CDLHAMMER(dataframe)
        # # Inverted Hammer: values [0, 100]
        # dataframe['CDLINVERTEDHAMMER'] = ta.CDLINVERTEDHAMMER(dataframe)
        # # Dragonfly Doji: values [0, 100]
        # dataframe['CDLDRAGONFLYDOJI'] = ta.CDLDRAGONFLYDOJI(dataframe)
        # # Piercing Line: values [0, 100]
        # dataframe['CDLPIERCING'] = ta.CDLPIERCING(dataframe) # values [0, 100]
        # # Morningstar: values [0, 100]
        # dataframe['CDLMORNINGSTAR'] = ta.CDLMORNINGSTAR(dataframe) # values [0, 100]
        # # Three White Soldiers: values [0, 100]
        # dataframe['CDL3WHITESOLDIERS'] = ta.CDL3WHITESOLDIERS(dataframe) # values [0, 100]

        # Pattern Recognition - Bearish candlestick patterns
        # ------------------------------------
        # # Hanging Man: values [0, 100]
        # dataframe['CDLHANGINGMAN'] = ta.CDLHANGINGMAN(dataframe)
        # # Shooting Star: values [0, 100]
        # dataframe['CDLSHOOTINGSTAR'] = ta.CDLSHOOTINGSTAR(dataframe)
        # # Gravestone Doji: values [0, 100]
        # dataframe['CDLGRAVESTONEDOJI'] = ta.CDLGRAVESTONEDOJI(dataframe)
        # # Dark Cloud Cover: values [0, 100]
        # dataframe['CDLDARKCLOUDCOVER'] = ta.CDLDARKCLOUDCOVER(dataframe)
        # # Evening Doji Star: values [0, 100]
        # dataframe['CDLEVENINGDOJISTAR'] = ta.CDLEVENINGDOJISTAR(dataframe)
        # # Evening Star: values [0, 100]
        # dataframe['CDLEVENINGSTAR'] = ta.CDLEVENINGSTAR(dataframe)

        # Pattern Recognition - Bullish/Bearish candlestick patterns
        # ------------------------------------
        # # Three Line Strike: values [0, -100, 100]
        # dataframe['CDL3LINESTRIKE'] = ta.CDL3LINESTRIKE(dataframe)
        # # Spinning Top: values [0, -100, 100]
        # dataframe['CDLSPINNINGTOP'] = ta.CDLSPINNINGTOP(dataframe) # values [0, -100, 100]
        # # Engulfing: values [0, -100, 100]
        # dataframe['CDLENGULFING'] = ta.CDLENGULFING(dataframe) # values [0, -100, 100]
        # # Harami: values [0, -100, 100]
        # dataframe['CDLHARAMI'] = ta.CDLHARAMI(dataframe) # values [0, -100, 100]
        # # Three Outside Up/Down: values [0, -100, 100]
        # dataframe['CDL3OUTSIDE'] = ta.CDL3OUTSIDE(dataframe) # values [0, -100, 100]
        # # Three Inside Up/Down: values [0, -100, 100]
        # dataframe['CDL3INSIDE'] = ta.CDL3INSIDE(dataframe) # values [0, -100, 100]

        # # Chart type
        # # ------------------------------------
        # # Heikin Ashi Strategy
        heikinashi = qtpylib.heikinashi(dataframe)
        # dataframe['ha_open'] = heikinashi['open']
        dataframe['ha_close'] = heikinashi['close']
        # dataframe['ha_high'] = heikinashi['high']
        # dataframe['ha_low'] = heikinashi['low']

        # RSI
                
        dataframe["rsi"] = ta.RSI(dataframe)
        dataframe["rsi_ha"] = ta.RSI(dataframe['ha_close'], timeperiod=14)
        
        dataframe['rsi_sma_ha'] = ta.SMA(dataframe["rsi_ha"], timeperiod=14)
        
        dataframe["rsi_ha_sma_rounded"] = round(dataframe['rsi_sma_ha'])

        # Retrieve best bid and best ask from the orderbook
        # ------------------------------------
        """
        # first check if dataprovider is available
        if self.dp:
            if self.dp.runmode.value in ('live', 'dry_run'):
                ob = self.dp.orderbook(metadata['pair'], 1)
                dataframe['best_bid'] = ob['bids'][0][0]
                dataframe['best_ask'] = ob['asks'][0][0]
        
        """
        
        df_informpair = self.resample_live_informpair(dataframe, "4h")
        df_informpair['emaX3_1d'] = ta.EMA(df_informpair, timeperiod=10)

        # مرج به دیتافریم 15 دقیقه‌ای اصلی، بدون look-ahead
        dataframe = pd.merge_asof(
            dataframe.sort_values('date'),
            df_informpair[['date', 'emaX3_1d']].sort_values('date'),
            on='date',
            direction='backward'
        )
#==============================================================
        df_informpair['emaX2_1d'] = ta.EMA(df_informpair, timeperiod=7)
        # مرج به دیتافریم 15 دقیقه‌ای اصلی، بدون look-ahead
        dataframe = pd.merge_asof(
            dataframe.sort_values('date'),
            df_informpair[['date', 'emaX2_1d']].sort_values('date'),
            on='date',
            direction='backward'
        )
#==============================================================
        df_informpair['emaX1_1d'] = ta.EMA(df_informpair, timeperiod=5)
        # مرج به دیتافریم 15 دقیقه‌ای اصلی، بدون look-ahead
        dataframe = pd.merge_asof(
            dataframe.sort_values('date'),
            df_informpair[['date', 'emaX1_1d']].sort_values('date'),
            on='date',
            direction='backward'
        )
#==============================================================
        #dataframe['max'] = (dataframe['X4_1d']>dataframe['X5_1d'])&(dataframe['X5_1d']>dataframe['X6_1d'])
        #dataframe['min'] = (dataframe['X4_1d']<dataframe['X5_1d'])&(dataframe['X5_1d']<dataframe['X6_1d'])
        
        dataframe['max'] = (dataframe['emaX1_1d']>dataframe['emaX2_1d'])&(dataframe['emaX2_1d']>dataframe['emaX3_1d'])
        dataframe['min'] = (dataframe['emaX1_1d']<dataframe['emaX2_1d'])&(dataframe['emaX2_1d']<dataframe['emaX3_1d'])

        # یک سری موقت که فقط در لحظه True شدن max یا min مقدار می‌گیرد
        last_signal = pd.Series(np.nan, index=dataframe.index)
        last_signal[dataframe['max']] = -1
        last_signal[dataframe['min']] = 1

        # مقادیر خالی (nan) رو با آخرین مقدار معتبر قبلی پر می‌کنیم
        last_signal = last_signal.ffill()

        # مقدار پیش‌فرض صفر
        dataframe['max_or_min'] = 0

        # فقط جایی که هر دو False هستند از last_signal استفاده می‌کنیم
        both_false = (~dataframe['max']) & (~dataframe['min'])
        dataframe.loc[both_false, 'max_or_min'] = last_signal[both_false]

        # تبدیل به int در صورت نیاز
        dataframe['max_or_min'] = dataframe['max_or_min'].fillna(0).astype(int)
        
                 
        dataframe['max_streak'] = self.consecutive_count(dataframe['max'])
        dataframe['min_streak'] = self.consecutive_count(dataframe['min'])
        
        return dataframe
    def consecutive_count(self,series: pd.Series) -> pd.Series:
        groups = (~series).cumsum()
        counts = series.groupby(groups).cumsum()
        return counts
    def confirm_trade_exit(self, pair: str, trade: 'Trade', order_type: str, amount: float,
                          rate: float, time_in_force: str, exit_reason: str,
                          current_time: 'datetime', **kwargs) -> bool:
        profit_percent = trade.calc_profit_ratio(rate) * 100
        exit_signals = ['long_exit_tag', 'short_exit_tag']
        if exit_reason in exit_signals and profit_percent < 2:
            logger.info(f"Exit for {pair} rejected due to profit < 2% (profit: {profit_percent:.2f}%)")
            return False
        return True
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._pair_params = self._load_pair_params_from_json()

    def _load_pair_params_from_json(self) -> dict:
        json_path = Path(__file__).with_suffix(".json")
        if not json_path.exists():
            return {}

        try:
            with json_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("params", {})
        except (json.JSONDecodeError, OSError):
            return {}
    def _get_pair_param(
        self,
        pair: Optional[str],
        group: str,
        name: str,
        default: Union[int, float],
    ):
        runmode = self.config.get("runmode") if hasattr(self, "config") else None
        if runmode in (RunMode.HYPEROPT):  # if runmode in (RunMode.HYPEROPT, RunMode.BACKTEST):
            return default
        if pair is None:
            return default

        pair_cfg = self._pair_params.get("pairs", {}).get(pair, {})
        value = pair_cfg.get(group, {}).get(name)
        if value is None:
            value = self._pair_params.get(group, {}).get(name, default)
        return value

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        Based on TA indicators, populates the entry signal for the given dataframe
        :param dataframe: DataFrame
        :param metadata: Additional information, like the currently traded pair
        :return: DataFrame with entry columns populated
        """
        pair = metadata.get("pair")
        buy_rsi = self._get_pair_param(pair, "buy", "buy_rsi", self.buy_rsi.value)
        max_streak = self._get_pair_param(pair, "buy", "max_streak", self.max_streak.value)

        min_streak = self._get_pair_param(pair, "sell", "min_streak", self.min_streak.value)
        sell_rsi = self._get_pair_param(pair, "sell", "sell_rsi", self.sell_rsi.value)

        mask_long_enter_1 = (
            
                # Signal: RSI crosses above 30
                #(qtpylib.crossed_above(dataframe["htleadsine"],dataframe["htsine"]))
                
                (dataframe["rsi_ha_sma_rounded"].shift(1) <= buy_rsi)
                &(dataframe["rsi_ha_sma_rounded"] > buy_rsi)
                &(dataframe['max_streak']<max_streak)
                #&((dataframe['max']==True)|(dataframe['max_or_min']==1))
                &(dataframe['max_or_min']==1)
                & (dataframe["volume"] > 0)  # Make sure Volume is not 0
            )
        dataframe.loc[mask_long_enter_1, 'enter_long'] = 1
        dataframe.loc[mask_long_enter_1, 'enter_tag'] = 'Long_sig_1'#بازار افزایشی
            
        mask_long_enter_2 = (             
                (dataframe['max_or_min'].shift(1)==0)
                &(dataframe['max_or_min']==1)
                & (dataframe["close"] > dataframe["ema50"])
                & (dataframe["volume"] > 0)  # Make sure Volume is not 0
                    )
        dataframe.loc[mask_long_enter_2, 'enter_long'] = 1
        dataframe.loc[mask_long_enter_2, 'enter_tag'] = 'Long_sig_2'#بازار افزایشی

        mask_long_enter_3 = (              
                    (dataframe['min'].shift(1)==True)
                    &(dataframe['max']==True)
                    & (dataframe["volume"] > 0)  # Make sure Volume is not 0
                    )
        dataframe.loc[mask_long_enter_3, 'enter_long'] = 1
        dataframe.loc[mask_long_enter_3, 'enter_tag'] = 'Long_sig_3'#بازار افزایشی

        mask_short_enter_1 = (
            
                # Signal: RSI crosses above 70
                #(qtpylib.crossed_above(dataframe["rsi"], self.short_rsi.value))
                #& (dataframe["tema"] > dataframe["bb_middleband"])  # Guard: tema above BB middle
                #& (dataframe["tema"] < dataframe["tema"].shift(1))  # Guard: tema is falling
                #& (dataframe["volume"] > 0)  # Make sure Volume is not 0
                (dataframe["rsi_ha_sma_rounded"].shift(1) >= sell_rsi)
                &(dataframe["rsi_ha_sma_rounded"] < sell_rsi)
                #&((dataframe['min']==True)|(dataframe['max_or_min']==-1))
                &(dataframe['max_or_min']==-1)
                &(dataframe['min_streak']<min_streak)
                
                & (dataframe["volume"] > 0)  # Make sure Volume is not 0
            )
        dataframe.loc[mask_short_enter_1, 'enter_short'] = 1
        dataframe.loc[mask_short_enter_1, 'enter_tag'] = 'Shrt_sig_1'#بازار افزایشی

        mask_short_enter_2 = (
                    (dataframe['max_or_min'].shift(1)==0)
                    &(dataframe['max_or_min']==-1)
                    & (dataframe["close"] < dataframe["ema50"])                      
                    & (dataframe["volume"] > 0)  # Make sure Volume is not 0
                    )
        dataframe.loc[mask_short_enter_2, 'enter_short'] = 1
        dataframe.loc[mask_short_enter_2, 'enter_tag'] = 'Shrt_sig_2'#بازار افزایشی

        mask_short_enter_3 = (
                    (dataframe['max'].shift(1)==True)
                    &(dataframe['min']==True)       
                        &(dataframe["volume"] > 0)  # Make sure Volume is not 0
                     )
        dataframe.loc[mask_short_enter_3, 'enter_short'] = 1
        dataframe.loc[mask_short_enter_3, 'enter_tag'] = 'Shrt_sig_3'#بازار افزایشی

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        Based on TA indicators, populates the exit signal for the given dataframe
        :param dataframe: DataFrame
        :param metadata: Additional information, like the currently traded pair
        :return: DataFrame with exit columns populated
        """
        

        mask_long_exit = (
            (qtpylib.crossed_above(dataframe["ema50"], dataframe["ema25"]))
            #(dataframe["rsi"].shift(1) >= self.sell_rsi.value)    
            #&(dataframe["rsi"] < self.sell_rsi.value)
            # (dataframe["volume"] < 0)
            & (dataframe["volume"] > 0)  # Make sure Volume is not 0
            )
        dataframe.loc[mask_long_exit, 'exit_long'] = 1
        dataframe.loc[mask_long_exit, 'exit_tag'] = 'crossed_above'

        mask_short_exit = (
                    (qtpylib.crossed_below(dataframe["ema50"], dataframe["ema25"]))
                    
                    #(dataframe["rsi"].shift(1) >= self.sell_rsi.value)    
                    #&(dataframe["rsi"] < self.sell_rsi.value)
                    # (dataframe["volume"] < 0)
                    & (dataframe["volume"] > 0)  # Make sure Volume is not 0
                    )
        dataframe.loc[mask_short_exit, 'exit_short'] = 1
        dataframe.loc[mask_short_exit, 'exit_tag'] = 'crossed_below'

        return dataframe
    
    def resample_live_informpair(self, dataframe: pd.DataFrame , timeframe: str) -> pd.DataFrame:
        df = dataframe.copy().set_index('date')

        ohlc_dict = {
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum',
        }

        resampled = (
            df.resample(timeframe, label='left', closed='left')
              .agg(ohlc_dict)
        )
        # کندل‌های خالی (بدون دیتا) رو حذف کن ولی آخرین کندل ناقص رو نگه دار
        resampled.dropna(subset=['open'], inplace=True)
        resampled.reset_index(inplace=True)
        return resampled


