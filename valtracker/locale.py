"""The site's words in Korean, British English and Traditional Chinese (Taiwan), looked up by the Japanese.

One Japanese text per entry; a text missing here is shown in Japanese (tests/test_i18n.py finds the ones the
pages use). The owner's own words (OWNER_ONLY: the settings and what only the owner is told) stay Japanese.
"""

TEXTS = {
    # Header, footer and the parts every page has
    "言語": {"ko": "언어", "en-GB": "Language", "zh-TW": "語言"},
    "ダークモード {state}": {"ko": "다크 모드 {state}", "en-GB": "Dark mode: {state}", "zh-TW": "深色模式：{state}"},
    "ダークモード: 自動 → オフ → オン": {
        "ko": "다크 모드: 자동 → 끔 → 켬",
        "en-GB": "Dark mode: auto → off → on",
        "zh-TW": "深色模式：自動 → 關 → 開",
    },
    "自動": {"ko": "자동", "en-GB": "auto", "zh-TW": "自動"},
    "オフ": {"ko": "끔", "en-GB": "off", "zh-TW": "關"},
    "オン": {"ko": "켬", "en-GB": "on", "zh-TW": "開"},
    "本文へ移動": {"ko": "본문으로 이동", "en-GB": "Skip to content", "zh-TW": "跳到主要內容"},
    "メニュー": {"ko": "메뉴", "en-GB": "Menu", "zh-TW": "選單"},
    "検索": {"ko": "검색", "en-GB": "Search", "zh-TW": "搜尋"},
    "エージェント": {"ko": "요원", "en-GB": "Agents", "zh-TW": "特務"},
    "設定": {"ko": "설정", "en-GB": "Settings", "zh-TW": "設定"},
    "サイト一覧へ戻る": {"ko": "사이트 목록으로 돌아가기", "en-GB": "Back to all sites", "zh-TW": "回到網站列表"},
    "VALORANT のプレイヤーのランクと最近の試合、エージェントの能力を、スマホでもすぐ見られるトラッカー。": {
        "ko": "VALORANT 플레이어의 랭크와 최근 경기, 요원의 스킬을 스마트폰에서도 바로 볼 수 있는 트래커.",
        "en-GB": "A VALORANT tracker for players' ranks and recent matches, and what each agent can do, quick to read on a phone.",
        "zh-TW": "在手機上也能馬上查看 VALORANT 玩家的牌位、近期對戰與特務技能的戰績查詢工具。",
    },
    "VAL Tracker は Riot Games の承認を受けたものではなく、Riot Games や VALORANT の制作・運営に関わる人の見解を示すものではありません。VALORANT と Riot Games は Riot Games, Inc. の商標または登録商標です。": {
        "ko": "VAL Tracker는 Riot Games의 승인을 받지 않았으며, Riot Games 또는 VALORANT의 제작·운영에 관여하는 사람의 견해를 반영하지 않습니다. VALORANT와 Riot Games는 Riot Games, Inc.의 상표 또는 등록 상표입니다.",
        "en-GB": "VAL Tracker isn't endorsed by Riot Games and doesn't reflect the views or opinions of Riot Games or anyone officially involved in producing or managing VALORANT. VALORANT and Riot Games are trademarks or registered trademarks of Riot Games, Inc.",
        "zh-TW": "VAL Tracker 未經 Riot Games 認可，亦不代表 Riot Games 或任何正式參與 VALORANT 製作、營運人員的觀點與意見。VALORANT 與 Riot Games 為 Riot Games, Inc. 的商標或註冊商標。",
    },
    "戦績は HenrikDev API、ゲームの名前と画像は valorant-api.com から取得しています。": {
        "ko": "전적은 HenrikDev API에서, 게임의 이름과 이미지는 valorant-api.com에서 가져옵니다.",
        "en-GB": "Match data comes from the HenrikDev API; the game's names and pictures from valorant-api.com.",
        "zh-TW": "戰績來自 HenrikDev API，遊戲中的名稱與圖片來自 valorant-api.com。",
    },
    # Errors
    "エラー {status}": {"ko": "오류 {status}", "en-GB": "Error {status}", "zh-TW": "錯誤 {status}"},
    "ページが見つかりません": {"ko": "페이지를 찾을 수 없습니다", "en-GB": "Page not found", "zh-TW": "找不到這個頁面"},
    "アドレスが変わったか、なくなった可能性があります。": {
        "ko": "주소가 바뀌었거나 페이지가 없어졌을 수 있습니다.",
        "en-GB": "The address may have changed, or the page may no longer exist.",
        "zh-TW": "網址可能已變更，或頁面已不存在。",
    },
    "{name} のトップへ": {"ko": "{name} 첫 화면으로", "en-GB": "{name} home", "zh-TW": "前往 {name} 首頁"},
    "この操作はできませんでした。": {
        "ko": "이 작업을 할 수 없었습니다.",
        "en-GB": "That could not be done.",
        "zh-TW": "無法執行這項操作。",
    },
    "戦績の検索はまだ準備中です。": {
        "ko": "전적 검색은 아직 준비 중입니다.",
        "en-GB": "Player search is not ready yet.",
        "zh-TW": "戰績搜尋仍在準備中。",
    },
    "戦績を取得できませんでした。時間をおいてもう一度どうぞ。": {
        "ko": "전적을 가져오지 못했습니다. 잠시 후 다시 시도해 주세요.",
        "en-GB": "The match data could not be fetched. Please try again later.",
        "zh-TW": "無法取得戰績，請稍後再試。",
    },
    "プレイヤーが見つかりませんでした。名前とタグを確かめてください。": {
        "ko": "플레이어를 찾지 못했습니다. 이름과 태그를 확인해 주세요.",
        "en-GB": "No player was found. Please check the name and tag.",
        "zh-TW": "找不到這位玩家，請確認名稱與標籤。",
    },
    "混み合っています。少し待ってからもう一度どうぞ。": {
        "ko": "요청이 많습니다. 잠시 후 다시 시도해 주세요.",
        "en-GB": "It is busy just now. Please wait a moment and try again.",
        "zh-TW": "目前使用人數較多，請稍候再試。",
    },
    "戦績のサービスから答えがありませんでした。時間をおいてもう一度どうぞ。": {
        "ko": "전적 서비스에서 응답이 없었습니다. 잠시 후 다시 시도해 주세요.",
        "en-GB": "The match data service did not answer. Please try again later.",
        "zh-TW": "戰績服務沒有回應，請稍後再試。",
    },
    # Search and favourites (top)
    "プレイヤーを探す": {"ko": "플레이어 찾기", "en-GB": "Find a player", "zh-TW": "搜尋玩家"},
    "Riot ID（名前#タグ）でランクと最近の試合を調べられます。☆を付けたプレイヤーはこのブラウザに保存され、ここに並びます。": {
        "ko": "Riot ID(이름#태그)로 랭크와 최근 경기를 찾아볼 수 있습니다. ☆를 누른 플레이어는 이 브라우저에 저장되어 여기에 표시됩니다.",
        "en-GB": "Look up a rank and recent matches by Riot ID (name#tag). Players you star are saved in this browser and listed here.",
        "zh-TW": "用 Riot ID（名稱#標籤）查詢牌位與近期對戰。加上 ☆ 的玩家會儲存在這個瀏覽器，並列在這裡。",
    },
    "Riot ID": {"ko": "Riot ID", "en-GB": "Riot ID", "zh-TW": "Riot ID"},
    "名前#タグ": {"ko": "이름#태그", "en-GB": "Name#tag", "zh-TW": "名稱#標籤"},
    "「名前#タグ」の形で入れてください（例: Name#JP1）。": {
        "ko": "“이름#태그” 형식으로 입력해 주세요(예: Name#KR1).",
        "en-GB": "Please enter it as “name#tag” (for example Name#EUW).",
        "zh-TW": "請以「名稱#標籤」的格式輸入（例如 Name#TW1）。",
    },
    "お気に入り": {"ko": "즐겨찾기", "en-GB": "Favourites", "zh-TW": "我的最愛"},
    "まだありません。プレイヤーのページで ☆ を押すと、ここに並びます。": {
        "ko": "아직 없습니다. 플레이어 페이지에서 ☆를 누르면 여기에 표시됩니다.",
        "en-GB": "None yet. Press ☆ on a player's page to list them here.",
        "zh-TW": "目前還沒有。在玩家頁面按下 ☆，就會列在這裡。",
    },
    "お気に入りを使うには JavaScript を有効にしてください。": {
        "ko": "즐겨찾기를 쓰려면 JavaScript를 켜 주세요.",
        "en-GB": "Favourites need JavaScript to be turned on.",
        "zh-TW": "要使用我的最愛，請啟用 JavaScript。",
    },
    "最近見たプレイヤー": {"ko": "최근 본 플레이어", "en-GB": "Recently viewed", "zh-TW": "最近查看的玩家"},
    "まだありません。": {"ko": "아직 없습니다.", "en-GB": "None yet.", "zh-TW": "目前還沒有。"},
    # A player
    "検索に戻る": {"ko": "검색으로 돌아가기", "en-GB": "Back to search", "zh-TW": "回到搜尋"},
    "レベル {level}": {"ko": "레벨 {level}", "en-GB": "Level {level}", "zh-TW": "等級 {level}"},
    "お気に入りに追加": {"ko": "즐겨찾기에 추가", "en-GB": "Add to favourites", "zh-TW": "加入我的最愛"},
    "お気に入りから外す": {"ko": "즐겨찾기에서 빼기", "en-GB": "Remove from favourites", "zh-TW": "從我的最愛移除"},
    "現在のランク": {"ko": "현재 랭크", "en-GB": "Current rank", "zh-TW": "目前牌位"},
    "最高ランク": {"ko": "최고 랭크", "en-GB": "Peak rank", "zh-TW": "最高牌位"},
    "ランクなし": {"ko": "랭크 없음", "en-GB": "Unranked", "zh-TW": "無牌位"},
    "直近 {count} 試合": {"ko": "최근 {count}경기", "en-GB": "Last {count} matches", "zh-TW": "最近 {count} 場"},
    "勝率": {"ko": "승률", "en-GB": "Win rate", "zh-TW": "勝率"},
    "{wins}勝 {losses}敗": {
        "ko": "{wins}승 {losses}패",
        "en-GB": "{wins} W {losses} L",
        "zh-TW": "{wins} 勝 {losses} 敗",
    },
    "HS率": {"ko": "헤드샷 비율", "en-GB": "HS%", "zh-TW": "爆頭率"},
    "よく使うエージェント": {"ko": "자주 쓰는 요원", "en-GB": "Most played agents", "zh-TW": "常用特務"},
    "{games}試合": {"ko": "{games}경기", "en-GB": "{games} played", "zh-TW": "{games} 場"},
    "{wins}勝": {"ko": "{wins}승", "en-GB": "{wins} won", "zh-TW": "{wins} 勝"},
    "最近の試合": {"ko": "최근 경기", "en-GB": "Recent matches", "zh-TW": "近期對戰"},
    "最近の試合が見つかりませんでした。": {
        "ko": "최근 경기를 찾지 못했습니다.",
        "en-GB": "No recent matches were found.",
        "zh-TW": "找不到近期對戰。",
    },
    "勝ち": {"ko": "승리", "en-GB": "Win", "zh-TW": "勝"},
    "負け": {"ko": "패배", "en-GB": "Loss", "zh-TW": "敗"},
    "引き分け": {"ko": "무승부", "en-GB": "Draw", "zh-TW": "平手"},
    # A match
    "試合": {"ko": "경기", "en-GB": "Match", "zh-TW": "對戰"},
    "{minutes}分": {"ko": "{minutes}분", "en-GB": "{minutes} min", "zh-TW": "{minutes} 分鐘"},
    "レッド": {"ko": "레드", "en-GB": "Red", "zh-TW": "紅隊"},
    "ブルー": {"ko": "블루", "en-GB": "Blue", "zh-TW": "藍隊"},
    "プレイヤー": {"ko": "플레이어", "en-GB": "Player", "zh-TW": "玩家"},
    # Queues
    "コンペティティブ": {"ko": "경쟁전", "en-GB": "Competitive", "zh-TW": "競技模式"},
    "アンレート": {"ko": "일반전", "en-GB": "Unrated", "zh-TW": "一般模式"},
    "スイフトプレイ": {"ko": "신속플레이", "en-GB": "Swiftplay", "zh-TW": "極速模式"},
    "スパイクラッシュ": {"ko": "스파이크 돌격", "en-GB": "Spike Rush", "zh-TW": "輻能搶攻戰"},
    "デスマッチ": {"ko": "데스매치", "en-GB": "Deathmatch", "zh-TW": "死鬥模式"},
    "エスカレーション": {"ko": "에스컬레이션", "en-GB": "Escalation", "zh-TW": "鬥陣升級"},
    "レプリケーション": {"ko": "복제", "en-GB": "Replication", "zh-TW": "複製模式"},
    "チームデスマッチ": {"ko": "팀 데스매치", "en-GB": "Team Deathmatch", "zh-TW": "團隊死鬥"},
    "プレミア": {"ko": "프리미어", "en-GB": "Premier", "zh-TW": "Premier"},
    "カスタム": {"ko": "사용자 설정", "en-GB": "Custom", "zh-TW": "自訂對戰"},
    "新マップ": {"ko": "새로운 맵", "en-GB": "New Map", "zh-TW": "新地圖"},
    "スノーボールファイト": {"ko": "눈싸움", "en-GB": "Snowball Fight", "zh-TW": "雪球大戰"},
    # Agents
    "役割とアビリティの説明です。": {
        "ko": "역할과 스킬 설명입니다.",
        "en-GB": "Each agent's role and abilities.",
        "zh-TW": "各特務的定位與技能說明。",
    },
    "エージェントの一覧を取得できませんでした。時間をおいてもう一度どうぞ。": {
        "ko": "요원 목록을 가져오지 못했습니다. 잠시 후 다시 시도해 주세요.",
        "en-GB": "The list of agents could not be fetched. Please try again later.",
        "zh-TW": "無法取得特務列表，請稍後再試。",
    },
    "アビリティ": {"ko": "스킬", "en-GB": "Abilities", "zh-TW": "技能"},
    "アルティメット": {"ko": "궁극기", "en-GB": "Ultimate", "zh-TW": "絕招"},
}

#: Words only the owner sees (the settings, and why the search cannot work yet): Japanese only.
OWNER_ONLY = {
    "HenrikDev の API キーがまだ設定されていません。設定から入れてください。",
    "HenrikDev の API キーが受け付けられませんでした。設定で確かめてください。",
    "API キーを消しました。",
    "API キーを保存しました。",
}
