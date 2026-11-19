// サイトのページの崩れを探す: 画面より広いもの、文字の重なり、切れた文字、折り返したヘッダーのタブとボタン、
// スマホで小さすぎるタップ領域、読み込めなかった絵、スクリプトや CSP のエラー、読み込めなかったと言うページ。
// スクリーンリーダーと目のために: 名前の無いリンクと入力欄、飛んでいる見出し、同じ id が 2 つ、
// 背景に対して薄すぎる文字（WCAG AA、薄くした親も数える）、紙の上に出した暗い画面向けの絵（白地に白い印）、
// 場所がわからないほど薄い入力欄やボタンの縁（WCAG 1.4.11）。全ページを全言語で、
// サイトが想定している幅で（docs/CONVENTIONS.md）。
//
//   node tests/ui/audit.js <base, e.g. http://127.0.0.1:5077> [--live] [--quick] [--desktop]
//
// --live: the real site (fewer pages at once, follows the leaderboard to a real player and match). Exit code 1
// when something is found, with one line per problem and where it shows.
// --desktop: only the pages the PC app has and the site has not, or shows differently (the server run with
// tests/ui/serve.py --desktop).
"use strict";

const { chromium } = require("playwright");

const [base, ...flags] = process.argv.slice(2);
const live = flags.includes("--live");
const quick = flags.includes("--quick");
const desktop = flags.includes("--desktop");
// PC アプリ専用のページ: フレンド、チェックアップ、設定と、そのリンク元。
const DESKTOP_PAGES = ["/friends", "/checkup", "/settings", "/help", "/"];
const WIDTHS = [360, 760, 1000, 1440];
const LANGS = quick ? ["ja", "en-GB"] : ["ja", "ko", "en-GB", "zh-TW"];
const THEMES = live || quick ? ["light"] : ["light", "dark"];
// 各言語の訪問者をいつもの時計で（サーバーのページは訪問者自身の時計で時間を数える）。
const ZONES = { "ja": "Asia/Tokyo", "ko": "Asia/Seoul", "en-GB": "Europe/London", "zh-TW": "Asia/Taipei" };
const PAGES = [
  "/",
  "/leaderboard?full=1",
  "/status",
  "/crosshair?code=0;P;c;5;h;0;0l;4;0o;2;0a;1;1b;0",
  "/shop",
  "/tools",
  "/servers",
  "/ranks",
  "/queues?rank=14&rank=17",
  "/sensitivity?sens=0.35&dpi=800&new_dpi=1600",
  "/agents",
  "/news",
  "/esports",
  "/esports?show=results",
  "/skins",
  "/skins?weapon=all&tier=ultra",
  "/agents/stats",
  "/maps",
  "/maps/7eaecc1b-4337-bbf6-6ab9-04b8f06b3319",
  "/weapons",
  "/weapons/9c82e19d-4575-0200-1a81-3eacf00cf872?vs=ec845bf4-4f79-ddda-a3da-0db3774b2794",
  "/weapons/29a0cfab-485b-f5d5-779a-b59f85e204a8",
  "/compare",
  "/help",
  "/search?q=Tester",
  "/search?q=Tester%20JP1",
  "/p/nobody-here/0000",
];
// テストの作りものの答え（tests/ui/serve.py）で: そのプレイヤー、試合、エージェント。
const MADE_UP = [
  "/p/Tester/JP1?full=1",
  "/p/Tester/JP1?span=all&full=1",
  "/p/Tester/JP1?span=all&page=4&full=1",
  "/p/Tester/JP1?span=act&full=1",
  "/compare?a=Tester%23JP1&b=Other%23KR1&span=act",
  "/p/Tester/JP1/seasons",
  "/m/ap/aaaaaaaa-0000-4000-8000-000000000001",
  "/m/ap/aaaaaaaa-0000-4000-8000-000000000001?me=Tester%23JP1",
  "/agents/11111111-1111-4111-8111-111111111111",
  "/collection",
  "/settings",
  "/skins/aa000000-0000-4000-8000-000000000001",
  "/skins/aa000000-0000-4000-8000-000000000001?chroma=aa000000-0000-4000-8000-000000000022",
  "/shop/history",
  "/progress",
  "/live",
  "/live?phase=menus",
  "/live?phase=queue",
  "/live?phase=found",
  "/live?phase=pregame",
  "/live?phase=ingame",
  "/collection?show=cards",
  "/collection?weapon=9c82e19d-4575-0200-1a81-3eacf00cf872&equip=ok",
  "/collection?show=titles",
  "/collection?show=sprays",
  "/collection?show=buddies",
  "/collection?show=flex",
  "/collection?show=sets",
  "/collection?weapon=29a0cfab-485b-f5d5-779a-b59f85e204a8",
  // 装備を Riot から読めなかった（tests/ui/serve.py: 作りものの PD にはロードアウトが無い）
  "/collection?weapon=9c82e19d-4575-0200-1a81-3eacf00cf872&loadout=none",
  "/compare?a=Tester%23JP1&b=Other%23KR1",
  "/leaderboard?q=RiotTop%23AP1&full=1",
  "/agents/stats?queue=competitive",
];
// ページを見る前に見つけたもの（本番のスキン動画）: 最後に問題に足す。
const early = [];

