"""
Utility: CSV signal logger for post-session analysis.
"""
import csv
import os
from datetime import datetime


def log_signal(signal, log_file="logs/signals.csv"):
    os.makedirs("logs", exist_ok=True)
    header = [
        "timestamp", "symbol", "signal", "instrument_type", "strike",
        "entry_price", "stop_loss", "target_1", "confidence", "risk_reward",
        "sources", "strategy"
    ]
    row = [
        str(datetime.now()), signal.symbol, signal.signal, signal.instrument_type,
        signal.strike or "", signal.entry_price, signal.stop_loss,
        signal.target_1, signal.confidence, signal.risk_reward,
        signal.notes, signal.strategy_name,
    ]
    write_header = not os.path.exists(log_file)
    with open(log_file, "a", newline="") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(header)
        writer.writerow(row)
