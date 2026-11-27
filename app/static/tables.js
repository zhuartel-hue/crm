// Kolumny tabel: poszerzanie (przeciagnij prawa krawedz naglowka) i zmiana kolejnosci (przeciagnij naglowek).
// Ustawienia zapisywane na serwerze dla zalogowanego uzytkownika i danej listy. Dwuklik na krawedzi = reset.
(function () {
  var cache = {};
  function keyOf(table) {
    var all = Array.prototype.slice.call(document.querySelectorAll('.card.tbl > table'));
    return location.pathname + '#' + all.indexOf(table);
  }
  function headRow(table) { return table.rows[0] && table.rows[0].querySelector('th') ? table.rows[0] : null; }
  function plainRows(table, n) {
    return Array.prototype.filter.call(table.rows, function (r) {
      if (r.cells.length !== n) return false;
      for (var i = 0; i < r.cells.length; i++) if (r.cells[i].colSpan > 1) return false;
      return true;
    });
  }
  function tag(table) {
    var h = headRow(table); if (!h || h.dataset.tagged) return h;
    var n = h.cells.length;
    plainRows(table, n).forEach(function (r) { for (var i = 0; i < n; i++) r.cells[i].dataset.ci = i; });
    h.dataset.tagged = '1';
    return h;
  }
  function applyOrder(table, order) {
    var h = headRow(table), n = h.cells.length;
    if (!order || order.length !== n) return;
    plainRows(table, n).forEach(function (r) {
      var byCi = {}; Array.prototype.forEach.call(r.cells, function (c) { byCi[c.dataset.ci] = c; });
      order.forEach(function (ci) { if (byCi[ci]) r.appendChild(byCi[ci]); });
    });
  }
  function applyWidths(table, widths, force) {
    widths = widths || {};
    if (!force && !Object.keys(widths).length) return;
    var h = headRow(table), total = 0;
    Array.prototype.forEach.call(h.cells, function (c) {
      var w = widths[c.dataset.ci] || Math.round(c.getBoundingClientRect().width);
      c.style.width = w + 'px'; total += w;
    });
    table.style.tableLayout = 'fixed'; table.style.width = total + 'px';
    table.classList.add('cols-fixed');
  }
  function state(table) {
    var h = headRow(table), widths = {};
    Array.prototype.forEach.call(h.cells, function (c) { if (c.style.width) widths[c.dataset.ci] = parseInt(c.style.width, 10); });
    return { order: Array.prototype.map.call(h.cells, function (c) { return +c.dataset.ci; }), widths: widths };
  }
  var timers = {};
  function save(table) {
    var k = keyOf(table), st = state(table); cache[k] = st;
    clearTimeout(timers[k]);
    timers[k] = setTimeout(function () {
      fetch('/ustawienia/kolumny', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ t: k, order: st.order, widths: st.widths }) });
    }, 400);
  }
  function reset(table) {
    var k = keyOf(table); delete cache[k];
    fetch('/ustawienia/kolumny', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ t: k, reset: true }) }).then(function () { location.reload(); });
  }
  function wire(table) {
    var h = tag(table); if (!h || h.dataset.wired) return; h.dataset.wired = '1';
    Array.prototype.forEach.call(h.cells, function (th) {
      th.classList.add('col-th');
      var g = document.createElement('span'); g.className = 'col-grip'; g.title = 'Przeciągnij, aby zmienić szerokość (dwuklik: układ domyślny)';
      th.appendChild(g);
    });
    var mode = null, th0 = null, x0 = 0, w0 = 0, moved = false;
    h.addEventListener('pointerdown', function (e) {
      if (e.button !== 0 || e.target.closest('a,input,button,select')) return;
      th0 = e.target.closest('th'); if (!th0) return;
      x0 = e.clientX; moved = false;
      if (e.target.classList.contains('col-grip')) {
        mode = 'resize'; w0 = th0.getBoundingClientRect().width;
        if (!table.classList.contains('cols-fixed')) applyWidths(table, {}, true);  // zamroz obecne szerokosci
        e.preventDefault();
      } else mode = 'move';
      h.setPointerCapture(e.pointerId);
    });
    h.addEventListener('pointermove', function (e) {
      if (!mode) return;
      var dx = e.clientX - x0;
      if (mode === 'resize') {
        var nw = Math.max(40, Math.round(w0 + dx)), old = parseInt(th0.style.width, 10) || w0;
        th0.style.width = nw + 'px'; table.style.width = (parseInt(table.style.width, 10) + nw - old) + 'px'; moved = true;
      } else if (Math.abs(dx) > 8) {
        moved = true; th0.classList.add('col-moving');
        var el = document.elementFromPoint(e.clientX, e.clientY), over = el && el.closest('th');
        if (over && over !== th0 && over.parentNode === h) {
          var r = over.getBoundingClientRect(), after = e.clientX > r.left + r.width / 2;
          var order = Array.prototype.map.call(h.cells, function (c) { return +c.dataset.ci; });
          var from = order.indexOf(+th0.dataset.ci); order.splice(from, 1);
          var to = order.indexOf(+over.dataset.ci) + (after ? 1 : 0); order.splice(to, 0, +th0.dataset.ci);
          applyOrder(table, order); x0 = e.clientX;
        }
      }
    });
    function end() { if (mode && moved) save(table); if (th0) th0.classList.remove('col-moving'); mode = null; th0 = null; }
    h.addEventListener('pointerup', end); h.addEventListener('pointercancel', end);
    h.addEventListener('dblclick', function (e) { if (e.target.classList.contains('col-grip')) reset(table); });
  }
  function apply(root) {
    Array.prototype.forEach.call((root || document).querySelectorAll('.card.tbl > table'), function (table) {
      if (!headRow(table)) return;
      tag(table);
      var k = keyOf(table), done = function (st) {
        if (st) { applyOrder(table, st.order); applyWidths(table, st.widths); }
        wire(table);
      };
      if (k in cache) done(cache[k]);
      else fetch('/ustawienia/kolumny?t=' + encodeURIComponent(k), { credentials: 'same-origin' })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (st) { cache[k] = st && st.order ? st : null; done(cache[k]); })
        .catch(function () { done(null); });
    });
  }
  window.sygnavoTables = { apply: apply };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', function () { apply(); });
  else apply();
})();
