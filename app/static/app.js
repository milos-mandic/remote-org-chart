// Vanilla JS for three concerns only: (1) type-to-filter search, (2) the
// profile drawer, (3) node expand/collapse via the card caret.
// No frameworks, no dependencies. All person data is already in the DOM.
(function () {
  "use strict";

  var chart = document.getElementById("chart");
  if (!chart) return;

  // ---- helpers ----------------------------------------------------------
  function cardOf(node) {
    return node.querySelector(":scope > summary > .card") || node.querySelector(":scope > .card");
  }
  function parentNode(node) {
    return node.parentElement ? node.parentElement.closest(".node") : null;
  }
  function ancestorCards(node) {
    var names = [], p = parentNode(node);
    while (p) { var c = cardOf(p); if (c) names.unshift(c); p = parentNode(p); }
    return names;
  }
  function reportCards(node) {
    var out = [], ch = node.querySelector(":scope > .children");
    if (ch) Array.prototype.forEach.call(ch.children, function (k) {
      if (k.classList && k.classList.contains("node")) { var c = cardOf(k); if (c) out.push(c); }
    });
    return out;
  }

  // ---- search -----------------------------------------------------------
  var input = document.getElementById("search");
  var noResults = document.getElementById("no-results");
  var nodes = Array.prototype.slice.call(chart.querySelectorAll(".node"));

  function resetSearch() {
    nodes.forEach(function (n) {
      n.classList.remove("hidden");
      if (n.tagName === "DETAILS" && !n.classList.contains("leaf")) n.open = true;
    });
    if (noResults) noResults.classList.add("hidden");
  }
  function runSearch(term) {
    var keep = new Set();
    nodes.forEach(function (n) {
      if ((n.dataset.search || "").indexOf(term) !== -1) {
        var cur = n;
        while (cur) { keep.add(cur); cur = parentNode(cur); }
      }
    });
    nodes.forEach(function (n) {
      if (keep.has(n)) { n.classList.remove("hidden"); if (n.tagName === "DETAILS") n.open = true; }
      else n.classList.add("hidden");
    });
    if (noResults) noResults.classList.toggle("hidden", keep.size !== 0);
  }
  if (input) input.addEventListener("input", function () {
    var term = input.value.trim().toLowerCase();
    if (term === "") resetSearch(); else runSearch(term);
  });

  // ---- profile drawer ---------------------------------------------------
  var panel = document.getElementById("panel");
  var backdrop = document.getElementById("panel-backdrop");
  var el = {
    avatar: document.getElementById("p-avatar"),
    name: document.getElementById("p-name"),
    title: document.getElementById("p-title"),
    badges: document.getElementById("p-badges"),
    line: document.getElementById("p-line"),
    reports: document.getElementById("p-reports"),
    rcount: document.getElementById("p-rcount"),
  };

  function badge(text, cls) {
    var s = document.createElement("span");
    s.className = "badge " + (cls || "dept");
    s.textContent = text;
    return s;
  }

  function openPanel(card) {
    var node = card.closest(".node");
    var d = card.dataset;

    var av = card.querySelector(".avatar");
    el.avatar.textContent = av ? av.textContent : "";
    el.avatar.style.cssText = av ? av.style.cssText : "";
    el.name.textContent = d.name || "";
    el.title.textContent = d.title || "";

    el.badges.innerHTML = "";
    if (d.dept) el.badges.appendChild(badge(d.dept, "dept"));
    if (d.country) el.badges.appendChild(badge(d.country, "tag"));
    if (d.model) el.badges.appendChild(badge(d.model, "tag model"));
    if (d.bucket) el.badges.appendChild(badge(d.bucket, "tag"));
    if (d.flag) el.badges.appendChild(badge("⚠ inconsistent", "warn"));

    // reporting line (ancestor chain, highest manager first)
    var line = ancestorCards(node);
    if (line.length) {
      el.line.innerHTML = "";
      line.forEach(function (c, i) {
        var b = document.createElement("button");
        b.type = "button"; b.className = "link"; b.textContent = c.dataset.name;
        b.addEventListener("click", function () { focusCard(c); });
        el.line.appendChild(b);
        if (i < line.length - 1) {
          var sep = document.createElement("span"); sep.className = "sep"; sep.textContent = "›";
          el.line.appendChild(sep);
        }
      });
    } else {
      el.line.textContent = "Top of the org — no manager in this chart.";
    }

    // direct reports
    var reps = reportCards(node);
    el.rcount.textContent = "(" + reps.length + ")";
    el.reports.innerHTML = "";
    if (!reps.length) {
      var li = document.createElement("li"); li.className = "dim"; li.textContent = "None"; el.reports.appendChild(li);
    } else {
      reps.forEach(function (c) {
        var li = document.createElement("li");
        var b = document.createElement("button");
        b.type = "button"; b.textContent = c.dataset.name;
        b.addEventListener("click", function () { focusCard(c); });
        li.appendChild(b);
        if (c.dataset.title) {
          var t = document.createElement("div"); t.className = "r-title"; t.textContent = c.dataset.title; li.appendChild(t);
        }
        el.reports.appendChild(li);
      });
    }

    panel.classList.add("open");
    panel.setAttribute("aria-hidden", "false");
    backdrop.classList.remove("hidden");
  }

  function focusCard(card) {
    // expand ancestors so the person is visible, scroll to them, open their panel
    var p = card.closest(".node");
    while (p) { if (p.tagName === "DETAILS") p.open = true; p = parentNode(p); }
    card.scrollIntoView({ behavior: "smooth", block: "center" });
    openPanel(card);
  }

  function closePanel() {
    panel.classList.remove("open");
    panel.setAttribute("aria-hidden", "true");
    backdrop.classList.add("hidden");
  }

  if (backdrop) backdrop.addEventListener("click", closePanel);
  var closeBtn = document.getElementById("p-close");
  if (closeBtn) closeBtn.addEventListener("click", closePanel);
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") closePanel(); });

  // clicks in the chart: caret toggles; a card opens the profile panel
  chart.addEventListener("click", function (e) {
    var caret = e.target.closest(".caret");
    if (caret) {
      e.preventDefault();
      var d = caret.closest("details.node");
      if (d) d.open = !d.open;
      return;
    }
    var card = e.target.closest(".card");
    if (card) {
      var summary = e.target.closest("summary");
      if (summary && summary.parentElement.classList.contains("node")) e.preventDefault();
      openPanel(card);
    }
  });

  // ---- refresh ----------------------------------------------------------
  var refresh = document.getElementById("refresh");
  if (refresh) refresh.addEventListener("click", function () {
    refresh.disabled = true; refresh.textContent = "Refreshing…";
    fetch("/api/refresh", { method: "POST" }).then(function () { location.reload(); });
  });
})();
