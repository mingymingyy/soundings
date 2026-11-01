// Dashboard behaviour: reveal panels, refresh in place, sparkline hover, linked research.
(function () {
  var reduced = matchMedia('(prefers-reduced-motion: reduce)');
  var POLL_MS = 5 * 60 * 1000;
  var LATE_MS = 90 * 60 * 1000;
  var market = null;

  function sgtToday() {
    return new Date(Date.now() + 8 * 3600 * 1000).toISOString().slice(0, 10);
  }

  function refreshCountdowns(root) {
    var today = Date.parse(sgtToday());
    (root || document).querySelectorAll('[data-until]').forEach(function (el) {
      var days = Math.round((Date.parse(el.dataset.until) - today) / 86400000);
      el.textContent = days === 0 ? 'today' : days === 1 ? 'tomorrow' : days > 1 ? 'in ' + days + ' days' : 'passed';
    });
    (root || document).querySelectorAll('.ev[data-date]').forEach(function (el) {
      if (Date.parse(el.dataset.date) < today) el.remove();
    });
  }

  function markLateness() {
    var stamp = document.getElementById('stamp');
    if (stamp) stamp.classList.toggle('is-late', Date.now() - Date.parse(document.body.dataset.generated) > LATE_MS);
  }

  function format(value, el) {
    var dp = Number(el.dataset.dp);
    return value.toLocaleString('en-US', { minimumFractionDigits: dp, maximumFractionDigits: dp }) + (el.dataset.suffix || '');
  }

  function tween(el, from, to) {
    if (reduced.matches || document.hidden) { el.textContent = format(to, el); return; }
    var start = performance.now();
    var duration = 700;
    (function frame(now) {
      var t = Math.min(1, (now - start) / duration);
      var eased = 1 - Math.pow(1 - t, 3);
      el.textContent = format(from + (to - from) * eased, el);
      if (t < 1) requestAnimationFrame(frame);
    })(start);
  }

  function numbers(root) {
    var map = {};
    root.querySelectorAll('tr[id] [data-num], [data-tile] [data-num]').forEach(function (el) {
      var host = el.closest('tr[id]');
      var key = host ? host.id : 'tile-' + el.closest('[data-tile]').dataset.tile;
      map[key] = { el: el, value: Number(el.dataset.num) };
    });
    return map;
  }

  async function poll() {
    if (document.hidden) return;
    try {
      var bucket = Math.floor(Date.now() / 60000);
      var fresh = await (await fetch('data/market.json?t=' + bucket, { cache: 'no-store' })).json();
      market = fresh;
      if (fresh.generated === document.body.dataset.generated) return;
      var page = await (await fetch('./?t=' + bucket, { cache: 'no-store' })).text();
      swap(new DOMParser().parseFromString(page, 'text/html'), fresh.generated);
    } catch (err) {
      /* a missed poll just leaves the last rendered numbers and their timestamps on screen */
    }
  }

  function swap(next, generated) {
    var before = numbers(document);
    ['.tape', '.curve'].forEach(function (sel) {
      var incoming = next.querySelector(sel);
      var current = document.querySelector(sel);
      if (incoming && current) current.replaceWith(document.adoptNode(incoming));
    });
    document.querySelectorAll('.panel').forEach(function (panel) {
      var incoming = next.getElementById(panel.id);
      if (!incoming) return;
      panel.querySelector('.prov').replaceWith(document.adoptNode(incoming.querySelector('.prov')));
      panel.querySelector('[data-swap]').replaceWith(document.adoptNode(incoming.querySelector('[data-swap]')));
    });
    var updated = document.getElementById('updated');
    var nextUpdated = next.getElementById('updated');
    if (updated && nextUpdated) {
      updated.textContent = nextUpdated.textContent;
      updated.dateTime = nextUpdated.dateTime;
    }
    document.body.dataset.generated = generated;
    var after = numbers(document);
    Object.keys(after).forEach(function (key) {
      if (!before[key] || before[key].value === after[key].value) return;
      tween(after[key].el, before[key].value, after[key].value);
      var row = after[key].el.closest('tr');
      if (row && !reduced.matches) {
        row.classList.add('flash');
        setTimeout(function () { row.classList.remove('flash'); }, 1100);
      }
    });
    refreshCountdowns();
    markLateness();
  }

  // Tooltips: tenor rects carry data-tip, sparklines read the series from the JSON snapshot.
  var tip = document.getElementById('tip');

  function placeTip(x, y, text) {
    tip.textContent = text;
    tip.hidden = false;
    var half = tip.offsetWidth / 2 + 6;
    tip.style.left = Math.max(half, Math.min(innerWidth - half, x)) + 'px';
    tip.style.top = Math.max(36, y) + 'px';
  }

  function hideTip() {
    tip.hidden = true;
    var line = document.querySelector('.spark .x');
    if (line) line.remove();
  }

  function rowById(id) {
    if (!market) return null;
    var panels = market.panels;
    for (var key in panels) {
      var match = panels[key].rows.find(function (r) { return r.id === id; });
      if (match) return match;
    }
    return null;
  }

  document.addEventListener('pointermove', function (event) {
    var hit = event.target.closest && event.target.closest('[data-tip]');
    if (hit) { placeTip(event.clientX, event.clientY, hit.dataset.tip); return; }
    var svg = event.target.closest && event.target.closest('.spark');
    var row = svg && rowById(svg.dataset.id);
    if (!row || !row.series) { hideTip(); return; }
    var box = svg.getBoundingClientRect();
    var ratio = Math.max(0, Math.min(1, (event.clientX - box.left) / box.width));
    var index = Math.round(ratio * (row.series.length - 1));
    var point = row.series[index];
    var line = svg.querySelector('.x');
    if (!line) {
      line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
      line.setAttribute('class', 'x');
      svg.appendChild(line);
    }
    var viewBox = svg.viewBox.baseVal;
    var x = 4 + (index * (viewBox.width - 8)) / Math.max(1, row.series.length - 1);
    line.setAttribute('x1', x); line.setAttribute('x2', x);
    line.setAttribute('y1', 0); line.setAttribute('y2', viewBox.height);
    var dp = row.dp;
    var text = point[1].toLocaleString('en-US', { minimumFractionDigits: dp, maximumFractionDigits: dp }) + (row.suffix || '');
    placeTip(box.left + (x / viewBox.width) * box.width, box.top, point[0] + '  ' + text);
  }, { passive: true });
  document.addEventListener('pointerleave', hideTip);
  addEventListener('scroll', function () { if (!tip.hidden) hideTip(); }, { passive: true });

  // Research notes linked to each panel: repo entries plus this browser's local edits.
  function readLocalNotes() {
    return new Promise(function (resolve) {
      if (!window.indexedDB) return resolve([]);
      var request;
      try { request = indexedDB.open('soundings-research', 1); } catch (err) { return resolve([]); }
      request.onupgradeneeded = function () {
        var db = request.result;
        if (!db.objectStoreNames.contains('notes')) db.createObjectStore('notes', { keyPath: 'id' });
        if (!db.objectStoreNames.contains('files')) db.createObjectStore('files');
      };
      request.onerror = function () { resolve([]); };
      request.onsuccess = function () {
        var db = request.result;
        var all = db.transaction('notes').objectStore('notes').getAll();
        all.onsuccess = function () { resolve(all.result || []); db.close(); };
        all.onerror = function () { resolve([]); db.close(); };
      };
    });
  }

  async function renderLinked() {
    var repo = [];
    try { repo = await (await fetch('data/research/entries.json', { cache: 'no-cache' })).json(); } catch (err) { /* empty */ }
    var local = await readLocalNotes();
    var merged = new Map(repo.map(function (note) { return [note.id, note]; }));
    local.forEach(function (note) {
      if (note.deleted) merged.delete(note.id); else merged.set(note.id, note);
    });
    var notes = Array.from(merged.values()).sort(function (a, b) { return String(b.date).localeCompare(String(a.date)); });
    document.querySelectorAll('.linked').forEach(function (slot) {
      var related = notes.filter(function (note) { return (note.panels || []).indexOf(slot.dataset.panel) >= 0; }).slice(0, 4);
      slot.textContent = '';
      if (related.length) {
        var heading = document.createElement('h3');
        heading.textContent = 'Linked research';
        var list = document.createElement('ul');
        related.forEach(function (note) {
          var item = document.createElement('li');
          var link = document.createElement('a');
          link.href = 'research/#' + encodeURIComponent(note.id);
          link.textContent = note.title;
          item.appendChild(link);
          list.appendChild(item);
        });
        slot.append(heading, list);
      }
      var add = document.createElement('a');
      add.className = 'add';
      add.href = 'research/?new=' + slot.dataset.panel;
      add.textContent = related.length ? 'Add a note' : 'Add a research note';
      slot.appendChild(add);
    });
  }

  document.querySelectorAll('.reveal').forEach(function (el) {
    if (reduced.matches || !('IntersectionObserver' in window)) { el.classList.add('in'); return; }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) { entry.target.classList.add('in'); io.unobserve(entry.target); }
      });
    }, { rootMargin: '0px 0px -6% 0px' });
    io.observe(el);
  });

  refreshCountdowns();
  markLateness();
  renderLinked();
  // The first paint already has the numbers, so the freshness check can wait until the page is idle.
  setTimeout(poll, 2500);
  setInterval(function () { poll(); markLateness(); }, POLL_MS);
  document.addEventListener('visibilitychange', function () { if (!document.hidden) { poll(); refreshCountdowns(); } });
})();
