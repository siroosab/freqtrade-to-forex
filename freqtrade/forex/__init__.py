"""Forex broker integrations and domain models."""

from freqtrade.forex.models import (
    ForexAccount,
    ForexCandle,
    ForexCurrency,
    ForexMarketSession,
    ForexQuote,
    ForexQuoteRate,
    OandaEnvironment,
    OandaInstrument,
    OandaPrice,
    OandaCandle,
    OandaOrderResult,
    OandaOrderActionResult,
)
from freqtrade.forex.config import (
    OandaSettings,
    execution_mode_for_native_runmode,
    validate_native_forex_config,
)
from freqtrade.forex.costs import FillResult, ForexFillModel, financing_cost
from freqtrade.forex.execution import (
    ExecutionMode,
    ExecutionModeError,
    ExecutionResult,
    IdempotencyError,
    OandaExecutionGateway,
)
from freqtrade.forex.exit_rules import AtrStop, FixedStop, TakeProfit, TimeExit, TrailingStop
from freqtrade.forex.features import (
    ForexFeature,
    ForexFeaturePipeline,
    ForexFreqAIAdapter,
    ForexFreqAIExecutionGate,
)
from freqtrade.forex.health import OandaHealthCheck, OandaHealthReport
from freqtrade.forex.hyperopt import ForexHyperopt, HyperoptCandidate, HyperoptResult
from freqtrade.forex.backtest import (
    BacktestPeriodSummary,
    BacktestResult,
    BacktestTrade,
    EquityPoint,
    ForexBacktester,
)
from freqtrade.forex.ledger import (
    NativeTradeOrderStore,
    PaperLedger,
    PaperOrderRecord,
    PaperPerformance,
    PaperPositionRecord,
    PaperTradeRecord,
)
from freqtrade.forex.margin import (
    MarginError,
    MarginPosition,
    MarginSnapshot,
    margin_for_position,
    margin_snapshot,
    notional_in_account_currency,
    required_margin,
    validate_exposure,
)
from freqtrade.forex.oanda import OandaClient
from freqtrade.forex.order_validation import (
    BrokerOrderValidator,
    OrderValidationError,
    ProtectiveStopPolicy,
)
from freqtrade.forex.position_semantics import (
    PositionMode,
    PositionSemanticsError,
    PositionTransition,
    apply_order,
    close_by_opposite,
)
from freqtrade.forex.provider import OandaMarketDataProvider
from freqtrade.forex.paper import DryRunSession, PaperAccountState, PaperPosition
from freqtrade.forex.practice_runs import PracticeRunRecord, PracticeRunRecorder
from freqtrade.forex.native_core import (
    ForexPricing,
    ForexWalletSnapshot,
    OandaNativeCoreBridge,
    attach_native_forex_adapters,
)
from freqtrade.forex.native_protections import (
    ForexNativeProtectionBridge,
    ForexProtectionDecision,
    ForexProtectionLimits,
)
from freqtrade.forex.strategy_loop import (
    DryRunStrategyLoop,
    EmaCrossStrategy,
    ForexStrategyAdapter,
    Signal,
    StrategyStepResult,
)
from freqtrade.forex.strategy_state import ForexStrategyState, ForexStrategyStateStore
from freqtrade.forex.runner import DryRunWorker, WorkerConfig
from freqtrade.forex.historical import HistoricalCandleStore
from freqtrade.forex.risk import (
    ForexRateBook,
    RiskSizingError,
    pip_value_per_unit,
    quote_to_account_rate,
    units_for_fixed_risk,
)
from freqtrade.forex.risk_limits import (
    RiskControl,
    RiskLimitError,
    RiskLimitPolicy,
    RiskLimits,
    RiskUsage,
    aggregate_currency_exposure,
)
from freqtrade.forex.state import OandaAccountState, OandaPosition
from freqtrade.forex.stream import OandaTransactionStream, TransactionCursorStore
from freqtrade.forex.transactions import (
    BrokerOrderStatus,
    OandaTransaction,
    OrderLifecycle,
    OrderStateMachine,
)


