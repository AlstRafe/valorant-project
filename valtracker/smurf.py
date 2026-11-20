"""トラッカーから見える情報で、サブ垢（スマーフ）である確率を推定する。あくまで推定なので、
ページにも根拠と一緒にそう書く（どの材料も単独では何も証明しない）。

求め方（どの数字もページの根拠から確かめられる）:

1. 出発点は ``BASE`` %（ランク戦をする人のうちサブ垢の割合の見当。Riot は数字を出していない）。
2. 材料ごとに「サブ垢ならこうなりやすい / 本垢ならこうなりやすい」の倍率（尤度比）を表から決め、
   オッズに掛ける（ロジスティック: 対数オッズを足す）。
   - アカウントレベル: ランクの割に低いほどサブ垢寄り（レベルはプレイ時間、ランクは腕前で上がる）
   - ランク戦のアクト数: 少ないほどサブ垢寄り。レベルとほぼ同じこと（アカウントの古さ）を測るので、
     レベルがわかるときは効き目を半分にする
   - 勝率: 5 割からのずれを試合数で割った z 値。少ない試合の高い勝率は偶然でも起きるので弱い
   - 同じランク帯の中での VAL スコア（score.py）: 帯の真ん中（500）より上ほどサブ垢寄り
3. ページの「+24」などは、各材料が確率を何ポイント動かしたか（シャープレイ値: どの順で足しても公平な割り振り）。
   出発点にすべて足すと、ちょうど出した確率になる。

ランクが無いか、使える材料が 1 つも無いときは推定しない。"""

from __future__ import annotations

import math
from itertools import combinations

#: 出発点（%）。
BASE = 10
#: 「サブ垢の可能性が高い」「あり」になる確率（%）。それ未満は本垢寄り。
LIKELY, MAYBE = 60, 35
#: 根拠の棒がいっぱいになるポイント（どの材料の棒も同じ物差し）。
FULL = 40
#: 勝率を材料にする最少の試合数、VAL スコアの最少の試合数。
GAMES_ENOUGH, SCORE_GAMES = 10, 5

#: 根拠の文（訳は locale.py）。
LEVEL_NEW = "レベル {level} でこのランク"
LEVEL_LOW = "ランクの割にレベルが低い（{level}）"
LEVEL_FITS = "レベル {level}（ランク相応）"
LEVEL_HIGH = "レベル {level}（長く遊んでいる）"
ACTS_FEW = "ランク戦は {count} アクトだけ"
ACTS_SOME = "ランク戦 {count} アクト"
ACTS_MANY = "ランク戦 {count} アクト（長い経歴）"
WIN_RECENT = "最近の勝率 {rate}%（{games} 試合）"
WIN_ACT = "今アクトの勝率 {rate}%（{games} 試合）"
SCORE = "同じランク帯の中で VAL スコア {score}"
#: 使わなかった材料。
NO_LEVEL = "レベルは非公開"
FEW_GAMES = "試合が少ないので勝率は使っていない（{games} 試合）"
#: 文の全部（tests/test_i18n.py が使う）。
WORDS = (
    LEVEL_NEW,
    LEVEL_LOW,
    LEVEL_FITS,
    LEVEL_HIGH,
    ACTS_FEW,
    ACTS_SOME,
    ACTS_MANY,
    WIN_RECENT,
    WIN_ACT,
    SCORE,
    NO_LEVEL,
    FEW_GAMES,
)

#: ランク（tier の下限）ごとの (レベルの上限（未満）, 倍率)。上限 None は残り全部。
#: 21 = アセンダント、15 = プラチナ、9 = シルバー、3 = アイアン。ランク戦はレベル 20 から。
LEVELS = (
    (21, ((60, 10.0), (120, 3.0), (250, 0.9), (None, 0.45))),
    (15, ((50, 8.0), (100, 2.5), (200, 0.85), (None, 0.4))),
    (9, ((30, 4.0), (60, 1.8), (150, 0.85), (None, 0.5))),
    (3, ((150, 1.0), (None, 0.6))),
)
#: ランク（tier の下限）ごとの (アクト数の上限（以下）, 倍率)。12 = ゴールド。
ACTS = (
    (15, ((1, 3.5), (2, 2.0), (5, 1.0), (10, 0.7), (None, 0.5))),
    (12, ((1, 2.5), (2, 1.6), (5, 1.0), (10, 0.75), (None, 0.55))),
    (3, ((2, 1.2), (5, 1.0), (10, 0.8), (None, 0.6))),
)


def _from_table(table, tier: int, value: int, inclusive: bool) -> float:
    rows = next((rows for low, rows in table if tier >= low), table[-1][1])
    for limit, ratio in rows:
        if limit is None or (value <= limit if inclusive else value < limit):
            return ratio
    return 1.0


def _chance(log_odds: float) -> float:
    return 1 / (1 + math.exp(-log_odds))


