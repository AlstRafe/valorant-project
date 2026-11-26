"""/live の「今回のセッション」: 持ち主が続けてプレイしたコンペティティブの試合（ゲーム自身の RR の記録、
PD の competitiveupdates）の勝敗、動いた RR の合計、今のランクまでの道のり。

続けてプレイした = 試合の開始の間が GAP 秒以内。最後の試合から SHOWN 秒たったら出さない（その日の遊びは終わった）。
勝ち負けは RR の動きの向き（勝てば上がり、負ければ下がる。0 は引き分けか、ランクを守った負け）。
RR の無い配置試合とほかのモードは数えない。"""

from __future__ import annotations

from datetime import datetime

from valtracker.stats import JST

#: 続けてプレイした試合どうしの開始の間の上限（1 試合 40 分前後 + 休憩）。
GAP = 3 * 3600
#: 最後の試合の開始からこれだけたったら、セッションは出さない。
SHOWN = 8 * 3600
#: これより上の tier（イモータル 1〜）は RR に上限が無い: 次のランクまでの棒を出さない。
TOP_TIER = 23


def summary(changes: dict, now: float) -> dict | None:
    """``changes``: {試合 ID: {"change", "tier", "rr", "at"}}（pdmatch.rr_updates）。
    答え: ``{"matches": [{"id", "change", "tier", "rr", "at"}]（古い順）, "wins", "losses", "even", "net",
    "tier", "rr", "started"（最初の試合の開始、datetime JST）, "to_next"（次のランクまでの RR、無ければ None）}``、
    または None（最近の試合が無い）。"""
    rows = []
    for match_id, item in (changes or {}).items():
        if not isinstance(item, dict):
            continue
        try:
            at, change, tier, rr = (int(item.get(key) or 0) for key in ("at", "change", "tier", "rr"))
        except (TypeError, ValueError):
            continue
        if at > 0 and tier > 2:
            rows.append({"id": str(match_id), "change": change, "tier": tier, "rr": rr, "at": at})
    rows.sort(key=lambda row: row["at"], reverse=True)
    if not rows or now - rows[0]["at"] > SHOWN or rows[0]["at"] > now + 600:
        return None
    run = [rows[0]]
    for row in rows[1:]:
        if run[-1]["at"] - row["at"] > GAP:
            break
        run.append(row)
    run.reverse()
    last = run[-1]
    return {
        "matches": run,
        "wins": sum(row["change"] > 0 for row in run),
        "losses": sum(row["change"] < 0 for row in run),
        "even": sum(row["change"] == 0 for row in run),
        "net": sum(row["change"] for row in run),
        "tier": last["tier"],
        "rr": last["rr"],
        "started": datetime.fromtimestamp(run[0]["at"], JST),
        "to_next": max(0, 100 - last["rr"]) if last["tier"] <= TOP_TIER else None,
    }
