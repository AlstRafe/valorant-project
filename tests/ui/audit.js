// Looks for broken layout on the site's pages: anything wider than the screen, text over text, text cut off,
// header tabs and buttons that wrap, tap targets too small on a phone, pictures that did not load, script or CSP
// errors, a page that says it could not load, and for screen readers and eyes: links and fields without a name,
// headings that skip, the same id twice, text too faint for its background (WCAG AA, a faded parent counted too),
// pictures drawn for a dark screen shown on paper (a white sign on white), and the edges of fields and buttons too
// faint to see where they are (WCAG 1.4.11). Every page in every language
// at the widths the site is designed for (docs/CONVENTIONS.md).
//
//   node tests/ui/audit.js <base, e.g. http://127.0.0.1:5077> [--live] [--quick]
//
// --live: the real site (fewer pages at once, follows the leaderboard to a real player and match). Exit code 1
// when something is found, with one line per problem and where it shows.
"use strict";

const { chromium } = require("playwright");

const [base, ...flags] = process.argv.slice(2);
const live = flags.includes("--live");
const quick = flags.includes("--quick");
const WIDTHS = [360, 760, 1000, 1440];
const LANGS = quick ? ["ja", "en-GB"] : ["ja", "ko", "en-GB", "zh-TW"];
const THEMES = live || quick ? ["light"] : ["light", "dark"];
// Each language's visitor in its usual clock (the servers page counts hours in the visitor's own).
const ZONES = { "ja": "Asia/Tokyo", "ko": "Asia/Seoul", "en-GB": "Europe/London", "zh-TW": "Asia/Taipei" };
const PAGES = [
  "/",
  "/leaderboard?full=1",
  "/status",
  "/crosshair?code=0;P;c;5;h;0;0l;4;0o;2;0a;1;1b;0",
  "/shop",
  "/tools",
  "/servers",
  "/queues?rank=14&rank=17",
  "/sensitivity?sens=0.35&dpi=800&new_dpi=1600",
  "/agents",
  "/news",
  "/esports",
  "/esports?show=results",
  "/skins",
  "/skins?weapon=all&tier=ultra",
  "/agents/stats",
  "/compare",
  "/p/nobody-here/0000",
];
// With the tests' made-up answers (tests/ui/serve.py): their player, match and agent.
const MADE_UP = [
  "/p/Tester/JP1?full=1",
  "/m/ap/aaaaaaaa-0000-4000-8000-000000000001",
  "/agents/11111111-1111-4111-8111-111111111111",
  "/collection",
  "/settings",
  "/skins/aa000000-0000-4000-8000-000000000001",
  "/skins/aa000000-0000-4000-8000-000000000001?chroma=aa000000-0000-4000-8000-000000000022",
  "/shop/history",
  "/progress",
  "/live",
  "/live?phase=pregame",
  "/live?phase=ingame",
  "/collection?show=cards",
  "/collection?show=titles",
  "/collection?show=sprays",
  "/compare?a=Tester%23JP1&b=Other%23KR1",
  "/agents/stats?queue=competitive",
];
// Found before the pages are looked at (the real site's skin video): added to the problems at the end.
const early = [];