def _shares(start: float, effects: list[float]) -> list[float]:
    """各材料（対数オッズ ``effects``）が確率を動かした分（0〜1）: シャープレイ値。足すと全部の材料での確率 − 出発点。"""
    count = len(effects)
    shares = []
    for index, effect in enumerate(effects):
        others = effects[:index] + effects[index + 1 :]
        share = 0.0
        for size in range(count):
            weight = math.factorial(size) * math.factorial(count - size - 1) / math.factorial(count)
            for group in combinations(others, size):
                before = start + sum(group)
                share += weight * (_chance(before + effect) - _chance(before))
        shares.append(share)
    return shares


def guess(
    tier: int,
    level: int | None,
    acts: int | None = None,
    games: int = 0,
    win_rate: float | None = None,
    score: int | None = None,
    score_games: int = 0,
    act_record: bool = False,
) -> dict | None:
    """サブ垢の確率の推定。材料が無ければ None。
    ``tier``: 今のランク、``level``: アカウントレベル（隠していれば None）、``acts``: ランク戦をしたアクトの数、
    ``games``/``win_rate``: 試合数と勝率（``act_record``: 今アクトの戦績、でなければ最近の試合）、
    ``score``/``score_games``: VAL スコアの平均とその試合数（同じランク帯が基準）。

    答え: ``percent``（サブ垢の確率 %）、``kind``（likely / maybe / main）、``side``（smurf / main: 確率の高い方）と
    ``shown``（その方の %）、``sure``（信頼度 high / mid / low）、``base``（出発点 %）、``signs``（根拠:
    ``{"words": (文, 値), "points": 確率を動かしたポイント, "share": 棒の長さ（``FULL`` ポイントで 100）}``、効いた順）、
    ``unused``（使わなかった材料の文）。"""
    if tier <= 2:
        return None
    acts = acts if acts and acts > 0 else None  # ランクがあるのに 0: アクトの一覧が読めていない
    signs: list[tuple[tuple[str, dict], float]] = []
    unused: list[tuple[str, dict]] = []
    if level:
        ratio = _from_table(LEVELS, tier, level, inclusive=False)
        words = LEVEL_NEW if ratio >= 3 else LEVEL_LOW if ratio > 1.2 else LEVEL_HIGH if ratio < 0.6 else LEVEL_FITS
        signs.append(((words, {"level": level}), math.log(ratio)))
    else:
        unused.append((NO_LEVEL, {}))
    if acts is not None:
        ratio = _from_table(ACTS, tier, acts, inclusive=True)
        words = ACTS_FEW if ratio >= 1.5 else ACTS_MANY if ratio <= 0.6 else ACTS_SOME
        signs.append(((words, {"count": acts}), math.log(ratio) / (2 if level else 1)))
    if win_rate is not None and games >= GAMES_ENOUGH:
        wins = games * win_rate / 100
        z = (wins - games / 2) / math.sqrt(games / 4)
        words = WIN_ACT if act_record else WIN_RECENT
        signs.append(((words, {"rate": round(win_rate), "games": games}), min(max(0.6 * z - 0.2, -0.6), 1.5)))
    elif win_rate is not None and games > 0:
        unused.append((FEW_GAMES, {"games": games}))
    if score is not None and score_games >= SCORE_GAMES:
        effect = min(max((score - 500) / 250 * 0.9, -0.6), 1.3) * min(1.0, score_games / 10)
        signs.append(((SCORE, {"score": score}), effect))
    if not signs:
        return None
    start = math.log(BASE / (100 - BASE))
    percent = min(max(round(100 * _chance(start + sum(effect for _, effect in signs))), 1), 99)
    shares = _shares(start, [effect for _, effect in signs])
    points = [round(100 * share) for share in shares]
    # 丸めの残り: 一番効いたものに。出発点 + 根拠 = 出した確率 にそろえる
    biggest = max(range(len(shares)), key=lambda index: abs(shares[index]))
    points[biggest] += percent - BASE - sum(points)
    order = sorted(range(len(signs)), key=lambda index: -abs(shares[index]))
    known_games = games if win_rate is not None and games >= GAMES_ENOUGH else 0
    if len(signs) >= 3 and known_games >= 20:
        sure = "high"
    elif len(signs) >= 2:
        sure = "mid"
    else:
        sure = "low"
    return {
        "percent": percent,
        "kind": "likely" if percent >= LIKELY else "maybe" if percent >= MAYBE else "main",
        "side": "smurf" if percent >= 50 else "main",
        "shown": percent if percent >= 50 else 100 - percent,
        "sure": sure,
        "base": BASE,
        "signs": [
            {
                "words": signs[index][0],
                "points": points[index],
                "share": min(100, round(100 * abs(points[index]) / FULL)),
            }
            for index in order
        ],
        "unused": unused,
    }
