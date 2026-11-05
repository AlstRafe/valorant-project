# VAL Tracker

VALORANT のトラッカー。Riot ID でプレイヤーのランクと最近の試合（KAST・ラウンドの流れなど）を調べ、お気に入りに登録できる。
地域のランキング、ストアのバンドル、クロスヘアのコード表示、サーバー状況、エージェントの能力、サーバーごとの混む時間帯と待ち時間の目安、
いま出るマップとパーティーのランク差、感度の換算も見られる。
4 言語（日本語・韓国語・英語・繁体字中国語）、ダーク / ライト、スマホ対応。サイト一覧（Home Page）に Python のサイトとして `/val` に載る。

設計と今後の段階（Riot ログインでショップ・所持品・エージェント進捗、対戦中の相手）は [docs/DESIGN.md](docs/DESIGN.md)。

## 動かす

```bash
python -m pip install -r requirements.txt
python -m valtracker            # http://127.0.0.1:5050/ （この PC だけ。設定も開ける）
python -m unittest discover -s tests
```

- 戦績には HenrikDev API のキーが要る。[ダッシュボード](https://api.henrikdev.xyz/dashboard/)の「API Keys」で作り、画面の「設定」に入れる（`instance/settings.json` に保存。Git には入らない）。試すだけなら環境変数 `VAL_HENRIK_KEY` でもよい
- 所持品（`/val/collection`、持ち主だけ）は、設定に Riot の `ssid` クッキーを一度貼ると見られる（手順は設定ページ。仕組みは [DESIGN.md](docs/DESIGN.md#riot-のログイン段階2valtrackerriotpy)）
- エージェントやマップの名前と絵は valorant-api.com から取り、`instance/` にためて自分のアドレスで出す（外部の読み込みはしない）

## Home Page に載せる

Home Page の `sites.toml` に次の `[[site]]` を足す（Home Page プロジェクト側の作業）。設定ページは持ち主だけが開ける（公開の名前では、持ち主としてログインした端末だけ）。

```toml
[[site]]
mount = "/val"
python = "../VALORANT-Project"
entry = "valtracker.hosting:site"
```

## 書式とチェック

Home Page・News Site・Infra Game と共通のルール（1 行 120 字・ダブルクォート・LF ほか）。まとめは Home Page の [docs/CONVENTIONS.md](https://github.com/AlstRafe/home-page/blob/master/docs/CONVENTIONS.md)。VS Code は推奨拡張（EditorConfig・Ruff・Prettier・ESLint・Even Better TOML ほか）を入れると保存時に整形する。

```bash
uvx ruff@0.16.9 format .   # Python の整形
uvx ruff@0.16.9 check .    # Python のチェック
uvx taplo@0.9.3 fmt        # TOML の整形（確かめるだけなら --check）
npm install                # 初回だけ（Prettier・ESLint。サイト自体は使わない）
npm run format             # JS・CSS・Markdown・JSON・YAML の整形
npm run lint               # 整形済みかの確認と ESLint（警告 0 件まで）
```

GitHub に push すると Actions（`.github/workflows/ci.yml`）がテスト（Ubuntu と Windows）・Ruff・Taplo・Prettier・ESLint と、
画面の崩れの検査を通す。

```bash
python tests/ui/serve.py                         # テストの作り物の答えで http://127.0.0.1:5077/ を開く
node tests/ui/audit.js http://127.0.0.1:5077     # 全ページ × 4 言語 × 360/760/1000/1440px（--quick で ja と en-GB だけ）
```

はみ出し・文字の重なり・切れ・1 行のはずの物の折り返し・小さすぎるボタン・エラーを探し、見つかれば 1 で終わる。
Actions の「CI → Run workflow」で手で回すと、本番（実際のプレイヤーと試合）も同じように調べる。テンプレート（`valtracker/templates/*.html`）は整形しない（`.prettierignore`）。

VAL Tracker は Riot Games の承認を受けたものではなく、Riot Games や VALORANT の制作・運営に関わる人の見解を示すものではありません。VALORANT と Riot Games は Riot Games, Inc. の商標または登録商標です。