// ページの中で動く: 問題ごとに {kind, where, text}。
function inspect() {
  const width = document.documentElement.clientWidth;
  const found = [];
  const path = (el) => {
    const parts = [];
    for (let node = el; node && node !== document.body && parts.length < 4; node = node.parentElement) {
      let part = node.tagName.toLowerCase();
      if (node.id) part += "#" + node.id;
      else if (node.classList.length) part += "." + [...node.classList].slice(0, 2).join(".");
      parts.unshift(part);
    }
    return parts.join(" > ");
  };
  const words = (el) =>
    (el.innerText || el.getAttribute("aria-label") || el.alt || "").trim().replace(/\s+/g, " ").slice(0, 40);
  const styles = new Map();
  const css = (el) => {
    if (!styles.has(el)) styles.set(el, getComputedStyle(el));
    return styles.get(el);
  };
  const shown = (el) => {
    const style = css(el);
    const box = el.getBoundingClientRect();
    return style.visibility !== "hidden" && style.display !== "none" && box.width > 0 && box.height > 0;
  };
  // 祖先がスクロールかクリップしているか（または固定バーの中か）と、fixed か sticky か: 要素ごとに、
  // 親の答えと自分のスタイルから求めて持つ。
  const kept = new Map();
  const up = (el) => {
    const whole = { left: -Infinity, top: -Infinity, right: Infinity, bottom: Infinity };
    if (!el || el === document.documentElement || el === document.body)
      return { clips: false, fixed: false, box: whole };
    if (!kept.has(el)) {
      const parent = up(el.parentElement);
      const style = css(el);
      const pinned = style.position === "fixed" || style.position === "sticky";
      let box = parent.box;
      if (style.overflowX !== "visible" || style.overflowY !== "visible") {
        const own = el.getBoundingClientRect();
        box = {
          left: Math.max(box.left, own.left),
          top: Math.max(box.top, own.top),
          right: Math.min(box.right, own.right),
          bottom: Math.min(box.bottom, own.bottom),
        };
      }
      kept.set(el, {
        clips: parent.clips || style.overflowX !== "visible" || pinned,
        fixed: parent.fixed || pinned,
        box, // 祖先が見せてくれるページの部分
      });
    }
    return kept.get(el);
  };
  // 要素の見える部分（祖先がクリップする部分は除く）。
  // 2 行にまたがるインライン要素は 1 行に 1 つの箱: 全体の箱だと次の言葉を覆ってしまう。
  const seen = (el) => {
    const room = up(el.parentElement).box;
    const parts = css(el).display === "inline" ? [...el.getClientRects()] : [el.getBoundingClientRect()];
    return parts.map((box) => ({
      left: Math.max(box.left, room.left),
      top: Math.max(box.top, room.top),
      right: Math.min(box.right, room.right),
      bottom: Math.min(box.bottom, room.bottom),
    }));
  };
  const clipped = (el) => up(el.parentElement).clips;
  const fixed = (el) => up(el).fixed;
  // 閉じた <details> の中（summary 以外）: 箱はあるが画面には無い。
  const folded = (el) => {
    const details = el.closest("details:not([open])");
    return Boolean(details) && !el.closest("summary");
  };
  const own = (el) => [...el.childNodes].some((node) => node.nodeType === 3 && node.textContent.trim());
  const all = [...document.body.querySelectorAll("*")].filter(
    (el) => !el.closest("svg, script, style, template, noscript, [hidden], .sr") && !folded(el) && shown(el),
  );

  if (document.documentElement.scrollWidth > width + 1) {
    found.push({
      kind: "page wider than the screen",
      where: "html",
      text: `${document.documentElement.scrollWidth}px > ${width}px`,
    });
  }
  for (const el of all) {
    const box = el.getBoundingClientRect();
    if ((box.right > width + 1 || box.left < -1) && !clipped(el)) {
      const parent = el.parentElement.getBoundingClientRect();
      if (!(parent.right > width + 1 || parent.left < -1))
        found.push({ kind: "sticks out of the screen", where: path(el), text: words(el) });
    }
    const style = css(el);
    if (own(el) && style.textOverflow !== "ellipsis" && el.tagName !== "TEXTAREA" && el.tagName !== "INPUT") {
      const cutX = style.overflowX !== "visible" && el.scrollWidth > el.clientWidth + 1;
      const cutY =
        style.overflowY !== "visible" && el.scrollHeight > el.clientHeight + 2 && !/auto|scroll/.test(style.overflowY);
      const tiny = el.clientWidth <= 1 || el.clientHeight <= 1; // スクリーンリーダー専用の言葉
      if (((cutX && !/auto|scroll/.test(style.overflowX)) || cutY) && !tiny)
        found.push({ kind: "text cut off", where: path(el), text: words(el) });
    }
  }
  // 文字の上の文字（ページの上の固定タブバーは意図したものなので除く）。
  const texts = all
    .filter((el) => own(el) && !fixed(el))
    .flatMap((el) => seen(el).map((box) => ({ el, box })))
    .filter(({ box }) => box.right - box.left > 1 && box.bottom - box.top > 1)
    .sort((a, b) => a.box.top - b.box.top);
  for (let i = 0; i < texts.length; i++) {
    const a = texts[i];
    for (let j = i + 1; j < texts.length && texts[j].box.top < a.box.bottom - 3; j++) {
      const b = texts[j];
      if (a.el.contains(b.el) || b.el.contains(a.el)) continue;
      const x = Math.min(a.box.right, b.box.right) - Math.max(a.box.left, b.box.left);
      const y = Math.min(a.box.bottom, b.box.bottom) - Math.max(a.box.top, b.box.top);
      if (x > 3 && y > 3) {
        found.push({
          kind: "text over text",
          where: path(a.el) + "  ×  " + path(b.el),
          text: words(a.el) + " / " + words(b.el),
        });
      }
    }
  }
  // 折り返した 1 行もの: ヘッダー、タブ、ボタン、チップ。
  // 行は文字のある位置で数える（行に合わせて高くなったボタンは折り返しではない）。
  const lines = (el) => {
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    const boxes = [];
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      if (!node.textContent.trim() || !shown(node.parentElement)) continue;
      const range = document.createRange();
      range.selectNodeContents(node);
      boxes.push(...[...range.getClientRects()].filter((box) => box.width > 1 && box.height > 1));
    }
    boxes.sort((a, b) => a.top - b.top);
    let count = 0;
    let bottom = -Infinity;
    for (const box of boxes) {
      if (box.top >= bottom - 2) count++;
      bottom = Math.max(bottom, box.bottom);
    }
    return count;
  };
  for (const el of document.querySelectorAll(
    "header a, header summary, header button, nav.modes a, .tabbar a, button, .button, .badge, thead th",
  )) {
    if (!shown(el) || el.closest("[hidden]") || folded(el)) continue;
    // 縦に積むボタン（エージェント選択のカード: 絵・名前・勝率）は、積んだ一つ一つが 1 行かを見る
    const look = getComputedStyle(el);
    const stacked = look.display === "grid" || (look.display === "flex" && look.flexDirection === "column");
    const parts = stacked ? [...el.children] : [el];
    if (Math.max(0, ...parts.map(lines)) > 1)
      found.push({ kind: "wraps onto two lines", where: path(el), text: words(el) });
  }
  if (width <= 760) {
    for (const el of document.querySelectorAll(
      "button, .button, select, input:not([type=hidden]), summary, nav a, .tabbar a",
    )) {
      if (!shown(el) || el.closest("[hidden]") || folded(el)) continue;
      // チェックボックスやラジオボタンはラベルでも押せる（WCAG: ラベルもターゲットの一部）
      const target = (el.matches("input[type=radio], input[type=checkbox]") && el.closest("label")) || el;
      const box = target.getBoundingClientRect();
      if (box.width < 24 || box.height < 24)
        found.push({
          kind: "too small to tap",
          where: path(el),
          text: `${Math.round(box.width)}×${Math.round(box.height)} ${words(el)}`,
        });
    }
  }
  // スクリーンリーダーとキーボード用: 名前、ラベル、見出し、id。
  const named = (el) =>
    Boolean(
      (el.textContent || "").trim() ||
      el.getAttribute("aria-label") ||
      el.getAttribute("title") ||
      el.querySelector("img[alt]:not([alt='']), svg title"),
    );
  for (const el of document.querySelectorAll("a[href], button")) {
    if (!el.closest("[hidden]") && !named(el)) found.push({ kind: "link or button without a name", where: path(el) });
  }
  for (const el of document.querySelectorAll("input:not([type=hidden]):not([type=submit]), select, textarea")) {
    const labelled =
      el.closest("label") ||
      el.getAttribute("aria-label") ||
      el.getAttribute("aria-labelledby") ||
      (el.id && document.querySelector(`label[for="${el.id}"]`));
    if (!labelled) found.push({ kind: "field without a label", where: path(el) });
  }
  for (const img of document.querySelectorAll("img:not([alt])"))
    found.push({ kind: "picture without alt", where: path(img) });
  const ids = new Map();
  for (const el of document.querySelectorAll("[id]")) ids.set(el.id, (ids.get(el.id) || 0) + 1);
  for (const [id, count] of ids)
    if (count > 1) found.push({ kind: "same id twice", where: "#" + id, text: `${count}×` });
  const headings = [...document.querySelectorAll("main h1, main h2, main h3, main h4")].filter(
    (el) => !el.closest("[hidden]"),
  );
  if (headings.filter((el) => el.tagName === "H1").length !== 1)
    found.push({
      kind: "not one h1",
      where: "main",
      text: String(headings.filter((el) => el.tagName === "H1").length),
    });
  for (let i = 1; i < headings.length; i++) {
    if (+headings[i].tagName[1] > +headings[i - 1].tagName[1] + 1)
      found.push({ kind: "heading skips a level", where: path(headings[i]), text: words(headings[i]) });
  }
  // 後ろのものに対して薄すぎて読めない文字（WCAG AA: 4.5、大きい文字は 3）。
  const canvas = document.createElement("canvas").getContext("2d", { willReadFrequently: true });
  const colors = new Map();
  const rgba = (value) => {
    if (!colors.has(value)) {
      canvas.clearRect(0, 0, 1, 1);
      canvas.fillStyle = "#000";
      canvas.fillStyle = value;
      canvas.fillRect(0, 0, 1, 1);
      const [r, g, b, a] = canvas.getImageData(0, 0, 1, 1).data;
      colors.set(value, [r, g, b, a / 255]);
    }
    return colors.get(value);
  };
  const over = (top, under) => top.slice(0, 3).map((part, i) => part * top[3] + under[i] * (1 - top[3]));
  const behind = (el) => {
    const layers = [];
    for (let node = el; node; node = node.parentElement) {
      const style = css(node);
      if (style.backgroundImage !== "none") return null; // 絵かグラデーション: わからない
      const color = rgba(style.backgroundColor);
      if (color[3] > 0) layers.push(color);
      if (color[3] >= 1) break;
    }
    let under = [255, 255, 255];
    for (const layer of layers.reverse()) under = over(layer, under);
    return under;
  };
  const light = (rgb) => {
    const [r, g, b] = rgb.map((part) => {
      const c = part / 255;
      return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  // 自分と親の opacity を通して要素がどれだけ見えるか
  const faded = (el) => {
    let shown = 1;
    for (let node = el; node; node = node.parentElement) shown *= +css(node).opacity;
    return shown;
  };
  for (const el of all) {
    if (!own(el) || el.closest("[aria-disabled=true], :disabled")) continue;
    const style = css(el);
    const back = behind(el);
    if (!back) continue;
    const color = rgba(style.color);
    const ink = over([color[0], color[1], color[2], color[3] * faded(el)], back);
    const [high, low] = [light(ink), light(back)].sort((a, b) => b - a);
    const ratio = (high + 0.05) / (low + 0.05);
    const size = parseFloat(style.fontSize);
    const large = size >= 24 || (size >= 18.6 && +style.fontWeight >= 700);
    if (ratio < (large ? 3 : 4.5))
      found.push({ kind: "text too faint", where: path(el), text: `${ratio.toFixed(2)} ${words(el)}` });
  }
  // 後ろのものに対する印やアイコン（透ける部分のある絵）: 描かれたピクセルの平均色を CSS の filter を通して。
  // 写真とカードは自分の背景を持つので測らない。
  const sample = document.createElement("canvas");
  sample.width = sample.height = 24;
  const draw = sample.getContext("2d", { willReadFrequently: true });
  for (const img of document.images) {
    if (!img.complete || !img.naturalWidth || !shown(img) || img.closest("[hidden], .sr") || folded(img)) continue;
    const box = img.getBoundingClientRect();
    if (box.width < 10 || box.height < 10) continue;
    const back = behind(img);
    if (!back) continue;
    const filter = css(img).filter;
    draw.clearRect(0, 0, 24, 24);
    draw.filter = filter && filter !== "none" ? filter : "none";
    let data;
    try {
      draw.drawImage(img, 0, 0, 24, 24);
      data = draw.getImageData(0, 0, 24, 24).data;
    } catch {
      continue; // ほかのサイトの絵: 読めない
    }
    draw.filter = "none";
    let solid = 0;
    const sum = [0, 0, 0];
    for (let i = 0; i < data.length; i += 4) {
      if (data[i + 3] < 128) continue;
      solid++;
      for (let c = 0; c < 3; c++) sum[c] += data[i + c];
    }
    if (!solid || solid / (data.length / 4) > 0.92) continue;
    const ink = over([...sum.map((part) => part / solid), faded(img)], back);
    const [high, low] = [light(ink), light(back)].sort((a, b) => b - a);
    const ratio = (high + 0.05) / (low + 0.05);
    if (ratio < 2)
      found.push({
        kind: "picture too faint",
        where: path(img),
        text: `${ratio.toFixed(2)} ${(img.alt || img.getAttribute("src") || "").slice(-40)}`,
      });
  }
  // 入力欄・セレクト・枠付きボタンの場所: 縁か塗りがページから浮いて見えないといけない。
  for (const el of document.querySelectorAll(
    "input:not([type=hidden], [type=radio], [type=checkbox], [type=range], [type=color]), select, textarea, button, .button, nav.modes a",
  )) {
    if (!shown(el) || el.closest("[hidden]") || folded(el)) continue;
    const style = css(el);
    const field = el.matches("input, select, textarea");
    const edged = parseFloat(style.borderTopWidth) > 0 && rgba(style.borderTopColor)[3] > 0;
    if (!field && !edged) continue; // 押す文字やアイコン: その色は上で測っている
    const back = behind(el.parentElement);
    if (!back) continue;
    const fill = over(rgba(style.backgroundColor), back);
    const edge = edged ? over(rgba(style.borderTopColor), back) : fill;
    const stands = (rgb) => {
      const [high, low] = [light(rgb), light(back)].sort((a, b) => b - a);
      return (high + 0.05) / (low + 0.05);
    };
    const ratio = Math.max(stands(fill), stands(edge));
    if (ratio < 2) found.push({ kind: "edge too faint", where: path(el), text: `${ratio.toFixed(2)} ${words(el)}` });
  }
  for (const img of document.images) {
    if (img.complete && img.naturalWidth === 0 && img.getAttribute("src"))
      found.push({ kind: "picture did not load", where: path(img), text: img.getAttribute("src").slice(-60) });
  }
  return found;
}

async function main() {
  const browser = await chromium.launch();
  const problems = new Map(); // "kind | page | where" -> {text, seen: "lang width theme" の Set}
  const add = (kind, page, where, text, at) => {
    const key = `${kind} | ${page} | ${where}`;
    if (!problems.has(key)) {
      problems.set(key, { text, seen: new Set() });
      // すぐにも出す: 途中で切られた実行（時間制限）でも見つけたものを伝える
      console.log(`found: ${key}${text ? "  “" + text + "”" : ""}  (${at})`);
    }
    problems.get(key).seen.add(at);
  };
  let pages = desktop ? DESKTOP_PAGES : live ? PAGES : PAGES.concat(MADE_UP);
  for (const theme of THEMES) {
    for (const lang of LANGS) {
      for (const width of WIDTHS) {
        const context = await browser.newContext({
          viewport: { width, height: 800 },
          colorScheme: theme,
          // 動き終わった見た目を測る（開いたときの本文のフェードの途中だと文字が薄く見える）
          reducedMotion: "reduce",
          locale: lang,
          timezoneId: ZONES[lang],
        });
        await context.addCookies([{ name: "lang", value: lang, url: base }]);
        const page = await context.newPage();
        const errors = [];
        page.on("console", (message) => message.type() === "error" && errors.push(message.text().slice(0, 120)));
        page.on("pageerror", (error) => errors.push(String(error).slice(0, 120)));
        page.on(
          "response",
          (answer) =>
            answer.status() >= 400 && errors.push(`${answer.status()} ${answer.url().replace(base, "").slice(0, 90)}`),
        );
        if (live && pages === PAGES) pages = PAGES.concat(await follow(page));
        process.stderr.write(`${theme} ${lang} ${width}px\n`);
        for (const where of pages) {
          errors.length = 0;
          // PC アプリのページは Riot Client の次のイベントを待つ（/live/next は 25 秒開いたまま）
          // : ネットが静かになるのを待たず、読み込んだら見る
          await page
            .goto(base + where, { waitUntil: desktop ? "load" : "networkidle", timeout: 60000 })
            .catch((error) => errors.push(String(error).slice(0, 120)));
          await page.waitForTimeout(desktop ? 700 : live ? 400 : 200);
          // 遅い本番では読み込みが 60 秒を過ぎてから次の文書に移ることがある: 移り終わるのを待ってもう一度だけ見る
          const found = await page.evaluate(inspect).catch(async (error) => {
            await page.waitForLoadState("load", { timeout: 30000 }).catch(() => {});
            return page.evaluate(inspect).catch(() => {
              errors.push(String(error).split("\n")[0].slice(0, 120));
              return [];
            });
          });
          for (const item of found) add(item.kind, where, item.where, item.text, `${lang} ${width} ${theme}`);
          // 動くはずなのに動かないと言うページ（そう言ってよいのは作りものの誰もいないページ、タグ無しの検索、
          // ロードアウトの無いコレクションだけ）
          const failed = await page
            .$eval(".problem, main [role=alert]", (el) => el.innerText.trim().slice(0, 60))
            .catch(() => "");
          const meant =
            where.startsWith("/p/nobody-here/") || where.startsWith("/search?") || where.includes("loadout=none");
          if (failed && !meant) add("page shows an error", where, "", failed, `${lang} ${width} ${theme}`);
          for (const error of errors) {
            // 誰もいないページはわざと 404 を返す（ブラウザーもコンソールでそう言う）
            if (where.startsWith("/p/nobody-here/") && error.includes("404")) continue;
            if (where.startsWith("/search?") && error.includes("400")) continue; // タグ無しの検索も
            add("error", where, error, "", `${lang} ${width} ${theme}`);
          }
        }
        await context.close();
      }
    }
  }
  if (!live && !desktop) await livePage(browser);
  await browser.close();
  for (const [kind, where, what, text] of early) add(kind, where, what, text, "once");
  console.log("\n--- all problems, and everywhere they show ---");
  for (const [key, { text, seen }] of [...problems].sort()) {
    const at = [...seen];
    console.log(
      `${key}${text ? "  “" + text + "”" : ""}\n    at ${at.length > 6 ? at.slice(0, 6).join(", ") + ` … (${at.length})` : at.join(", ")}`,
    );
  }
  console.log(`${problems.size} problem(s) on ${pages.length} pages`);
  process.exitCode = problems.size ? 1 : 0;
}

// /live はページを再読み込みせずに数秒ごとに読み直し、ボタンも送る（持ち主は毎回エージェントの中の位置を見失っていた）
// : タイマーとピックの間もページは同じ文書のまま、エージェントの絵は同じ要素のままで、
// 確認のたびに作り直して取り直さない（持ち主にはまた読み込むのが見えていた）。
async function livePage(browser) {
  const page = await browser.newPage({ viewport: { width: 390, height: 800 }, reducedMotion: "reduce" });
  let loads = 0;
  page.on("load", () => loads++);
  await page.goto(base + "/live?phase=pregame", { waitUntil: "networkidle", timeout: 60000 });
  const timer = () => page.$eval(".live-head", (head) => head.textContent).catch(() => "");
  const before = await timer();
  await page.evaluate(() => {
    window.auditMark = true;
    document.querySelectorAll(".pick-grid img, .seats img").forEach((img) => (img.auditKept = true));
  });
  let pictures = 0;
  const counting = (request) => request.url().includes("/media/") && pictures++;
  page.on("request", counting);
  await page.waitForTimeout(6500); // タイマー: エージェント選択では 2 秒ごと。作りもののタイマーは減っていく
  page.off("request", counting);
  const looked = (await timer()) !== before; // 作りもののタイマーが動いた: ページを読み直して差し込んだ
  const remade = await page.evaluate(
    () => [...document.querySelectorAll(".pick-grid img, .seats img")].filter((img) => !img.auditKept).length,
  );
  if (!looked) early.push(["/live not updated", "/live?phase=pregame", "", before]);
  if (remade || pictures)
    early.push(["pictures made again", "/live?phase=pregame", "", `${remade} remade, ${pictures} fetched`]);
  // 作りもののゲームのエージェント 1 つは味方のもの: それでも押す（作りものの Riot は受け付ける）
  const sent = page
    .waitForResponse((answer) => answer.url().includes("/live/act"), { timeout: 15000 })
    .then(() => true)
    .catch(() => false);
  const pressed = await page.evaluate(() => {
    const button = document.querySelector(".pick-grid button.pick");
    if (!button) return false;
    button.disabled = false;
    button.form.requestSubmit(button);
    return true;
  });
  if (!pressed) early.push(["no agent to pick", "/live?phase=pregame", "", ""]);
  else if (!(await sent)) early.push(["pick not sent", "/live?phase=pregame", "", ""]);
  await page.waitForTimeout(1500);
  const kept = await page.evaluate(() => window.auditMark === true).catch(() => false);
  if (!kept || loads !== 1) early.push(["page reloads", "/live?phase=pregame", "", `${loads} loads`]);
  else if (!(await page.locator(".verdict").count()))
    early.push(["no note after a pick", "/live?phase=pregame", "", ""]);
  await page.close();
}

// 本番のサイト: 本物のプレイヤー（ランキングに最初に出る人）、その最新の試合、エージェント。
async function follow(page) {
  const more = [];
  await page.goto(base + "/leaderboard?full=1", { waitUntil: "networkidle", timeout: 60000 });
  // ランキングの出どころ: Riot 自身のもの（持ち主の Riot アカウント）か HenrikDev のもの
  const source = await page.$eval("table.board", (table) => table.dataset.source).catch(() => "none");
  console.log("leaderboard from: " + source);
  const player = await page
    .$eval("table.board th[scope=row] a[href*='/p/']", (a) => a.getAttribute("href"))
    .catch(() => null);
  if (player) {
    const at = player.replace(new URL(base).pathname.replace(/\/$/, ""), "");
    more.push(at + "?full=1");
    // そのプレイヤーを Riot ID でランキングから探す
    const id = decodeURIComponent(at.split("/").slice(2, 4).join("#"));
    more.push("/leaderboard?q=" + encodeURIComponent(id) + "&full=1");
    await page.goto(base + at + "?full=1", { waitUntil: "networkidle", timeout: 90000 });
    const match = await page.$eval("a[href*='/m/']", (a) => a.getAttribute("href")).catch(() => null);
    if (match) more.push(match.replace(new URL(base).pathname.replace(/\/$/, ""), ""));
  }
  await page.goto(base + "/agents", { waitUntil: "networkidle", timeout: 60000 });
  const agent = await page.$eval("a[href*='/agents/']", (a) => a.getAttribute("href")).catch(() => null);
  if (agent) more.push(agent.replace(new URL(base).pathname.replace(/\/$/, ""), ""));
  // スキン図鑑の最初のスキン（ヴァンダルの最上位ティア: バリアント、レベル、動画）と、その動画が再生されること
  await page.goto(base + "/skins", { waitUntil: "networkidle", timeout: 60000 });
  const skin = await page.$eval("a.skin-link", (a) => a.getAttribute("href")).catch(() => null);
  if (skin) {
    const at = skin.replace(new URL(base).pathname.replace(/\/$/, ""), "");
    more.push(at);
    await page.goto(base + at, { waitUntil: "networkidle", timeout: 60000 });
    const video = await page.$eval("video source", (source) => source.src).catch(() => null);
    if (!video) early.push(["no video", at, "", ""]);
    else {
      const answer = await page.request.get(video, { headers: { Range: "bytes=0-1023" } }).catch(() => null);
      if (!answer || answer.status() >= 400 || !/video|octet/.test(answer.headers()["content-type"] || ""))
        early.push(["video does not play", at, video.slice(-70), answer ? String(answer.status()) : "no answer"]);
    }
  } else early.push(["no skin listed", "/skins", "", ""]);
  return more;
}

main().catch((error) => {
  console.error(error);
  // 開いたままのブラウザーで node が終わらず、ジョブが 40 分の時間切れまで止まっていた
  process.exit(2);
});
