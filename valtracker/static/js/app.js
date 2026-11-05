// VAL Tracker's pages: favourite and recently viewed players (kept in this browser), match times in the
// visitor's own time zone, and the phone's tab bar hidden while typing. The pages work without it, except
// for the lists of players, which live only in this browser.
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
    write(RECENT, [person].concat(without(read(RECENT), person)).slice(0, RECENT_MAX));
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
    var address = box && box.getAttribute("data-part");
    if (!address) return;
    function failed() {
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
        box.innerHTML = html; // our own server's page, escaped by its templates
        box.removeAttribute("data-part");
        profile();
        times();
      })
      .catch(failed);
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

  // Phones: the tab bar at the bottom would sit on the keyboard while typing.
  function typing() {
    document.addEventListener("focusin", function (e) {
      if (e.target.matches("input, textarea")) document.body.classList.add("typing");
    });
    document.addEventListener("focusout", function () {
      document.body.classList.remove("typing");
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    part();
    profile();
    lists();
    times();
    typing();
  });
  addEventListener("storage", function (e) {
    if (e.key === FAVORITES || e.key === RECENT || e.key === null) lists();
  });
  // Coming back with the browser's Back button shows the page as it was: the lists may have changed since.
  addEventListener("pageshow", function (e) {
    if (e.persisted) lists();
  });
})();
