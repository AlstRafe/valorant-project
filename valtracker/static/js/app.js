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
  function fill(list) {
    var key = list.id === "favorites" ? FAVORITES : RECENT;
    var people = read(key);
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
          profile();
        }
        chosen();
        acts();
        times();
        days();
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
    if (!list || list.querySelector(".day") || !window.Intl) return;
    var format;
    try {
      format = new Intl.DateTimeFormat(document.documentElement.lang, {
        month: "short",
        day: "numeric",
        weekday: "short",
      });
    } catch (e) {
      return;
    }
    var groups = [];
    list.querySelectorAll("li.match").forEach(function (item) {
      var time = item.querySelector("time[datetime]");
      var moment = time ? new Date(time.getAttribute("datetime")) : null;
      if (!moment || isNaN(moment)) return;
      var key = moment.getFullYear() + "-" + moment.getMonth() + "-" + moment.getDate();
      var last = groups[groups.length - 1];
      if (!last || last.key !== key) {
        last = { key: key, label: format.format(moment), items: [] };
        groups.push(last);
      }
      last.items.push(item);
    });
    groups.forEach(function (group) {
      var wins = 0;
      var losses = 0;
      var rr = 0;
      var rated = false;
      group.items.forEach(function (item) {
        var outcome = item.getAttribute("data-outcome");
        if (outcome === "win") wins += 1;
        if (outcome === "loss") losses += 1;
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
    try {
      format = new Intl.DateTimeFormat(document.documentElement.lang, {
        month: "short",
        day: "numeric",
        weekday: "short",
      });
    } catch (e) {
      return;
    }
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
      title.textContent = format.format(moment);
      head.appendChild(title);
      list.insertBefore(head, item);
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
      button.textContent = "めくる";
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
  function refresh() {
    var box = document.querySelector("[data-refresh]");
    if (!box) return;
    var seconds = Math.max(3, Number(box.getAttribute("data-refresh")) || 10);
    setInterval(function () {
      if (document.visibilityState === "visible" && !document.querySelector("select:focus")) location.reload();
    }, seconds * 1000);
  }

  // The owner's collection lists every item of a tab at once: the names are narrowed as they are typed.
  function finder() {
    var input = document.getElementById("find");
    if (!input) return;
    input.parentNode.hidden = false;
    input.addEventListener("input", function () {
      var words = input.value.trim().toLowerCase();
      document.querySelectorAll("[data-name]").forEach(function (item) {
        item.hidden = words !== "" && item.getAttribute("data-name").indexOf(words) < 0;
      });
      document.querySelectorAll("[data-group]").forEach(function (group) {
        group.hidden = !group.querySelector("[data-name]:not([hidden])");
      });
    });
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
    part();
    chosen();
    acts();
    profile();
    lists();
    times();
    days();
    share();
    rounds();
    finder();
    typing();
    parts();
    copies();
    countdowns();
    zone();
    teams();
    night();
    refresh();
  });
  addEventListener("storage", function (e) {
    if (e.key === FAVORITES || e.key === RECENT || e.key === null) lists();
  });
  // Coming back with the browser's Back button shows the page as it was: the lists may have changed since.
  addEventListener("pageshow", function (e) {
    if (e.persisted) lists();
  });
})();
