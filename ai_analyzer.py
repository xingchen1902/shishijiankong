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

RELEASE_BUCKETS = (
    ("<400", None, 400),
    ("400-999.99", 400, 1000),
    ("1000-4999.99", 1000, 5000),
    ("5000-9999.99", 5000, 10000),
    (">=10000", 10000, None),
)


def _date_range(date_str):
    next_date = (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    return date_str + " 00:00:00", next_date + " 00:00:00"


def _release_distribution(rows):
    """按释放数量区间计算笔数占比和 ARK 数量占比。"""
    def summarize(values):
        total_count = len(values)
        total_amount = sum(values)
        result = []
        for label, lower, upper in RELEASE_BUCKETS:
            selected = [value for value in values if (lower is None or value >= lower) and (upper is None or value < upper)]
            amount = sum(selected)
            result.append({
                "区间": label,
                "笔数": len(selected),
                "数量_ARK": round(amount, 6),
                "笔数占比": round(len(selected) / total_count * 100, 2) if total_count else 0,
                "数量占比": round(amount / total_amount * 100, 2) if total_amount else 0,
            })
        return {
            "总笔数": total_count,
            "总数量_ARK": round(total_amount, 6),
            "区间分布": result,
            "大额_大于等于5000": {
                "笔数": sum(value >= 5000 for value in values),
                "数量_ARK": round(sum(value for value in values if value >= 5000), 6),
            },
            "超大额_大于等于10000": {
                "笔数": sum(value >= 10000 for value in values),
                "数量_ARK": round(sum(value for value in values if value >= 10000), 6),
            },
        }

    all_values = [float(row["value"] or 0) for row in rows]
    return {
        "全部释放": summarize(all_values),
        "静态释放": summarize([float(row["value"] or 0) for row in rows if row["type"] == "release_static"]),
        "动态释放": summarize([float(row["value"] or 0) for row in rows if row["type"] == "release_dynamic"]),
    }


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
    all_release_rows = conn.execute(
        """
        SELECT type, value FROM events
        WHERE timestamp >= ? AND timestamp < ?
          AND type IN ('release_static', 'release_dynamic')
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
    swap_data = dict(swap) if swap else {}
    if swap_data:
        buy_usdt = float(swap_data.get("buy_usdt") or 0)
        sell_usdt = float(swap_data.get("sell_usdt") or 0)
        swap_data["net_buy_usdt"] = round(buy_usdt - sell_usdt, 6)
        swap_data["口径说明"] = "净买入USDT = buy_usdt - sell_usdt；正数表示资金净流入底池，负数表示净流出底池"
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
        "释放分布": _release_distribution(all_release_rows),
        "底池交易统计": swap_data,
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
                        "分析中必须引用‘释放分布’中的笔数占比、数量占比和大额占比。"
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


def _format_report(report, payload=None):
    def items(key):
        values = report.get(key) or []
        return "\n".join(f"• {value}" for value in values) if values else "• 无"

    sections = [
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
    ]
    distribution = (payload or {}).get("释放分布", {}).get("全部释放")
    if distribution:
        sections.extend(["", "释放分布（程序计算）："])
        sections.append(
            f"总计：{distribution['总笔数']} 笔 / {distribution['总数量_ARK']:,.2f} ARK"
        )
        for bucket in distribution["区间分布"]:
            sections.append(
                f"{bucket['区间']}：{bucket['笔数']} 笔（{bucket['笔数占比']:.2f}%），"
                f"{bucket['数量_ARK']:,.2f} ARK（{bucket['数量占比']:.2f}%）"
            )
        large = distribution["大额_大于等于5000"]
        extra_large = distribution["超大额_大于等于10000"]
        total_amount = distribution["总数量_ARK"] or 0
        sections.extend([
            f"大额≥5,000：{large['笔数']} 笔，{large['数量_ARK']:,.2f} ARK（{large['数量_ARK'] / total_amount * 100:.2f}%）" if total_amount else "大额≥5,000：0",
            f"超大额≥10,000：{extra_large['笔数']} 笔，{extra_large['数量_ARK']:,.2f} ARK（{extra_large['数量_ARK'] / total_amount * 100:.2f}%）" if total_amount else "超大额≥10,000：0",
        ])
    return "\n".join(sections)


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
            report_text = _format_report(report, payload)
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
