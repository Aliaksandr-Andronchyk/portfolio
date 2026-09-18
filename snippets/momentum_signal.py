"""Minimal momentum trading signal: SMA crossover + RSI filter.
Pure stdlib, drop into any pipeline."""
from dataclasses import dataclass
from statistics import mean


@dataclass
class Signal:
    index: int
    action: str
    price: float
    fast_sma: float
    slow_sma: float
    rsi: float


def sma(values, window):
    return mean(values[-window:])


def rsi(values, window=14):
    if len(values) < window + 1:
        return 50.0
    deltas = [values[i] - values[i - 1] for i in range(-window, 0)]
    gains = [d for d in deltas if d > 0]
    losses = [-d for d in deltas if d < 0]
    avg_gain = sum(gains) / window
    avg_loss = sum(losses) / window
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def momentum_signals(prices, fast=5, slow=20, rsi_window=14):
    signals = []
    for i in range(slow, len(prices)):
        window = prices[: i + 1]
        f, s = sma(window, fast), sma(window, slow)
        prev_f, prev_s = sma(window[:-1], fast), sma(window[:-1], slow)
        r = rsi(window, rsi_window)
        action = "HOLD"
        if prev_f <= prev_s and f > s and r < 70:
            action = "BUY"
        elif prev_f >= prev_s and f < s and r > 30:
            action = "SELL"
        signals.append(Signal(i, action, prices[i], f, s, r))
    return signals


if __name__ == "__main__":
    demo = [100, 101, 99, 102, 105, 107, 106, 110, 112, 111,
            115, 118, 117, 120, 122, 121, 119, 116, 114, 110,
            108, 105, 103, 101, 99]
    for sig in momentum_signals(demo):
        if sig.action != "HOLD":
            print(f"bar {sig.index}: {sig.action} @ {sig.price} "
                  f"(sma5={sig.fast_sma:.1f} sma20={sig.slow_sma:.1f} rsi={sig.rsi:.1f})")
