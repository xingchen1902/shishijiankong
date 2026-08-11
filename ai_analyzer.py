#!/usr/bin/env python3
"""每日 ARK 数据 AI 分析：程序整理事实，DeepSeek 负责解释。"""

import json
import os
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv

from db import (
    get_ai_daily_report,
    get_conn,
    get_dex_daily_snapshot,
    get_pool_address_daily_summaries,
    get_staking_daily_snapshots,
    save_ai_daily_report,
)
from pusher import push_ai_daily_report_to_telegram

load_dotenv()

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")


def _date_range(date_str):
    next_date = (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    return date_str + " 00:00:00", next_date + " 00:00:00"


def build_daily_ai_payload(date_str, record):
    start, end = _date_range(date_str)
    conn = get_conn()
    event_rows = conn.execute(
        """
        SELECT type, COUNT(*) AS count, COALESCE(SUM(value), 0) AS total, MAX(value) AS largest
        FROM events WHERE timestamp >= ? AND timestamp < ? GROUP BY type ORDER BY total DESC
        """, (start, end),
    ).fetchall()
    release_rows = conn.execute(
        """
        SELECT type, value, tx, block, timestamp, from_addr, to_addr, release_period
        FROM events
        WHERE timestamp >= ? AND timestamp < ?
          AND type IN ('release_static', 'release_dynamic') AND value >= 400
        ORDER BY timestamp, id LIMIT 500
        """, (start, end),
    ).fetchall()
    swap = conn.execute(
        """
        SELECT COALESCE(SUM(CASE WHEN side='buy_ark' THEN amount_usdt ELSE 0 END),0) AS buy_usdt,
               COALESCE(SUM(CASE WHEN side='sell_ark' THEN amount_usdt ELSE 0 END),0) AS sell_usdt,
               COUNT(*) AS count
        FROM lp_swaps WHERE timestamp >= ? AND timestamp < ?
        """, (start, end),
    ).fetchone()
    history = conn.execute(
        "SELECT * FROM daily_summary WHERE date <= ? ORDER BY date DESC LIMIT 7", (date_str,)
    ).fetchall()
    conn.close()

    pool_rows = [
        row for row in get_pool_address_daily_summaries(365)
        if row.get("date") == date_str
    ]
    staking = next(
        (row for row in get_staking_daily_snapshots(365) if row.get("date") == date_str),
        None,
    )
    current_dex = get_dex_daily_snapshot(date_str)
    previous_date = (datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    previous_dex = get_dex_daily_snapshot(previous_date)

    return {
        "说明": "所有金额和数量由监控程序计算，模型只能基于这些事实分析，不得自行补造数字。",
        "日期": date_str,
        "当日汇总": record,
        "事件统计": [dict(row) for row in event_rows],
        "异常释放明细": [dict(row) for row in release_rows],
        "底池交易统计": dict(swap) if swap else {},
        "监控资金地址日汇总": pool_rows,
        "底池快照": {"当天": current_dex, "前一天": previous_dex},
        "官网质押快照": staking,
        "近7日汇总": [dict(row) for row in history],
    }


def _call_deepseek(payload):
    response = requests.post(
        f"{DEEPSEEK_BASE_URL.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
        json={
            "model": DEEPSEEK_MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是 ARK 链上监控日报分析师。只使用用户提供的结构化数据。"
                        "先核对数字，再给出中文日报。不要把推测写成事实。"
                        "必须输出 JSON：risk_level、summary、key_findings、anomalies、recommendations。"
                        "risk_level 只能是 正常、关注、警告、高风险；数组字段必须是字符串数组。"
                    ),
                },
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            "thinking": {"type": "disabled"},
            "response_format": {"type": "json_object"},
            "max_tokens": 2500,
        },
        timeout=120,
    )
    response.raise_for_status()
    data = response.json()
    content = data["choices"][0]["message"].get("content") or "{}"
    if content.startswith("```"):
        content = content.strip().strip("`")
        if content.startswith("json"):
            content = content[4:].lstrip()
    report = json.loads(content)
    required = ("risk_level", "summary", "key_findings", "anomalies", "recommendations")
    if not report or any(key not in report for key in required):
        raise ValueError("DeepSeek 返回的日报 JSON 不完整")
    if not report.get("summary") and not any(report.get(key) for key in required[2:]):
        raise ValueError("DeepSeek 返回了空日报")
    risk_map = {"low": "正常", "medium": "关注", "high": "警告", "critical": "高风险"}
    report["risk_level"] = risk_map.get(report.get("risk_level"), report.get("risk_level", "关注"))
    return report


def _format_report(report):
    def items(key):
        values = report.get(key) or []
        return "\n".join(f"• {value}" for value in values) if values else "• 无"

    return "\n".join([
        f"综合评级：{report.get('risk_level', '关注')}",
        "",
        "核心结论：",
        str(report.get("summary") or "暂无结论"),
        "",
        "关键发现：",
        items("key_findings"),
        "",
        "异常与风险：",
        items("anomalies"),
        "",
        "建议关注：",
        items("recommendations"),
    ])


def generate_and_push_daily_report(date_str, record):
    """生成并推送日报；已有生成结果时只重试未成功的推送。"""
    existing = get_ai_daily_report(date_str)
    if existing and existing.get("status") == "pushed":
        print(f"  [AI日报] {date_str} 已完成，跳过")
        return True
    if existing and existing.get("report_text"):
        report_text = existing["report_text"]
    else:
        if not DEEPSEEK_API_KEY:
            print("  [AI日报] 跳过: 未配置 DEEPSEEK_API_KEY")
            save_ai_daily_report(date_str, model=DEEPSEEK_MODEL, status="pending", error="未配置 DEEPSEEK_API_KEY")
            return False
        payload = build_daily_ai_payload(date_str, record)
        try:
            report = _call_deepseek(payload)
            report_text = _format_report(report)
            save_ai_daily_report(
                date_str,
                model=DEEPSEEK_MODEL,
                status="generated",
                report_text=report_text,
                report_json=json.dumps(report, ensure_ascii=False),
                source_json=json.dumps(payload, ensure_ascii=False),
                generated_at=datetime.now().isoformat(timespec="seconds"),
                error="",
            )
        except Exception as exc:
            print(f"  [AI日报] 生成失败: {exc}")
            save_ai_daily_report(date_str, model=DEEPSEEK_MODEL, status="failed", error=str(exc))
            return False

    telegram_ok = bool(existing and existing.get("telegram_pushed")) or push_ai_daily_report_to_telegram(date_str, report_text)
    save_ai_daily_report(
        date_str,
        status="pushed" if telegram_ok else "push_failed",
        telegram_pushed=int(telegram_ok),
        error="" if telegram_ok else "Telegram 推送未成功",
    )
    return telegram_ok
