# PC アプリのスクリーンショット

Windows の PC アプリ（`VALTracker.exe`）の実際のウィンドウを撮ったもの。サイト（`/val`）も同じ画面。

- 写っているプレイヤー・試合・数字・持ち主（Owner#JP1、Tester#JP1 など）はすべて作りもの。誰のアカウントも読んでいない
- エージェント・マップ・ランク・武器・スキンの名前と絵はゲームのもの（valorant-api.com から）
- 撮り直し: GitHub の Actions →「Screenshots」→ Run workflow（ブランチで）。`tests/ui/shots.py` が Windows のランナーで
  アプリのウィンドウを開いて撮り、そのブランチにコミットする。作りものの試合は `tests/ui/played.py`、サーバーは
  `tests/ui/serve.py --desktop --real`

## ライブ（自分のゲームをリアルタイムで）

Riot クライアントにサインインしていれば、設定なしで使える。ゲームの状態が変わるとすぐ切り替わる。

ゲームを閉じている間。自分の戦績・ショップ・進み具合・所持品への近道。

![ライブ: ゲームを閉じている間](screenshots/01-live-game-closed.png)

メニュー。パーティーの全員とランク、モードを選んでキューを開始。

![ライブ: メニューとキュー](screenshots/02-live-menus-queue.png)

マッチング中。待っている時間と、キューをやめるボタン。

![ライブ: マッチング中](screenshots/03-live-matchmaking.png)

エージェント選択。味方のランクと今アクトの成績、ピックと確定、味方にまだいない役割。名前を隠している人の名前は出さない。

![ライブ: エージェント選択](screenshots/04-live-agent-select.png)

対戦中。両チーム 10 人のエージェント・ランク・今アクトの成績と、チームの平均ランク。

![ライブ: 対戦中](screenshots/05-live-in-match.png)

試合が終わった直後。結果・自分の成績・RR の増減を、ゲームのサーバーから直接読んで出す。

![ライブ: 試合直後の結果](screenshots/06-live-result-after-match.png)

## プレイヤーの戦績

いまのランクと RR、次のランクまで、地域での位置、RR の推移。

![プレイヤー](screenshots/07-player.png)

VAL スコア（同じランク帯と比べた 1000 点満点）と、数字ごとの「上位 何 %」。

![プレイヤー: 数字](screenshots/08-player-numbers.png)

最近の試合。日ごとの勝敗・K/D・RR の合計、試合ごとの順位とスコア。

![プレイヤー: 最近の試合](screenshots/09-player-matches.png)

役割・エージェント・マップごとの成績と、マップごとの得意なエージェント。

![プレイヤー: エージェント別とマップ別](screenshots/10-player-agents-maps.png)

キルを取った場所と倒された場所。負けやすい場所を言葉でも出す。

![プレイヤー: キルとデスの場所](screenshots/11-player-kill-map.png)

通算。これまでに読んだ試合を全部ためて、RR の推移や成績を通しで見られる。

![プレイヤー: 通算](screenshots/12-player-lifetime.png)

## 試合

ラウンドの流れと、両チームのスコアボード（KAST・FK / FD・マルチキル・スコアなど）。

![試合](screenshots/13-match.png)

1 人ずつの対面成績と、その試合のキルとデスの場所（線は相手がいた場所へ）。

![試合: 対面成績とキルの場所](screenshots/14-match-kill-map.png)

ラウンドごとのキル。時間・武器・アシスト。

![試合: ラウンドごとのキル](screenshots/15-match-rounds.png)

## 持ち主の機能

今日のショップと残り時間、欲しいリストのスキンが並んだときの知らせ、ナイトマーケット。

![ショップ](screenshots/16-shop.png)

所持品。スキン・ガンバディー・カード・スプレー・タイトルを見て、ここから装備できる。

![所持品](screenshots/17-collection.png)

バトルパス・イベント・ミッション・エージェントの契約の進み具合。

![進み具合](screenshots/18-progress.png)

## エージェントとツール

エージェントの一覧（役割で絞れる）と、アビリティの説明。

![エージェント](screenshots/19-agents.png)

![エージェントのアビリティ](screenshots/20-agent.png)

ツールの一覧。

![ツール](screenshots/21-tools.png)

クロスヘア。ゲームのコードを貼ると、暗い所と明るい所での見た目と設定がわかる。

![クロスヘア](screenshots/22-crosshair.png)

サーバーごとの混む時間帯。

![サーバーの混み具合](screenshots/23-servers.png)

ランクの分布と、各ランクが上位何 % か。

![ランクの分布](screenshots/24-ranks.png)

## アプリ

設定と、新しい版が出たときの知らせ（どのページの上にも出る）。

![設定と新しい版の知らせ](screenshots/25-settings-update-notice.png)

細くしたウィンドウ（ゲームの横や 2 枚目の画面に置くとき）。タブは下に移る。

![細いウィンドウ](screenshots/26-narrow-window-in-match.png)

ライト。ウィンドウの枠（タイトルバー）もページの見た目に合わせる。

![ライト](screenshots/27-light-in-match.png)
