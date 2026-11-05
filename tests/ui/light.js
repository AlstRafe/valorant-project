// Temporary probe: what is hard to see in light mode that audit.js does not measure (pictures and SVG drawings
// against what is behind them, text under a faded parent, placeholders, the edges of fields and buttons).
//   node tests/ui/light.js <base> [--live]
"use strict";
const { chromium } = require("playwright");
const [base, ...flags] = process.argv.slice(2);
const live = flags.includes("--live");
const PAGES = [
  "/", "/leaderboard?full=1", "/status", "/crosshair?code=0;P;c;5;h;0;0l;4;0o;2;0a;1;1b;0", "/shop", "/tools",
  "/servers", "/queues?rank=14&rank=17", "/sensitivity?sens=0.35&dpi=800&new_dpi=1600", "/agents", "/news",
  "/esports", "/esports?show=results", "/skins", "/skins?weapon=all&tier=ultra", "/agents/stats", "/compare",
  "/settings",
];
const MADE_UP = [
  "/p/Tester/JP1?full=1", "/m/ap/aaaaaaaa-0000-4000-8000-000000000001",
  "/agents/11111111-1111-4111-8111-111111111111", "/collection", "/skins/aa000000-0000-4000-8000-000000000001",
  "/shop/history", "/progress", "/live?phase=ingame", "/compare?a=Tester%23JP1&b=Other%23KR1",
];
function inspect() {
  const found = [];
  const path = (el) => {
    const parts = [];
    for (let node = el; node && node !== document.body && parts.length < 4; node = node.parentElement) {
      let part = node.tagName.toLowerCase();
      if (node.id) part += "#" + node.id;
      else if (node.classList && node.classList.length) part += "." + [...node.classList].slice(0, 2).join(".");
      parts.unshift(part);
    }
    return parts.join(" > ");
  };
  const css = (el) => getComputedStyle(el);
  const shown = (el) => {
    const s = css(el);
    const b = el.getBoundingClientRect();
    return s.visibility !== "hidden" && s.display !== "none" && b.width > 0 && b.height > 0 && !el.closest("[hidden], .sr");
  };
  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  const rgba = (value) => {
    canvas.width = canvas.height = 1;
    ctx.clearRect(0, 0, 1, 1);
    ctx.fillStyle = "#000";
    ctx.fillStyle = value;
    ctx.fillRect(0, 0, 1, 1);
    const [r, g, b, a] = ctx.getImageData(0, 0, 1, 1).data;
    return [r, g, b, a / 255];
  };
  const over = (top, under) => top.slice(0, 3).map((p, i) => p * top[3] + under[i] * (1 - top[3]));
  const behind = (el, skipSelf) => {
    const layers = [];
    for (let node = skipSelf ? el.parentElement : el; node; node = node.parentElement) {
      const s = css(node);
      if (s.backgroundImage !== "none") return null;
      const c = rgba(s.backgroundColor);
      if (c[3] > 0) layers.push(c);
      if (c[3] >= 1) break;
    }
    let under = [255, 255, 255];
    for (const layer of layers.reverse()) under = over(layer, under);
    return under;
  };
  const fade = (el) => {
    let o = 1;
    for (let n = el; n; n = n.parentElement) o *= +css(n).opacity;
    return o;
  };
  const lum = (rgb) => {
    const [r, g, b] = rgb.map((p) => {
      const c = p / 255;
      return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  const ratio = (a, b) => {
    const [h, l] = [lum(a), lum(b)].sort((x, y) => y - x);
    return (h + 0.05) / (l + 0.05);
  };
  const words = (el) => (el.innerText || el.getAttribute("aria-label") || el.alt || el.getAttribute("src") || "").trim().replace(/\s+/g, " ").slice(-50);
  // pictures: the mean colour of their drawn (opaque) pixels against what is behind them
  for (const img of document.images) {
    if (!img.complete || !img.naturalWidth || !shown(img)) continue;
    const box = img.getBoundingClientRect();
    if (box.width < 8 || box.height < 8) continue;
    const back = behind(img, true);
    if (!back) continue;
    const size = 32;
    canvas.width = canvas.height = size;
    ctx.clearRect(0, 0, size, size);
    const filter = css(img).filter;
    ctx.filter = filter && filter !== "none" ? filter : "none";
    let data;
    try {
      ctx.drawImage(img, 0, 0, size, size);
      data = ctx.getImageData(0, 0, size, size).data;
    } catch (e) {
      continue;
    }
    ctx.filter = "none";
    let n = 0, solid = 0, sum = [0, 0, 0];
    for (let i = 0; i < data.length; i += 4) {
      n++;
      if (data[i + 3] < 128) continue;
      solid++;
      sum[0] += data[i]; sum[1] += data[i + 1]; sum[2] += data[i + 2];
    }
    if (!solid || solid / n > 0.92) continue; // a photo or a card: it brings its own background
    const mean = sum.map((v) => v / solid);
    const o = fade(img);
    const seen = mean.map((v, i) => v * o + back[i] * (1 - o));
    const r = ratio(seen, back);
    if (r < 3) found.push({ kind: "icon faint", where: path(img), text: `${r.toFixed(2)} ${words(img)}` });
  }
  // SVG drawings
  for (const shape of document.querySelectorAll("svg path, svg rect, svg circle, svg line, svg polyline, svg polygon, svg text")) {
    if (!shown(shape.closest("svg"))) continue;
    const s = css(shape);
    const svg = shape.closest("svg");
    const back = behind(svg, true);
    if (!back) continue;
    for (const prop of ["fill", "stroke"]) {
      const v = s[prop];
      if (!v || v === "none" || v.startsWith("url")) continue;
      const c = rgba(v);
      if (c[3] === 0) continue;
      const o = fade(shape) * +(s[prop + "Opacity"] || 1);
      const ink = over([c[0], c[1], c[2], c[3] * o], back);
      const r = ratio(ink, back);
      const need = shape.tagName === "text" ? 4.5 : 3;
      if (r < need && !(prop === "stroke" && parseFloat(s.strokeWidth) === 0))
        found.push({ kind: `svg ${prop} faint`, where: path(shape), text: `${r.toFixed(2)} ${v} ${shape.tagName === "text" ? shape.textContent.trim().slice(0, 30) : ""}` });
    }
  }
  // text under a faded parent (audit.js skips any opacity below 1)
  const own = (el) => [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
  for (const el of document.body.querySelectorAll("*")) {
    if (!own(el) || el.closest("svg, script, style") || !shown(el)) continue;
    const o = fade(el);
    const back = behind(el, false);
    if (!back) {
      if (!el.closest("[style]")) found.push({ kind: "text on a picture (not measured)", where: path(el), text: `${css(el).color} ${words(el).slice(0, 25)}` });
      continue;
    }
    const c = rgba(css(el).color);
    const ink = over([c[0], c[1], c[2], c[3] * o], back);
    const r = ratio(ink, back);
    const size = parseFloat(css(el).fontSize);
    const large = size >= 24 || (size >= 18.6 && +css(el).fontWeight >= 700);
    const disabled = el.closest("[aria-disabled=true], :disabled");
    if (r < (large ? 3 : 4.5)) found.push({ kind: disabled ? "disabled text faint" : o < 1 ? "faded text faint" : "text faint", where: path(el), text: `${r.toFixed(2)} ${words(el).slice(0, 30)}` });
  }
  // placeholders and the edges of fields, buttons and chips
  for (const el of document.querySelectorAll("input:not([type=hidden]), textarea, select, button, .button, .chip, nav.modes a")) {
    if (!shown(el)) continue;
    const s = css(el);
    const own = rgba(s.backgroundColor);
    const back = behind(el, true);
    if (!back) continue;
    const fill = own[3] > 0 ? over(own, back) : back;
    if (el.matches("input, textarea") && el.placeholder) {
      const p = rgba(getComputedStyle(el, "::placeholder").color);
      const r = ratio(over(p, fill), fill);
      if (r < 4.5) found.push({ kind: "placeholder faint", where: path(el), text: `${r.toFixed(2)} ${el.placeholder}` });
    }
    const edge = parseFloat(s.borderTopWidth) > 0 ? rgba(s.borderTopColor) : null;
    const fillVsBack = ratio(fill, back);
    const edgeVsBack = edge ? ratio(over(edge, back), back) : 1;
    if (Math.max(fillVsBack, edgeVsBack) < 1.6)
      found.push({ kind: "control edge faint", where: path(el), text: `fill ${fillVsBack.toFixed(2)} edge ${edgeVsBack.toFixed(2)} ${words(el).slice(0, 20)}` });
  }
  return found;
}
(async () => {
  const browser = await chromium.launch();
  const problems = new Map();
  for (const width of [1000, 360]) {
    const context = await browser.newContext({ viewport: { width, height: 800 }, colorScheme: "light", locale: "ja" });
    await context.addCookies([{ name: "lang", value: "ja", url: base }]);
    const page = await context.newPage();
    let pages = live ? PAGES.slice() : PAGES.concat(MADE_UP);
    if (live) {
      await page.goto(base + "/leaderboard?full=1", { waitUntil: "networkidle", timeout: 60000 }).catch(() => {});
      const player = await page.$eval("table.board th[scope=row] a[href*='/p/']", (a) => a.getAttribute("href")).catch(() => null);
      const prefix = new URL(base).pathname.replace(/\/$/, "");
      if (player) {
        pages.push(player.replace(prefix, "") + "?full=1");
        await page.goto(base + player.replace(prefix, "") + "?full=1", { waitUntil: "networkidle", timeout: 90000 }).catch(() => {});
        const match = await page.$eval("a[href*='/m/']", (a) => a.getAttribute("href")).catch(() => null);
        if (match) pages.push(match.replace(prefix, ""));
      }
      await page.goto(base + "/agents", { waitUntil: "networkidle", timeout: 60000 }).catch(() => {});
      const agent = await page.$eval("a[href*='/agents/']:not([href$='/stats'])", (a) => a.getAttribute("href")).catch(() => null);
      if (agent) pages.push(agent.replace(prefix, ""));
      await page.goto(base + "/skins", { waitUntil: "networkidle", timeout: 60000 }).catch(() => {});
      const skin = await page.$eval("a.skin-link", (a) => a.getAttribute("href")).catch(() => null);
      if (skin) pages.push(skin.replace(prefix, ""));
    }
    for (const where of pages) {
      await page.goto(base + where, { waitUntil: "networkidle", timeout: 60000 }).catch(() => {});
      await page.waitForTimeout(500);
      for (const item of await page.evaluate(inspect)) {
        const key = `${item.kind} | ${where} | ${item.where}`;
        if (!problems.has(key)) problems.set(key, { text: item.text, at: new Set() });
        problems.get(key).at.add(width);
      }
    }
    await context.close();
  }
  await browser.close();
  for (const [key, { text, at }] of [...problems].sort()) console.log(`${key}  “${text}”  (${[...at].join(",")})`);
  console.log(`${problems.size} item(s)`);
})();
