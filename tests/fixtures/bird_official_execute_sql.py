"""Verbatim `execute_sql` from the official BIRD evaluation script (for comparator parity only).

Source: https://raw.githubusercontent.com/AlibabaResearch/DAMO-ConvAI/main/bird/llm/src/evaluation.py
(file sha256 2f591e559dc2d97e5b35d5b656e80b0c2edf968f0bb5a78ddfd1d88b4bbbc472, retrieved 2026-11-23).
"""
import sqlite3


def execute_sql(predicted_sql,ground_truth, db_path):
    conn = sqlite3.connect(db_path)
    # Connect to the database
    cursor = conn.cursor()
    cursor.execute(predicted_sql)
    predicted_res = cursor.fetchall()
    cursor.execute(ground_truth)
    ground_truth_res = cursor.fetchall()
    res = 0
    if set(predicted_res) == set(ground_truth_res):
        res = 1
    return res
