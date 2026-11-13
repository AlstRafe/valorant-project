// VAL Tracker's pages: favourite and recently viewed players with their rank (kept in this browser), match times in
// the visitor's own time zone and the matches by day, sharing a page, the collection's name filter, and the phone's
// tab bar hidden while typing. The pages work without it, except for the lists of players, which live only in this
// browser.
(function () {
  "use strict";

  var FAVORITES = "val.favorites";
  var RECENT = "val.recent";
  var RECENT_MAX = 8;
  var FAVORITES_MAX = 50;

  function read(key) {
    try {
      var value = JSON.parse(localStorage.getItem(key) || "[]");
      return Array.isArray(value)
        ? value.filter(function (p) {
            return p && typeof p.name === "string" && typeof p.tag === "string";
          })
        : [];
    } catch (e) {
      return [];
    }
  }

  // A rank picture is one of our own (/val/media/...): anything else kept in the store is not shown.
  function ownPicture(address) {
    return typeof address === "string" && address.charAt(0) === "/" && address.charAt(1) !== "/" ? address : "";
  }

  function write(key, list) {
    try {
      localStorage.setItem(key, JSON.stringify(list));
    } catch (e) {
      // private browsing or a full store: the list is just not kept
    }
  }

  function same(a, b) {
    return a.name.toLowerCase() === b.name.toLowerCase() && a.tag.toLowerCase() === b.tag.toLowerCase();
  }

  function without(list, person) {
    return list.filter(function (p) {
      return !same(p, person);
    });
  }

  // The lists on the top page, built from a <template> (text only: names are never read as HTML).
  function fill(list, people) {
    people = people || read(list.id === "favorites" ? FAVORITES : RECENT);
    var template = document.getElementById("person");
    list.textContent = "";
    if (!people.length) {
      var empty = document.createElement("li");
      empty.className = "empty";
      empty.textContent = list.getAttribute("data-empty");
      list.appendChild(empty);
      return;
    }
    people.forEach(function (p) {
      var item = template.content.firstElementChild.cloneNode(true);
      var link = item.querySelector("a");
      link.href = list.getAttribute("data-base") + encodeURIComponent(p.name) + "/" + encodeURIComponent(p.tag);
      item.querySelector(".pname").textContent = p.name;
      item.querySelector(".ptag").textContent = "#" + p.tag;
      var picture = item.querySelector(".prank");
      if (picture && ownPicture(p.icon)) {
        picture.src = p.icon;
        picture.alt = typeof p.rank === "string" ? p.rank : "";
        picture.title = picture.alt;
        picture.hidden = false;
      }
      list.appendChild(item);
    });
  }

  function lists() {
    ["favorites", "recent"].forEach(function (id) {
      var list = document.getElementById(id);
      if (list) fill(list);
    });
    known();
  }

  // Everyone kept in this browser, each once: the favourites first.
  function kept() {
    var found = [];
    read(FAVORITES)
      .concat(read(RECENT))
      .forEach(function (p) {
        if (
          !found.some(function (q) {
            return same(p, q);
          })
        )
          found.push(p);
      });
    return found;
  }

  // The search box suggests the players kept here as "Name#TAG" (typing a name shows its tag), and a search with a
  // name but no tag shows the kept players of that name, whose tag the visitor may have forgotten.
  function known() {
    var choices = document.getElementById("known");
    if (choices) {
      choices.textContent = "";
      kept().forEach(function (p) {
        var option = document.createElement("option");
        option.value = p.name + "#" + p.tag;
        choices.appendChild(option);
      });
    }
    var list = document.getElementById("same-name");
    if (!list) return;
    var name = (list.getAttribute("data-name") || "").toLowerCase();
    var people = kept().filter(function (p) {
      return p.name.toLowerCase() === name;
    });
    if (!people.length) return;
    fill(list, people);
    list.parentNode.hidden = false;
  }

  // A player's page: remember the visit, and the ☆ button.
  function profile() {
    var box = document.getElementById("profile");
    if (!box) return;
    var person = { name: box.getAttribute("data-name"), tag: box.getAttribute("data-tag") };
    if (box.getAttribute("data-rank")) {
      person.rank = box.getAttribute("data-rank");
      person.icon = ownPicture(box.getAttribute("data-icon"));
    }
    write(RECENT, [person].concat(without(read(RECENT), person)).slice(0, RECENT_MAX));
    // a favourite keeps the rank it was last seen with
    var kept = read(FAVORITES);
    if (
      person.rank &&
      kept.some(function (p) {
        return same(p, person);
      })
    ) {
      write(
        FAVORITES,
        kept.map(function (p) {
          return same(p, person) ? person : p;
        }),
      );
    }
    var button = document.getElementById("fav");
    if (!button) return;
    function show() {
      var on = read(FAVORITES).some(function (p) {
        return same(p, person);
      });
      var words = button.getAttribute(on ? "data-on" : "data-off");
      button.setAttribute("aria-pressed", on ? "true" : "false");
      button.setAttribute("aria-label", words);
      button.title = words;
    }
    button.hidden = false;
    show();
    button.addEventListener("click", function () {
      var favorites = read(FAVORITES);
      var on = favorites.some(function (p) {
        return same(p, person);
      });
      write(FAVORITES, on ? without(favorites, person) : favorites.concat([person]).slice(-FAVORITES_MAX));
      show();
    });
    addEventListener("storage", function (e) {
      if (e.key === FAVORITES || e.key === null) show();
    });
  }

  // A player's page comes at once without the rank and matches (HenrikDev takes seconds): asked for here and put
  // in place. A failure leaves the way to the page that waits for them (?full=1).
  function part() {
    var box = document.getElementById("player");
    if (!box) return;
    var address = box.getAttribute("data-part");
    if (address) load(box, address, "");
    // The mode filter: only the matches change, so only they are asked for again (the account and rank are kept).
    box.addEventListener("click", function (e) {
      var link = e.target.closest ? e.target.closest(".modes a") : null;
      if (!link || !window.fetch || e.button || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
      e.preventDefault();
      if (link.getAttribute("aria-current")) return;
      link.parentNode.querySelectorAll("a").forEach(function (other) {
        other.removeAttribute("aria-current");
      });
      link.setAttribute("aria-current", "true");
      var games = document.getElementById("games");
      if (games) games.setAttribute("aria-busy", "true");
      var asked = new URL(link.href);
      asked.pathname += "/part";
      load(box, asked.href, link.href);
    });
  }

  // The act rank: every act is on the page; the list shows the one chosen (without a script, the latest).
  function acts() {
    var pick = document.getElementById("act-pick");
    if (!pick || !pick.hidden) return;
    pick.hidden = false;
    pick.addEventListener("change", function () {
      document.querySelectorAll(".act").forEach(function (act) {
        act.hidden = act.getAttribute("data-act") !== pick.value;
      });
    });
  }

  // A choice that is a page of its own (the act a player's numbers are counted over): shown when chosen, without
  // the button a browser without a script needs.
  function autoSubmit() {
    document.querySelectorAll("select[data-auto-submit]").forEach(function (pick) {
      var button = pick.form && pick.form.querySelector("button[type=submit]");
      if (!button || button.hidden) return;
      button.hidden = true;
      pick.addEventListener("change", function () {
        pick.form.submit();
      });
    });
  }

  // A mode further along the row (Premier on a phone) is scrolled into sight.
  function chosen() {
    var current = document.querySelector(".modes a[aria-current]");
    var row = current && current.parentNode;
    if (!row || row.scrollWidth <= row.clientWidth) return;
    var left = current.offsetLeft - row.offsetLeft;
    if (left < row.scrollLeft || left + current.offsetWidth > row.scrollLeft + row.clientWidth) {
      row.scrollLeft = left - (row.clientWidth - current.offsetWidth) / 2;
    }
  }

  // page: the address the visitor chose with the filter ("" for the first load).
  function load(box, address, page) {
    function failed() {
      if (page) return location.assign(page); // the plain page for that mode
      if (!box.querySelector(".wait")) return location.reload(); // "最新にする" on a page already shown
      var wait = box.querySelector(".wait");
      if (wait) wait.textContent = box.getAttribute("data-failed");
      var retry = box.querySelector(".retry");
      if (retry) retry.hidden = false;
    }
    if (!window.fetch) return failed();
    fetch(address, { headers: { Accept: "text/html" }, credentials: "same-origin" })
      .then(function (answer) {
        // a missing player or a busy HenrikDev is a part of the page too (404, 503), with its words
        if (answer.status >= 500 && answer.status !== 503) throw new Error(String(answer.status));
        return answer.text();
      })
      .then(function (html) {
        var fresh = document.createElement("template");
        fresh.innerHTML = html; // our own server's page, escaped by its templates
        var games = page ? document.getElementById("games") : null;
        var found = fresh.content.getElementById("games");
        if (games && found) {
          games.replaceWith(found);
          history.replaceState(null, "", page);
        } else {
          box.innerHTML = html;
          box.removeAttribute("data-part");
          box.removeAttribute("aria-busy");
          profile();
          if (/[?&]fresh=/.test(location.search)) {
            var here = new URL(location.href); // read again once: a reload or a shared link need not
            here.searchParams.delete("fresh");
            history.replaceState(null, "", here.href);
          }
        }
        chosen();
        acts();
        autoSubmit();
        times();
        days();
        mapPick();
        statCard();
        share();
        showCopies();
      })
      .catch(failed);
  }

  // Other parts that HenrikDev takes seconds for (a page of the leaderboard, the top page's notice) are asked for
  // the same way: [data-load] is their address. A quiet one ([data-quiet]) shows nothing when it fails.
  function parts() {
    document.querySelectorAll("[data-load]").forEach(function (box) {
      var address = box.getAttribute("data-load");
      box.removeAttribute("data-load");
      function failed() {
        if (box.hasAttribute("data-quiet")) return;
        var wait = box.querySelector(".wait");
        if (wait) wait.textContent = box.getAttribute("data-failed");
        var retry = box.querySelector(".retry");
        if (retry) retry.hidden = false;
      }
      if (!window.fetch) return failed();
      fetch(address, { headers: { Accept: "text/html" }, credentials: "same-origin" })
        .then(function (answer) {
          if (answer.status >= 500 && answer.status !== 503) throw new Error(String(answer.status));
          return answer.text();
        })
        .then(function (html) {
          box.innerHTML = html; // our own server's part, escaped by its templates
          box.hidden = !html.trim();
          times();
        })
        .catch(failed);
    });
  }

  // [data-copy]: its words to the clipboard (a crosshair code, a Riot ID), and the button says so for a moment.
  // One that is only a picture (an icon) keeps it, and says so to screen readers and in its title instead.
  function showCopies() {
    if (!navigator.clipboard) return;
    document.querySelectorAll("[data-copy][hidden]").forEach(function (button) {
      button.hidden = false;
    });
  }
  function copies() {
    if (!navigator.clipboard) return;
    showCopies();
    document.addEventListener("click", function (e) {
      var button = e.target.closest ? e.target.closest("[data-copy]") : null;
      if (!button) return;
      navigator.clipboard.writeText(button.getAttribute("data-copy")).then(function () {
        if (button.querySelector("svg")) {
          var said = button.querySelector("[role=status]") || button.appendChild(document.createElement("span"));
          var title = button.title;
          said.className = "sr";
          said.setAttribute("role", "status");
          said.textContent = button.getAttribute("data-done");
          button.setAttribute("data-state", "copied");
          button.title = said.textContent;
          setTimeout(function () {
            button.removeAttribute("data-state");
            button.title = title;
            said.textContent = "";
          }, 1600);
          return;
        }
        var before = button.textContent;
        button.textContent = button.getAttribute("data-done") || before;
        setTimeout(function () {
          button.textContent = before;
        }, 1600);
      });
    });
  }

  // The store's offers end at a set time: the time left is counted down each minute.
  function countdowns() {
    var boxes = document.querySelectorAll("[data-until]");
    if (!boxes.length) return;
    function tick() {
      var now = Date.now() / 1000;
      boxes.forEach(function (box) {
        var left = Math.ceil((Number(box.getAttribute("data-until")) - now) / 60);
        var words;
        if (left <= 0) words = box.getAttribute("data-over");
        else if (left >= 2 * 24 * 60) words = box.getAttribute("data-days").replace("{days}", Math.floor(left / 1440));
        else {
          words = box
            .getAttribute("data-hours")
            .replace("{hours}", Math.floor(left / 60))
            .replace("{minutes}", left % 60);
        }
        box.textContent = words;
      });
    }
    tick();
    setInterval(tick, 30000);
  }

  // Share a player's or a match's page: the phone's own share sheet, else the address copied.
  function share() {
    var button = document.getElementById("share");
    if (!button || !button.hidden) return;
    var copy = navigator.clipboard && navigator.clipboard.writeText;
    if (!navigator.share && !copy) return;
    button.hidden = false;
    button.addEventListener("click", function () {
      var address = location.href.split("#")[0];
      // the PC app's own address (127.0.0.1) leads nowhere for anyone else: the public site's
      var site = document.body.getAttribute("data-public");
      if (site) address = site + location.pathname + location.search;
      if (navigator.share) {
        navigator.share({ title: document.title, url: address }).catch(function () {});
        return;
      }
      navigator.clipboard.writeText(address).then(function () {
        var said = button.querySelector("[role=status]") || button.appendChild(document.createElement("span"));
        said.className = "sr";
        said.setAttribute("role", "status");
        said.textContent = button.getAttribute("data-copied");
        button.setAttribute("data-state", "copied");
        button.title = said.textContent;
        setTimeout(function () {
          button.removeAttribute("data-state");
          said.textContent = "";
        }, 2500);
      });
    });
  }

  // A player's matches by day in the visitor's own time zone, each day with its wins, losses and RR.
  function days() {
    var list = document.querySelector(".match-list[data-day]");
    if (!list || !window.Intl) return;
    // asked again after the map filter: the heads are made anew for the matches shown
    list.querySelectorAll(".day").forEach(function (head) {
      head.remove();
    });
    var format;
    var dated; // a day of another year (a lifetime goes back years): with its year
    try {
      format = new Intl.DateTimeFormat(document.documentElement.lang, {
        month: "short",
        day: "numeric",
        weekday: "short",
      });
      dated = new Intl.DateTimeFormat(document.documentElement.lang, {
        year: "numeric",
        month: "short",
        day: "numeric",
        weekday: "short",
      });
    } catch (e) {
      return;
    }
    var year = new Date().getFullYear();
    var groups = [];
    list.querySelectorAll("li.match").forEach(function (item) {
      if (item.hidden) return;
      var time = item.querySelector("time[datetime]");
      var moment = time ? new Date(time.getAttribute("datetime")) : null;
      if (!moment || isNaN(moment)) return;
      var key = moment.getFullYear() + "-" + moment.getMonth() + "-" + moment.getDate();
      var last = groups[groups.length - 1];
      if (!last || last.key !== key) {
        var label = (moment.getFullYear() === year ? format : dated).format(moment);
        last = { key: key, label: label, items: [] };
        groups.push(last);
      }
      last.items.push(item);
    });
    groups.forEach(function (group) {
      var wins = 0;
      var losses = 0;
      var rr = 0;
      var rated = false;
      var kills = 0;
      var deaths = 0;
      group.items.forEach(function (item) {
        var outcome = item.getAttribute("data-outcome");
        if (outcome === "win") wins += 1;
        if (outcome === "loss") losses += 1;
        kills += Number(item.getAttribute("data-k")) || 0;
        deaths += Number(item.getAttribute("data-d")) || 0;
        var change = item.getAttribute("data-rr");
        if (change !== null && !isNaN(Number(change))) {
          rr += Number(change);
          rated = true;
        }
      });
      var head = document.createElement("li");
      head.className = "day";
      var title = document.createElement("strong");
      title.textContent = group.label;
      var record = document.createElement("span");
      record.textContent = list.getAttribute("data-day").replace("{wins}", wins).replace("{losses}", losses);
      head.appendChild(title);
      head.appendChild(record);
      if (kills || deaths) {
        // the day's K/D, as tracker.gg's day heads have it
        record.appendChild(document.createTextNode(" · K/D " + (deaths ? kills / deaths : kills).toFixed(2)));
      }
      if (rated) {
        var net = document.createElement("span");
        net.className = rr > 0 ? "up" : rr < 0 ? "down" : "";
        net.textContent = (rr > 0 ? "+" : "") + rr + " RR";
        record.appendChild(document.createTextNode(" · "));
        record.appendChild(net);
      }
      list.insertBefore(head, group.items[0]);
    });
  }

  // A match: a round of the line at the top opens its kills further down (the address's #r5 too).
  function rounds() {
    function open(hash) {
      var target = /^#r\d+$/.test(hash) ? document.getElementById(hash.slice(1)) : null;
      if (target && target.tagName === "DETAILS") target.open = true;
    }
    if (!document.querySelector(".timeline")) return;
    open(location.hash);
    addEventListener("hashchange", function () {
      open(location.hash);
    });
    document.querySelector(".timeline").addEventListener("click", function (e) {
      var link = e.target.closest ? e.target.closest("a[href^='#r']") : null;
      if (link) open(link.getAttribute("href"));
    });
  }

  // Times are written in Japan's time by the server; shown in the visitor's own zone and language.
  function times() {
    if (!window.Intl || !Intl.DateTimeFormat) return;
    var format;
    try {
      format = new Intl.DateTimeFormat(document.documentElement.lang, {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
      });
    } catch (e) {
      return;
    }
    document.querySelectorAll("time[datetime]").forEach(function (el) {
      var moment = new Date(el.getAttribute("datetime"));
      if (!isNaN(moment)) el.textContent = format.format(moment);
    });
  }

  // The servers page counts the hours in the visitor's own clock: their offset from UTC in a cookie (minutes), and
  // the page once more when it was counted in another clock. Only when the cookie changed, so never in a loop.
  function zone() {
    var minutes = -new Date().getTimezoneOffset();
    if (!isFinite(minutes)) return;
    var had = (document.cookie.match(/(?:^|; )tz=(-?\d+)/) || [])[1];
    if (had === String(minutes)) return;
    document.cookie =
      "tz=" + minutes + "; path=/; max-age=31536000; SameSite=Lax" + (location.protocol === "https:" ? "; Secure" : "");
    var page = document.querySelector("[data-offset]");
    if (page && page.getAttribute("data-offset") !== String(minutes)) location.reload();
  }

  // Esports: the teams the visitor marked (kept in this browser), their matches lit up and, if asked, alone; the
  // matches by day in the visitor's own clock.
  var TEAMS = "val.teams";

  function marked() {
    try {
      var value = JSON.parse(localStorage.getItem(TEAMS) || "[]");
      return Array.isArray(value)
        ? value.filter(function (name) {
            return typeof name === "string";
          })
        : [];
    } catch (e) {
      return [];
    }
  }

  function teams() {
    var list = document.querySelector(".schedule");
    if (!list) return;
    var only = document.getElementById("fav-only");
    function paint() {
      var names = marked();
      list.querySelectorAll(".game").forEach(function (game) {
        var any = false;
        game.querySelectorAll(".team-row").forEach(function (row) {
          var name = row.getAttribute("data-team");
          var on = names.indexOf(name) >= 0;
          any = any || on;
          var star = row.querySelector(".star");
          star.hidden = false;
          star.setAttribute("aria-pressed", on ? "true" : "false");
          star.querySelector(".sr").textContent = list
            .getAttribute(on ? "data-unmark" : "data-mark")
            .replace("{team}", name);
        });
        game.classList.toggle("fav", any);
        game.hidden = Boolean(only && only.checked && !any);
      });
      if (only) only.parentNode.parentNode.hidden = names.length === 0;
    }
    list.addEventListener("click", function (e) {
      var star = e.target.closest ? e.target.closest(".star") : null;
      if (!star) return;
      var name = star.parentNode.getAttribute("data-team");
      var names = marked();
      var at = names.indexOf(name);
      if (at >= 0) names.splice(at, 1);
      else names.push(name);
      write(TEAMS, names.slice(-100));
      paint();
    });
    if (only) only.addEventListener("change", paint);
    paint();
    dayHeads(list, ".game");
  }

  // A heading before each day's first item: the day in the visitor's own clock and language.
  function dayHeads(list, items) {
    if (!window.Intl || list.querySelector(".day")) return;
    var format;
    var dated; // a day of another year (a lifetime goes back years): with its year
    try {
      format = new Intl.DateTimeFormat(document.documentElement.lang, {
        month: "short",
        day: "numeric",
        weekday: "short",
      });
      dated = new Intl.DateTimeFormat(document.documentElement.lang, {
        year: "numeric",
        month: "short",
        day: "numeric",
        weekday: "short",
      });
    } catch (e) {
      return;
    }
    var year = new Date().getFullYear();
    var last = "";
    list.querySelectorAll(items).forEach(function (item) {
      var time = item.querySelector("time[datetime]");
      var moment = time ? new Date(time.getAttribute("datetime")) : null;
      if (!moment || isNaN(moment)) return;
      var key = moment.getFullYear() + "-" + moment.getMonth() + "-" + moment.getDate();
      if (key === last) return;
      last = key;
      var head = document.createElement("li");
      head.className = "day";
      var title = document.createElement("strong");
      title.textContent = (moment.getFullYear() === year ? format : dated).format(moment);
      head.appendChild(title);
      list.insertBefore(head, item);
    });
  }

  // A player's matches by map: the maps they were played on, as a choice above the list (the matches stay on the
  // page, only hidden).
  function mapPick() {
    var list = document.querySelector(".match-list");
    var box = document.getElementById("map-pick");
    if (!list || !box || box.options.length > 1) return;
    var names = [];
    list.querySelectorAll("li.match[data-map]").forEach(function (item) {
      var name = item.getAttribute("data-map");
      if (name && names.indexOf(name) < 0) names.push(name);
    });
    if (names.length < 2) return;
    names.sort().forEach(function (name) {
      var option = document.createElement("option");
      option.value = name;
      option.textContent = name;
      box.appendChild(option);
    });
    box.closest(".map-pick").hidden = false;
    box.addEventListener("change", function () {
      list.querySelectorAll("li.match").forEach(function (item) {
        item.hidden = box.value !== "" && item.getAttribute("data-map") !== box.value;
      });
      days();
    });
  }

  // A picture of a player's numbers to save or send (1200 × 630, the size link previews use). Every picture on it is
  // this site's own (/media/), so the canvas can be saved.
  function picture(address) {
    return new Promise(function (done) {
      if (!address) return done(null);
      var image = new Image();
      image.onload = function () {
        done(image);
      };
      image.onerror = function () {
        done(null);
      };
      image.src = address;
    });
  }

  function fitted(context, words, width) {
    var said = String(words);
    while (said.length > 1 && context.measureText(said).width > width) said = said.slice(0, -2) + "…";
    return said;
  }

  function drawCard(card, images) {
    var canvas = document.createElement("canvas");
    canvas.width = 1200;
    canvas.height = 630;
    var c = canvas.getContext("2d");
    var font = 'system-ui, -apple-system, "Segoe UI", "Hiragino Sans", "Noto Sans JP", "Noto Sans KR", sans-serif';
    c.fillStyle = "#161513";
    c.fillRect(0, 0, 1200, 630);
    if (images.wide) {
      c.globalAlpha = 0.35;
      c.drawImage(images.wide, 0, 0, 1200, 340);
      c.globalAlpha = 1;
      var fade = c.createLinearGradient(0, 120, 0, 340);
      fade.addColorStop(0, "rgba(22,21,19,0)");
      fade.addColorStop(1, "#161513");
      c.fillStyle = fade;
      c.fillRect(0, 0, 1200, 340);
    }
    c.fillStyle = "#ff4655";
    c.fillRect(0, 0, 12, 630);
    c.textBaseline = "alphabetic";
    c.fillStyle = "#f6f4ef";
    c.font = "700 64px " + font;
    var name = fitted(c, card.name, 760);
    c.fillText(name, 64, 120);
    var after = c.measureText(name).width;
    c.fillStyle = "#b9b4aa";
    c.font = "500 40px " + font;
    c.fillText("#" + card.tag, 64 + after + 12, 120);
    var left = 64;
    if (images.rank) {
      c.drawImage(images.rank, 64, 150, 88, 88);
      left = 172;
    }
    c.fillStyle = "#f6f4ef";
    c.font = "700 40px " + font;
    c.fillText(fitted(c, card.rank, 600), left, 196);
    c.fillStyle = "#b9b4aa";
    c.font = "500 26px " + font;
    c.fillText(fitted(c, card.under, 700), left, 234);
    c.fillStyle = "#b9b4aa";
    c.font = "600 26px " + font;
    c.fillText(fitted(c, card.title, 1080), 64, 312);
    card.stats.forEach(function (stat, index) {
      var x = 64 + (index % 3) * 360;
      var y = 330 + Math.floor(index / 3) * 110;
      c.fillStyle = "rgba(255,255,255,0.06)";
      c.fillRect(x, y, 340, 96);
      c.fillStyle = "#b9b4aa";
      c.font = "500 24px " + font;
      c.fillText(stat[0], x + 20, y + 34);
      c.fillStyle = "#f6f4ef";
      c.font = "700 40px " + font;
      c.fillText(String(stat[1]), x + 20, y + 80);
    });
    var x = 64;
    card.agents.forEach(function (agent, index) {
      var image = images.agents[index];
      if (image) {
        c.drawImage(image, x, 560, 44, 44);
        x += 54;
      }
      c.fillStyle = "#f6f4ef";
      c.font = "600 26px " + font;
      var said = fitted(c, agent.name, 220);
      c.fillText(said, x, 592);
      x += c.measureText(said).width + 32;
    });
    c.fillStyle = "#b9b4aa";
    c.font = "500 24px " + font;
    c.textAlign = "right";
    c.fillText(card.site, 1136, 592);
    return canvas;
  }

  function statCard() {
    var button = document.getElementById("stat-card");
    if (!button || !button.hidden || !window.Promise || !window.URL) return;
    var card;
    try {
      card = JSON.parse(button.getAttribute("data-card"));
    } catch (e) {
      return;
    }
    var canvas = document.createElement("canvas");
    if (!canvas.getContext || !canvas.toBlob) return;
    button.hidden = false;
    button.addEventListener("click", function () {
      if (button.getAttribute("aria-busy")) return;
      button.setAttribute("aria-busy", "true");
      Promise.all(
        [picture(card.wide), picture(card.rankIcon)].concat(
          card.agents.map(function (agent) {
            return picture(agent.icon);
          }),
        ),
      )
        .then(function (found) {
          var drawn = drawCard(card, { wide: found[0], rank: found[1], agents: found.slice(2) });
          drawn.toBlob(function (blob) {
            button.removeAttribute("aria-busy");
            if (!blob) return;
            var named = (card.name + "_" + card.tag).replace(/[^\w\-぀-鿿가-힯]+/g, "_") + ".png";
            var file = window.File ? new File([blob], named, { type: "image/png" }) : null;
            if (
              file &&
              navigator.canShare &&
              navigator.canShare({ files: [file] }) &&
              matchMedia("(pointer: coarse)").matches
            ) {
              navigator.share({ files: [file], title: document.title }).catch(function () {});
              return;
            }
            var link = document.createElement("a");
            link.href = URL.createObjectURL(blob);
            link.download = named;
            document.body.appendChild(link);
            link.click();
            link.remove();
            setTimeout(function () {
              URL.revokeObjectURL(link.href);
            }, 10000);
          }, "image/png");
        })
        .catch(function () {
          button.removeAttribute("aria-busy");
        });
    });
  }

  // Crosshairs kept in this browser: the one shown can be saved with a name, and the list is on the page.
  var CROSSHAIRS = "val.crosshairs";
  var CROSSHAIRS_MAX = 30;

  function crosshairs() {
    var box = document.getElementById("xh-saved");
    if (!box) return;
    var form = document.getElementById("xh-save");
    var list = box.querySelector("ul");
    function kept() {
      var found;
      try {
        found = JSON.parse(localStorage.getItem(CROSSHAIRS) || "[]");
      } catch (e) {
        found = [];
      }
      return (Array.isArray(found) ? found : []).filter(function (item) {
        return item && typeof item.code === "string" && item.code.length <= 600 && typeof item.name === "string";
      });
    }
    function button(words, kind) {
      var made = document.createElement("button");
      made.type = "button";
      made.className = "quiet " + kind;
      made.textContent = words;
      return made;
    }
    function show() {
      var items = kept();
      list.textContent = "";
      items.forEach(function (item) {
        var row = document.createElement("li");
        var name = document.createElement("strong");
        name.textContent = item.name || box.getAttribute("data-unnamed");
        var code = document.createElement("code");
        code.textContent = item.code;
        var actions = document.createElement("p");
        var link = document.createElement("a");
        link.className = "button quiet";
        link.href = box.getAttribute("data-show") + "?code=" + encodeURIComponent(item.code);
        link.textContent = box.getAttribute("data-view");
        actions.appendChild(link);
        if (navigator.clipboard) {
          var copy = button(box.getAttribute("data-copy-word"), "copy");
          copy.setAttribute("data-copy", item.code);
          copy.setAttribute("data-done", box.getAttribute("data-done"));
          actions.appendChild(document.createTextNode(" "));
          actions.appendChild(copy);
        }
        var remove = button(box.getAttribute("data-remove"), "remove");
        remove.addEventListener("click", function () {
          write(
            CROSSHAIRS,
            kept().filter(function (other) {
              return other.code !== item.code;
            }),
          );
          show();
        });
        actions.appendChild(document.createTextNode(" "));
        actions.appendChild(remove);
        row.appendChild(name);
        row.appendChild(code);
        row.appendChild(actions);
        list.appendChild(row);
      });
      box.hidden = !items.length;
    }
    show();
    if (!form) return;
    form.hidden = false;
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var code = form.getAttribute("data-code");
      var name = form.querySelector("input[name=name]").value.trim().slice(0, 30);
      var items = kept().filter(function (item) {
        return item.code !== code;
      });
      items.unshift({ code: code, name: name });
      write(CROSSHAIRS, items.slice(0, CROSSHAIRS_MAX));
      form.querySelector("[role=status]").textContent = form.getAttribute("data-saved");
      show();
    });
    addEventListener("storage", function (e) {
      if (e.key === CROSSHAIRS || e.key === null) show();
    });
  }

  // The night market: the cards the game has not shown yet lie face down until turned, one by one or all at once,
  // as in the game. Which ones were turned is remembered for that night market only.
  var NIGHT = "val.night";

  function turned(key) {
    try {
      var value = JSON.parse(localStorage.getItem(NIGHT) || "{}");
      return value && value.key === key && Array.isArray(value.turned) ? value.turned : [];
    } catch (e) {
      return [];
    }
  }

  function night() {
    var shelf = document.querySelector(".night[data-night]");
    if (!shelf) return;
    var key = shelf.getAttribute("data-night");
    var all = shelf.querySelector(".flip-all");
    var cards = shelf.querySelectorAll(".item[data-flip]");
    function turn(card, keep) {
      card.classList.remove("face-down");
      card.classList.add("turned");
      var button = card.querySelector(".flip");
      if (button) button.remove();
      if (!keep) return;
      var list = turned(key);
      list.push(card.getAttribute("data-flip"));
      try {
        localStorage.setItem(NIGHT, JSON.stringify({ key: key, turned: list }));
      } catch (e) {
        // not kept: they lie face down again next time
      }
      if (all) all.hidden = !shelf.querySelector(".face-down");
    }
    var list = turned(key);
    cards.forEach(function (card) {
      if (list.indexOf(card.getAttribute("data-flip")) >= 0) return;
      card.classList.add("face-down");
      var button = document.createElement("button");
      button.type = "button";
      button.className = "flip";
      button.textContent = (all && all.getAttribute("data-flip")) || "めくる";
      button.addEventListener("click", function () {
        turn(card, true);
      });
      card.appendChild(button);
    });
    if (all && shelf.querySelector(".face-down")) {
      all.hidden = false;
      all.querySelector("button").addEventListener("click", function () {
        shelf.querySelectorAll(".face-down").forEach(function (card) {
          turn(card, true);
        });
      });
    }
  }

  // The owner's match being played: the page is read again every few seconds while it is in view.
  // A player's "最新にする": the part is asked for again in place (the scroll stays); without JavaScript the link
  // opens the whole page read again.
  function readAgain() {
    var box = document.getElementById("player");
    if (!box) return;
    box.addEventListener("click", function (e) {
      var link = e.target.closest ? e.target.closest("a.refresh") : null;
      if (!link || !window.fetch || e.button || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
      e.preventDefault();
      if (!link.getAttribute("href")) return; // already asked
      link.removeAttribute("href");
      link.textContent = link.getAttribute("data-busy");
      box.setAttribute("aria-busy", "true");
      load(box, link.getAttribute("data-part"), "");
    });
  }

  // "上へ戻る" on long pages: shown once the visitor is two screens down.
  function toTop() {
    var button = document.querySelector(".to-top");
    if (!button) return;
    function check() {
      var far = window.scrollY > window.innerHeight * 2;
      if (button.hidden === far) button.hidden = !far;
    }
    addEventListener("scroll", check, { passive: true });
    check();
    button.addEventListener("click", function (e) {
      e.preventDefault();
      var still = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
      window.scrollTo({ top: 0, behavior: still ? "auto" : "smooth" });
      var skip = document.querySelector(".brand");
      if (skip) skip.focus({ preventScroll: true });
    });
  }

  // Pictures fetched ahead (the agents' squares while the party is matchmaking): agent select then shows them at
  // once, from the browser's own store. Each is asked once a page.
  var warmed = {};
  function warm(addresses) {
    (addresses || []).forEach(function (address) {
      address = ownPicture(address);
      if (!address || warmed[address]) return;
      var picture = new Image();
      warmed[address] = picture; // held until it is loaded
      picture.decoding = "async";
      picture.src = address;
    });
  }

  // Puts into `old` what changed in `fresh` (the same part, read again), keeping every element that stays: a
  // picture already shown is not made again (the agents' squares blinked and were fetched again at each look), a
  // button keeps its focus. Children are matched by their tag, id, first class and label; the rest are added or
  // taken away.
  function morph(old, fresh) {
    function key(node) {
      return node.nodeType === 1
        ? node.nodeName +
            "#" +
            node.id +
            "." +
            (node.classList[0] || "") +
            "@" +
            (node.getAttribute("aria-labelledby") || "")
        : "#" + node.nodeType;
    }
    var a = old.firstChild;
    var b = fresh.firstChild;
    while (b) {
      var next = b.nextSibling;
      var wanted = key(b);
      var match = a;
      while (match && key(match) !== wanted) match = match.nextSibling;
      if (!match) {
        old.insertBefore(b, a); // new here (at the end when a is null)
      } else {
        if (match !== a) old.insertBefore(match, a);
        if (match.nodeType === 1) {
          Array.prototype.slice.call(match.attributes).forEach(function (attribute) {
            if (!b.hasAttribute(attribute.name)) match.removeAttribute(attribute.name);
          });
          Array.prototype.slice.call(b.attributes).forEach(function (attribute) {
            if (match.getAttribute(attribute.name) !== attribute.value)
              match.setAttribute(attribute.name, attribute.value);
          });
          morph(match, b);
        } else if (match.nodeValue !== b.nodeValue) {
          match.nodeValue = b.nodeValue;
        }
        a = match.nextSibling;
      }
      b = next;
    }
    while (a) {
      var gone = a;
      a = a.nextSibling;
      old.removeChild(gone);
    }
  }

  // The PC app (desktop.py): the Riot Client on the PC tells the app the moment the game changes (the match found,
  // agent select, a pick), and the app tells the page (/live/next answers then, or after 25 s with nothing new):
  // the page reads itself again at once instead of at its next look. Nothing is asked while the window is hidden or
  // minimised. On the site there is no address and nothing happens.
  function pushed(address, version, changed) {
    if (!address || !window.fetch) return;
    var waiting = false;
    function wait() {
      if (waiting || document.visibilityState !== "visible") return;
      waiting = true;
      fetch(address + "?after=" + encodeURIComponent(version), {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
        cache: "no-store",
      })
        .then(function (answer) {
          return answer.ok ? answer.json() : null;
        })
        .then(function (found) {
          waiting = false;
          if (!found) return setTimeout(wait, 10000);
          if (String(found.version) !== String(version)) {
            version = found.version;
            changed();
          }
          // a burst of events (picks at agent select) reads the page a few times a second at most
          setTimeout(wait, 250);
        })
        .catch(function () {
          waiting = false;
          setTimeout(wait, 10000);
        });
    }
    document.addEventListener("visibilitychange", wait);
    wait();
  }

  // /live is read again in the background (as often as the moment needs: each second once a match is found), and
  // only what changed is put in place: no reload, so the scroll, a mode chosen in the menu, the focused button and
  // the pictures stay. Its buttons (queue, pick, lock) are sent the same way and the answer (the page after the
  // press, with its note) is put in place at once. Never while a button is being pressed or the mode menu is open.
  // Without a script, the buttons send the page as before.
  function refresh() {
    var box = document.querySelector("[data-refresh]");
    if (!box || !window.fetch || !window.DOMParser) return;
    var timer = null;
    var asking = false;
    var again = false; // the game changed while the page was being read: read it once more
    var sending = false;
    var pressed = 0; // when a button was pressed down (a press that never ends stops nothing after 3 s)
    var last = box.innerHTML;

    function pressing() {
      return pressed > 0 && Date.now() - pressed < 3000;
    }

    function later() {
      clearTimeout(timer);
      timer = setTimeout(ask, Math.max(1, Number(box.getAttribute("data-refresh")) || 10) * 1000);
    }

    // the button that had the focus, or one the same (its name and value) in the new part
    function twin(old) {
      if (box.contains(old)) return old;
      var found = null;
      box.querySelectorAll("button[name]").forEach(function (button) {
        if (!found && old.name && button.name === old.name && button.value === old.value) found = button;
      });
      return found;
    }

    function put(text) {
      var fresh = new DOMParser().parseFromString(text, "text/html").querySelector("[data-refresh]");
      if (!fresh) return false;
      ["data-refresh", "data-live-phase", "data-live-words", "data-warm"].forEach(function (name) {
        box.setAttribute(name, fresh.getAttribute(name) || "");
      });
      warm((fresh.getAttribute("data-warm") || "").split(" "));
      if (fresh.innerHTML === last) return true;
      last = fresh.innerHTML;
      // a mode chosen in the menu but not started yet is kept
      var chosen = {};
      box.querySelectorAll("select[name]").forEach(function (select) {
        var start = select.querySelector("option[selected]");
        if (start && select.value !== start.value) chosen[select.name] = select.value;
      });
      var focused = document.activeElement;
      var left = window.scrollX;
      var top = window.scrollY;
      morph(box, fresh);
      window.scrollTo(left, top);
      box.querySelectorAll("select[name]").forEach(function (select) {
        if (chosen[select.name] && select.querySelector('option[value="' + chosen[select.name] + '"]'))
          select.value = chosen[select.name];
      });
      var again = focused && focused.tagName === "BUTTON" && document.activeElement !== focused ? twin(focused) : null;
      if (again) again.focus({ preventScroll: true });
      document.dispatchEvent(new CustomEvent("val:refreshed"));
      return true;
    }

    function ask() {
      clearTimeout(timer);
      if (asking || document.visibilityState !== "visible") return;
      if (sending || pressing() || box.querySelector("select:focus")) return later();
      asking = true;
      fetch(location.href, { credentials: "same-origin", headers: { Accept: "text/html" }, cache: "no-store" })
        .then(function (answer) {
          return answer.text();
        })
        .then(function (text) {
          if (!sending && !pressing()) put(text);
        })
        .catch(function () {})
        .then(function () {
          asking = false;
          if (again) {
            again = false;
            ask();
          } else later();
        });
    }

    box.addEventListener("pointerdown", function () {
      pressed = Date.now();
    });
    ["pointerup", "pointercancel"].forEach(function (name) {
      document.addEventListener(name, function () {
        pressed = 0;
      });
    });

    document.addEventListener("submit", function (event) {
      var form = event.target;
      if (!form.matches || !form.matches(".live-act") || !box.contains(form)) return;
      event.preventDefault();
      if (sending) return;
      sending = true;
      var data = new FormData(form);
      var button = event.submitter;
      if (button && button.name) data.append(button.name, button.value);
      form.querySelectorAll("button").forEach(function (each) {
        each.disabled = true;
      });
      form.setAttribute("aria-busy", "true");
      fetch(form.action, { method: "POST", body: data, credentials: "same-origin" })
        .then(function (answer) {
          return answer.text().then(function (text) {
            if (!put(text)) throw new Error("no page"); // a refusal or Riot busy is a /live page too
          });
        })
        .catch(function () {
          location.reload();
        })
        .then(function () {
          sending = false;
          later();
        });
    });

    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "visible") ask();
    });
    warm((box.getAttribute("data-warm") || "").split(" "));
    later();
    pushed(box.getAttribute("data-push"), box.getAttribute("data-push-version"), function () {
      if (asking) again = true;
      else ask();
    });
  }

  // The PC app updates itself (update.py): the button is sent in the background and the notice says how far the
  // update is, asked every second while it goes, until the app closes to open again as the new build. Without a
  // script the button sends the form and the settings page says it.
  function updating() {
    var box = document.querySelector("[data-update]");
    if (!box || !window.fetch) return;
    var form = box.querySelector("form");
    var button = form ? form.querySelector("button") : null;
    var said = box.querySelector("[data-update-said]");
    var timer = null;
    function show(found) {
      if (!found) return;
      if (said) {
        said.textContent = found.words || "";
        said.hidden = !found.words;
        said.classList.toggle("failed", !!found.failed);
      }
      if (button) {
        button.disabled = !!found.busy;
        if (found.button) button.textContent = found.button;
      }
      clearTimeout(timer);
      if (found.busy) timer = setTimeout(ask, 1000);
    }
    function read(answer) {
      return answer.ok ? answer.json() : null;
    }
    function ask() {
      fetch(box.getAttribute("data-update"), {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
        cache: "no-store",
      })
        .then(read)
        .then(show)
        .catch(function () {
          // the app is closing to open again as the new build: nothing more to ask
        });
    }
    if (form)
      form.addEventListener("submit", function (event) {
        event.preventDefault();
        if (button) button.disabled = true;
        fetch(form.action, {
          method: "POST",
          body: new FormData(form),
          headers: { Accept: "application/json" },
          credentials: "same-origin",
        })
          .then(read)
          .then(function (found) {
            if (found) show(found);
            else location.reload(); // a refusal (the form's token is old): the page as it is now
          })
          .catch(function () {
            if (button) button.disabled = false;
          });
      });
    if (box.hasAttribute("data-busy")) ask();
  }

  // The owner's collection lists every item of a tab at once: the names are narrowed as they are typed.
  function finder() {
    var input = document.getElementById("find");
    if (!input) return;
    input.parentNode.hidden = false;
    function narrow() {
      var words = input.value.trim().toLowerCase();
      document.querySelectorAll("[data-name]").forEach(function (item) {
        item.hidden = words !== "" && item.getAttribute("data-name").indexOf(words) < 0;
      });
      document.querySelectorAll("[data-group]").forEach(function (group) {
        // a group with nothing to narrow (an empty one says so itself) stays
        group.hidden = words !== "" && !group.querySelector("[data-name]:not([hidden])");
      });
    }
    input.addEventListener("input", narrow);
    // a list read again in the background (the PC app's friends) stays narrowed
    document.addEventListener("val:refreshed", function () {
      if (input.value.trim()) narrow();
    });
  }

  // Wearing something from the collection leaves the page where it is: the form goes in the background and the
  // list is replaced with the answer's (the same page, now with the new look marked). Without a script the page
  // reloads and comes back to the same weapon.
  function wearInPlace() {
    if (!window.fetch || !window.DOMParser || !document.querySelector(".equip-form")) return;
    var busy = false;
    // the note after wearing: long enough to read (a refusal longer; a tap closes it), not again on a reload
    function fade(said) {
      if (!said) return;
      setTimeout(
        function () {
          said.remove();
        },
        said.classList.contains("ok") ? Math.max(4000, said.textContent.length * 100) : 15000,
      );
      said.addEventListener("click", function () {
        said.remove();
      });
      var address = new URL(location.href);
      address.searchParams.delete("equip");
      address.searchParams.delete("riot");
      history.replaceState(null, "", address.pathname + address.search + address.hash);
    }
    fade(document.querySelector(".equip-said"));
    document.addEventListener("submit", function (event) {
      var form = event.target;
      var button = event.submitter;
      if (!form.matches || !form.matches(".equip-form") || !button || !button.name) return;
      event.preventDefault();
      if (busy) return;
      busy = true;
      var data = new FormData(form);
      data.append(button.name, button.value);
      button.disabled = true;
      fetch(form.action, { method: "POST", body: data, credentials: "same-origin" })
        .then(function (answer) {
          return answer.text().then(function (text) {
            var now = document.getElementById("owned");
            var fresh = new DOMParser().parseFromString(text, "text/html").getElementById("owned");
            if (!answer.ok || !now || !fresh) throw new Error("no list");
            var left = window.scrollX;
            var top = window.scrollY;
            now.replaceWith(fresh);
            window.scrollTo(left, top);
            var address = new URL(answer.url);
            history.replaceState(null, "", address.pathname + address.search + location.hash);
            var find = document.getElementById("find");
            if (find && find.value) find.dispatchEvent(new Event("input"));
            fade(fresh.querySelector(".equip-said"));
          });
        })
        .catch(function () {
          location.reload();
        })
        .then(function () {
          busy = false;
        });
    });
  }

  // The owner's game on every page: the header's ライブ tab shows where it is (a dot: the menus, matchmaking, agent
  // select, the match), and agent select starting brings a note with the way to pick. /live itself is read again
  // every few seconds (refresh), so there the page says where the game is and nothing is asked.
  function liveWatch() {
    var tab = document.querySelector("[data-live-state]");
    if (!tab) return;
    // the words in the page's language (base.html): agent select began, pick on ライブ, close, ライブ（{state}）
    var said = (tab.getAttribute("data-live-said") || "").split("|");
    var SAID = {
      began: said[0] || "エージェント選択が始まりました",
      pick: said[1] || "ライブでピックする",
      close: said[2] || "閉じる",
      label: said[3] || "ライブ（{state}）",
      found: said[4] || "試合が見つかりました",
    };
    var FOUND = 60000; // closing the note when the match is found closes it for that match's agent select too
    var STOP = { nologin: true, signedout: true, badpaste: true };
    var TOLD = "val.live.told";
    var SWITCHED = "val.live.switched";
    var SWITCH_KEEP = 150000; // agent select (85 s) after the match is found: not switched to again in that time
    var page = document.querySelector("[data-live-phase]");
    var timer = null;
    var asking = false;
    var again = false;

    function told(match) {
      try {
        if (match) sessionStorage.setItem(TOLD, match);
        return sessionStorage.getItem(TOLD) || "";
      } catch (e) {
        return "";
      }
    }

    // The PC app (unless turned off in 設定): the window goes to ライブ by itself when the match is found, once a
    // match (the owner may go back to another page during agent select). The window is not brought to the front.
    function switchToLive(state) {
      if (!document.body.classList.contains("app") || page) return false;
      if (state.phase !== "found" && state.phase !== "pregame") return false;
      if (pref("val.live.switch") === "off") return false;
      var key = state.match || "found";
      var was;
      try {
        was = JSON.parse(sessionStorage.getItem(SWITCHED) || "{}") || {};
      } catch (e) {
        was = {};
      }
      var recent = was.key === "found" && Date.now() - Number(was.at || 0) < SWITCH_KEEP;
      var keep = function (value) {
        try {
          sessionStorage.setItem(SWITCHED, JSON.stringify({ key: value, at: Date.now() }));
        } catch (e) {
          /* not kept: switched once more at most */
        }
      };
      if (was.key === key || recent) {
        if (recent && state.match) keep(state.match);
        return false;
      }
      keep(key);
      location.assign(tab.getAttribute("href"));
      return true;
    }

    // the note from the moment the match is found (the game's 5-second count) through agent select
    function note(state) {
      if (switchToLive(state)) return;
      var box = document.getElementById("live-alert");
      var was = told();
      var closed = was.indexOf("found@") === 0 && Date.now() - Number(was.slice(6)) < FOUND;
      if (closed && state.match) told(state.match); // closed when the match was found: this agent select's
      if (
        (state.phase !== "found" && state.phase !== "pregame") ||
        page ||
        closed ||
        (state.match && was === state.match)
      ) {
        if (box) box.remove();
        return;
      }
      var wide = /^(ja|zh)/.test(document.documentElement.lang); // full-width brackets in Japanese and Chinese
      var words = (state.phase === "found" ? SAID.found : SAID.began) + "|";
      if (state.map) words += wide ? "（" + state.map + "）" : " (" + state.map + ")";
      if (box) {
        if (box.getAttribute("data-words") !== words) {
          box.setAttribute("data-words", words);
          box.querySelector("strong").textContent = words.split("|")[0];
          box.querySelector("span").textContent = words.split("|")[1];
        }
        return;
      }
      box = document.createElement("div");
      box.id = "live-alert";
      box.className = "live-alert";
      box.setAttribute("role", "status");
      box.setAttribute("data-words", words);
      var line = document.createElement("p");
      var strong = document.createElement("strong");
      strong.textContent = words.split("|")[0];
      line.appendChild(strong);
      var where = document.createElement("span");
      where.textContent = words.split("|")[1];
      line.appendChild(where);
      var go = document.createElement("a");
      go.className = "button";
      go.href = tab.getAttribute("href");
      go.textContent = SAID.pick;
      var close = document.createElement("button");
      close.type = "button";
      close.className = "quiet";
      close.textContent = SAID.close;
      close.addEventListener("click", function () {
        var now = current;
        told(now.match || (now.phase === "found" ? "found@" + Date.now() : "seen"));
        box.remove();
      });
      box.appendChild(line);
      box.appendChild(go);
      box.appendChild(close);
      document.body.appendChild(box);
    }

    var current = {};

    function show(state) {
      current = state;
      tab.setAttribute("data-phase", state.phase || "off");
      tab.setAttribute("aria-label", state.words ? SAID.label.replace("{state}", state.words) : tab.textContent.trim());
      tab.title = state.words || "";
      // the top page's 自分 box says it too
      document.querySelectorAll("[data-live-text]").forEach(function (words) {
        if (state.words) words.textContent = state.words;
      });
      note(state);
    }

    function ask() {
      clearTimeout(timer);
      if (asking || document.visibilityState !== "visible") return;
      asking = true;
      fetch(tab.getAttribute("data-live-state"), {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      })
        .then(function (answer) {
          return answer.ok ? answer.json() : null;
        })
        .then(function (state) {
          asking = false;
          if (again) {
            again = false;
            return ask();
          }
          if (state) show(state);
          if (state && STOP[state.problem]) return;
          if (state) warm(state.icons);
          // as often as the server says the moment needs (each second once a match is found); else while the game
          // is open, often, and closed, now and then
          var every = state && Number(state.every) > 0 ? Number(state.every) * 1000 : 0;
          timer = setTimeout(ask, every || (state && state.phase ? 8000 : 30000));
        })
        .catch(function () {
          asking = false;
          timer = setTimeout(ask, 30000);
        });
    }

    if (page) {
      var here = function () {
        show({ phase: page.getAttribute("data-live-phase"), words: page.getAttribute("data-live-words") });
      };
      here();
      document.addEventListener("val:refreshed", here); // the page's new part (refresh)
      return;
    }
    ask();
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "visible") ask();
    });
    pushed(tab.getAttribute("data-live-push"), tab.getAttribute("data-push-version"), function () {
      if (asking) again = true;
      else ask();
    });
  }

  // A choice kept in this browser (the PC app's 設定): its value, or "" when never made.
  function pref(key) {
    try {
      return localStorage.getItem(key) || "";
    } catch (e) {
      return "";
    }
  }
  // 設定's boxes with data-pref: ticked unless turned "off", kept when changed.
  function prefs() {
    document.querySelectorAll("input[data-pref]").forEach(function (box) {
      var key = box.getAttribute("data-pref");
      box.checked = pref(key) !== "off";
      box.addEventListener("change", function () {
        try {
          if (box.checked) localStorage.removeItem(key);
          else localStorage.setItem(key, "off");
        } catch (e) {
          /* not kept */
        }
      });
    });
  }

  // The PC app (body.app, desktop.py): its window has no browser bar and does not take the browser's keys, and a
  // page of another site opened in it would have no way back. Back, forward and reload as buttons and keys
  // (Alt+←, Alt+→, F5, Ctrl+R), and other sites' links open in the default browser (a new window's address goes
  // there).
  function desktopApp() {
    if (!document.body.classList.contains("app")) return;
    document.querySelectorAll("[data-go]").forEach(function (button) {
      var go = button.getAttribute("data-go");
      if (go === "back" && history.length < 2) button.disabled = true;
      button.addEventListener("click", function () {
        if (go === "back") history.back();
        else if (go === "forward") history.forward();
        else location.reload();
      });
    });
    document.addEventListener("keydown", function (e) {
      if (e.altKey && e.key === "ArrowLeft") history.back();
      else if (e.altKey && e.key === "ArrowRight") history.forward();
      else if (e.key === "F5" || ((e.ctrlKey || e.metaKey) && !e.altKey && (e.key === "r" || e.key === "R"))) {
        location.reload();
      } else return;
      e.preventDefault();
    });
    document.addEventListener(
      "click",
      function (e) {
        var link = e.target.closest ? e.target.closest("a[href]") : null;
        if (!link || link.hasAttribute("download")) return;
        var address;
        try {
          address = new URL(link.href, location.href);
        } catch (err) {
          return;
        }
        if (address.origin === location.origin || !/^https?:$/.test(address.protocol)) return;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
      },
      true,
    );
  }

  // Phones: the tab bar at the bottom would sit on the keyboard while typing.
  function typing() {
    document.addEventListener("focusin", function (e) {
      if (e.target.matches("input, textarea")) document.body.classList.add("typing");
    });
    document.addEventListener("focusout", function () {
      document.body.classList.remove("typing");
    });
    // "/" goes to the page's search box (as on GitHub and YouTube), unless one is being typed in already.
    document.addEventListener("keydown", function (e) {
      if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey || e.target.closest("input, textarea, select")) return;
      var box = document.querySelector("form.search input:not([type=hidden])");
      if (!box) return;
      e.preventDefault();
      box.focus();
      box.select();
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    desktopApp();
    prefs();
    part();
    chosen();
    acts();
    autoSubmit();
    profile();
    lists();
    times();
    days();
    share();
    rounds();
    finder();
    wearInPlace();
    typing();
    parts();
    copies();
    countdowns();
    zone();
    teams();
    night();
    refresh();
    updating();
    readAgain();
    toTop();
    liveWatch();
    mapPick();
    statCard();
    crosshairs();
  });
  addEventListener("storage", function (e) {
    if (e.key === FAVORITES || e.key === RECENT || e.key === null) lists();
  });
  // Coming back with the browser's Back button shows the page as it was: the lists may have changed since.
  addEventListener("pageshow", function (e) {
    if (e.persisted) lists();
  });
})();
