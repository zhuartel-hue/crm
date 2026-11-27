// Edytor strony Start: wlacz "Dostosuj", przeciagaj kafelki, zmieniaj szerokosc/wysokosc, chowaj i dodawaj, zapisz.
(function () {
  var dash = document.getElementById('dash'); if (!dash) return;
  var bar = document.getElementById('dash-bar'), btn = document.getElementById('dash-edit');
  var add = document.getElementById('dash-add'), tpl = document.getElementById('dash-hidden');
  var COLS = +dash.dataset.cols, MAXH = +dash.dataset.maxh, before = null;

  function setSize(w, dw, dh) {
    var nw = Math.min(COLS, Math.max(1, +w.dataset.w + dw)), nh = Math.min(MAXH, Math.max(1, +w.dataset.h + dh));
    w.dataset.w = nw; w.dataset.h = nh; w.style.setProperty('--w', nw); w.style.setProperty('--h', nh);
  }
  function edit(on) {
    dash.classList.toggle('editing', on); bar.hidden = !on; btn.hidden = on;
    if (on) before = dash.innerHTML;
  }
  btn.addEventListener('click', function () { edit(true); });
  document.getElementById('dash-cancel').addEventListener('click', function () { location.reload(); });

  dash.addEventListener('click', function (e) {
    var b = e.target.closest('button[data-a]'); if (!b) return;
    var w = b.closest('.dash-w'), a = b.dataset.a;
    if (a === 'w-') setSize(w, -1, 0); else if (a === 'w+') setSize(w, 1, 0);
    else if (a === 'h-') setSize(w, 0, -1); else if (a === 'h+') setSize(w, 0, 1);
    else if (a === 'x') {
      var o = document.createElement('option'); o.value = w.dataset.k;
      o.textContent = w.querySelector('.dash-name').textContent; add.appendChild(o);
      tpl.content.appendChild(w);
    }
  });
  add.addEventListener('change', function () {
    var k = add.value; if (!k) return;
    var w = tpl.content.querySelector('.dash-w[data-k="' + k + '"]');
    if (w) dash.insertBefore(w, dash.firstChild);
    add.querySelector('option[value="' + k + '"]').remove(); add.value = '';
    w && w.scrollIntoView({ block: 'center' });
  });

  // Przeciaganie za uchwyt: mysz i palec (pointer events).
  var drag = null;
  dash.addEventListener('pointerdown', function (e) {
    var g = e.target.closest('.dash-grip'); if (!g || !dash.classList.contains('editing')) return;
    drag = g.closest('.dash-w'); drag.classList.add('dragging'); g.setPointerCapture(e.pointerId); e.preventDefault();
  });
  dash.addEventListener('pointermove', function (e) {
    if (!drag) return;
    var el = document.elementFromPoint(e.clientX, e.clientY), over = el && el.closest('.dash-w');
    if (!over || over === drag || over.parentNode !== dash) return;
    var r = over.getBoundingClientRect();
    var after = (e.clientY - r.top) > r.height / 2 || (e.clientX - r.left) > r.width * 0.66;
    dash.insertBefore(drag, after ? over.nextSibling : over);
  });
  function drop() { if (drag) { drag.classList.remove('dragging'); drag = null; } }
  dash.addEventListener('pointerup', drop); dash.addEventListener('pointercancel', drop);

  function send(body) {
    return fetch('/start/uklad', { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      .then(function (r) { if (!r.ok) throw new Error(r.status); location.reload(); })
      .catch(function () { alert('Nie udało się zapisać układu. Spróbuj jeszcze raz.'); });
  }
  document.getElementById('dash-save').addEventListener('click', function () {
    var items = Array.prototype.map.call(dash.querySelectorAll(':scope > .dash-w'), function (w) {
      return { k: w.dataset.k, w: +w.dataset.w, h: +w.dataset.h };
    });
    send({ items: items });
  });
  document.getElementById('dash-reset').addEventListener('click', function () {
    if (confirm('Przywrócić domyślny układ strony Start?')) send({ reset: true });
  });
})();