def __getattr__(name: str):
    """Load strategy and API exports only when callers explicitly request them."""
    if name == "ForexAIStrategyBaseline":
        from freqtrade.forex.ai_strategy import ForexAIStrategyBaseline

        return ForexAIStrategyBaseline
    if name == "ForexEmaStrategy":
        from freqtrade.forex.strategies.ema_cross import ForexEmaStrategy

        return ForexEmaStrategy
    if name == "create_app":
        from freqtrade.forex.api import create_app

        return create_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "OandaCandle",
    "OandaClient",
    "BrokerOrderValidator",
    "OrderValidationError",
    "ProtectiveStopPolicy",
    "PositionMode",
    "PositionSemanticsError",
    "PositionTransition",
    "apply_order",
    "close_by_opposite",
    "OandaMarketDataProvider",
    "DryRunSession",
    "PaperPosition",
    "PracticeRunRecord",
    "PracticeRunRecorder",
    "ForexPricing",
    "ForexWalletSnapshot",
    "OandaNativeCoreBridge",
    "attach_native_forex_adapters",
    "ForexNativeProtectionBridge",
    "ForexProtectionDecision",
    "ForexProtectionLimits",
    "PaperAccountState",
    "DryRunStrategyLoop",
    "EmaCrossStrategy",
    "ForexStrategyAdapter",
    "ForexEmaStrategy",
    "ForexStrategyState",
    "ForexStrategyStateStore",
    "Signal",
    "StrategyStepResult",
    "DryRunWorker",
    "WorkerConfig",
    "HistoricalCandleStore",
    "OandaEnvironment",
    "OandaInstrument",
    "ForexCurrency",
    "ForexQuote",
    "ForexQuoteRate",
    "ForexAccount",
    "ForexCandle",
    "ForexMarketSession",
    "OandaOrderResult",
    "OandaOrderActionResult",
    "OandaPrice",
    "OandaSettings",
    "execution_mode_for_native_runmode",
    "validate_native_forex_config",
    "FillResult",
    "ForexFillModel",
    "financing_cost",
    "OandaAccountState",
    "OandaPosition",
    "OandaTransactionStream",
    "TransactionCursorStore",
    "BrokerOrderStatus",
    "OandaTransaction",
    "OrderLifecycle",
    "OrderStateMachine",
    "ExecutionMode",
    "ExecutionModeError",
    "ExecutionResult",
    "IdempotencyError",
    "OandaExecutionGateway",
    "FixedStop",
    "AtrStop",
    "TakeProfit",
    "TrailingStop",
    "TimeExit",
    "ForexFeature",
    "ForexFeaturePipeline",
    "ForexFreqAIAdapter",
    "ForexFreqAIExecutionGate",
    "ForexAIStrategyBaseline",
    "OandaHealthCheck",
    "OandaHealthReport",
    "ForexHyperopt",
    "HyperoptCandidate",
    "HyperoptResult",
    "create_app",
    "BacktestResult",
    "BacktestTrade",
    "BacktestPeriodSummary",
    "EquityPoint",
    "ForexBacktester",
    "PaperLedger",
    "NativeTradeOrderStore",
    "PaperTradeRecord",
    "PaperPerformance",
    "PaperOrderRecord",
    "PaperPositionRecord",
    "MarginError",
    "MarginPosition",
    "MarginSnapshot",
    "margin_for_position",
    "margin_snapshot",
    "notional_in_account_currency",
    "required_margin",
    "validate_exposure",
    "RiskSizingError",
    "ForexRateBook",
    "pip_value_per_unit",
    "quote_to_account_rate",
    "units_for_fixed_risk",
    "RiskLimitError",
    "RiskControl",
    "RiskLimitPolicy",
    "RiskLimits",
    "RiskUsage",
    "aggregate_currency_exposure",
]