// Runs in the page: every problem as {kind, where, text}.
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
  // Whether an ancestor scrolls or clips it (or it is in the fixed bar), and whether it is fixed or sticky: kept
  // per element, as the answer of its parent and its own style.
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
        box, // the part of the page its ancestors let it show in
      });
    }
    return kept.get(el);
  };
  // The part of an element that can be seen (what its ancestors clip is not).
  // An inline element that runs over two lines is one box per line: its whole box would cover the next words.
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
  // Inside a closed <details> (not its summary): not on the screen, though it still has a box.
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
      const tiny = el.clientWidth <= 1 || el.clientHeight <= 1; // a word kept for screen readers only
      if (((cutX && !/auto|scroll/.test(style.overflowX)) || cutY) && !tiny)
        found.push({ kind: "text cut off", where: path(el), text: words(el) });
    }
  }
  // Text over text (not the fixed tab bar over the page, which is meant).
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
  // One-line things that wrapped: the header, tabs, buttons, chips. Lines are counted from where the text's
  // letters are (a button stretched taller by its row is not wrapped).
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
    if (lines(el) > 1) found.push({ kind: "wraps onto two lines", where: path(el), text: words(el) });
  }
  if (width <= 760) {
    for (const el of document.querySelectorAll(
      "button, .button, select, input:not([type=hidden]), summary, nav a, .tabbar a",
    )) {
      if (!shown(el) || el.closest("[hidden]") || folded(el)) continue;
      // a check box or radio button is tapped through its label too (WCAG: the label is part of the target)
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
  // For screen readers and keyboards: names, labels, headings, ids.
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
  // Text too faint to read against what is behind it (WCAG AA: 4.5, or 3 for large text).
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
      if (style.backgroundImage !== "none") return null; // a picture or gradient: cannot tell
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
  // how much of an element shows through its own and its parents' opacity
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
  // A sign or icon (a picture with see-through parts) against what is behind it: the mean colour of its drawn
  // pixels, through its CSS filter. Photos and cards bring their own background and are not measured.
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
      continue; // another site's picture: cannot be read
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
  // Where a field, a select or a bordered button is: its edge or its fill has to stand out from the page.
  for (const el of document.querySelectorAll(
    "input:not([type=hidden], [type=radio], [type=checkbox], [type=range], [type=color]), select, textarea, button, .button, nav.modes a",
  )) {
    if (!shown(el) || el.closest("[hidden]") || folded(el)) continue;
    const style = css(el);
    const field = el.matches("input, select, textarea");
    const edged = parseFloat(style.borderTopWidth) > 0 && rgba(style.borderTopColor)[3] > 0;
    if (!field && !edged) continue; // a word or an icon that is pressed: its own colour is measured above
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
  const problems = new Map(); // "kind | page | where" -> {text, seen: Set of "lang width theme"}
  const add = (kind, page, where, text, at) => {
    const key = `${kind} | ${page} | ${where}`;
    if (!problems.has(key)) {
      problems.set(key, { text, seen: new Set() });
      // at once too, so a run that is cut short (a time limit) still tells what it found
      console.log(`found: ${key}${text ? "  “" + text + "”" : ""}  (${at})`);
    }
    problems.get(key).seen.add(at);
  };
  let pages = live ? PAGES : PAGES.concat(MADE_UP);
  for (const theme of THEMES) {
    for (const lang of LANGS) {
      for (const width of WIDTHS) {
        const context = await browser.newContext({
          viewport: { width, height: 800 },
          colorScheme: theme,
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
          await page
            .goto(base + where, { waitUntil: "networkidle", timeout: 60000 })
            .catch((error) => errors.push(String(error).slice(0, 120)));
          await page.waitForTimeout(live ? 400 : 200);
          for (const item of await page.evaluate(inspect))
            add(item.kind, where, item.where, item.text, `${lang} ${width} ${theme}`);
          // a page that should work but says it could not (only the made-up nobody's page is meant to)
          const failed = await page
            .$eval(".problem, main [role=alert]", (el) => el.innerText.trim().slice(0, 60))
            .catch(() => "");
          if (failed && !where.startsWith("/p/nobody-here/"))
            add("page shows an error", where, "", failed, `${lang} ${width} ${theme}`);
          for (const error of errors) {
            // the nobody's page answers 404 on purpose (and the browser says so in the console)
            if (where.startsWith("/p/nobody-here/") && error.includes("404")) continue;
            add("error", where, error, "", `${lang} ${width} ${theme}`);
          }
        }
        await context.close();
      }
    }
  }
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

// The real site: a real player (the leaderboard's first shown one), their latest match, an agent.
async function follow(page) {
  const more = [];
  await page.goto(base + "/leaderboard?full=1", { waitUntil: "networkidle", timeout: 60000 });
  const player = await page
    .$eval("table.board th[scope=row] a[href*='/p/']", (a) => a.getAttribute("href"))
    .catch(() => null);
  if (player) {
    const at = player.replace(new URL(base).pathname.replace(/\/$/, ""), "");
    more.push(at + "?full=1");
    await page.goto(base + at + "?full=1", { waitUntil: "networkidle", timeout: 90000 });
    const match = await page.$eval("a[href*='/m/']", (a) => a.getAttribute("href")).catch(() => null);
    if (match) more.push(match.replace(new URL(base).pathname.replace(/\/$/, ""), ""));
  }
  await page.goto(base + "/agents", { waitUntil: "networkidle", timeout: 60000 });
  const agent = await page.$eval("a[href*='/agents/']", (a) => a.getAttribute("href")).catch(() => null);
  if (agent) more.push(agent.replace(new URL(base).pathname.replace(/\/$/, ""), ""));
  // the skin library's first skin (the best tier of the Vandal: variants, levels, videos), and that its video plays
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
  process.exitCode = 2;
});
