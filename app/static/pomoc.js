/* Okienko „Pomoc”: asystent AI Sygnavo (app/assistant.py).
 * - rozmowa trzymana w sessionStorage tej karty (tylko wygoda; serwer niczego nie pamieta),
 * - „Pokaz na ekranie” podswietla przyciski na zywo (czerwona ramka i numer),
 * - SygPomoc.highlight() uzywa tez skrypt zrzutow tools/zrzuty_pomocy.py, wiec zrzuty i podswietlenie sa identyczne.
 */
(function () {
  'use strict';
  const KEY = 'syg-pomoc-v1';

  function find(step) {
    let els = [];
    try { els = Array.from(document.querySelectorAll(step.css || '*')); } catch (e) { return null; }
    if (step.text) {
      const t = step.text.toLowerCase();
      els = els.filter((el) => (el.innerText || el.value || el.getAttribute('aria-label') || '').toLowerCase().includes(t));
      // najglebszy pasujacy element (np. przycisk, a nie cala sekcja)
      els = els.filter((el) => !els.some((o) => o !== el && el.contains(o)));
    }
    return els.find((el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; }) || null;
  }

  function clear() { document.querySelectorAll('.pz-hl').forEach((n) => n.remove()); }

  function highlight(steps, opts) {
    opts = opts || {};
    clear();
    const missing = [];
    let first = null;
    (steps || []).forEach((step, i) => {
      const el = find(step);
      if (!el) { missing.push(i + 1); return; }
      if (!first) first = el;
      if (opts.scroll !== false && i === 0) el.scrollIntoView({ block: 'center', inline: 'nearest' });
    });
    (steps || []).forEach((step, i) => {
      const el = find(step);
      if (!el) return;
      const r = el.getBoundingClientRect();
      const box = document.createElement('div');
      box.className = 'pz-hl';
      box.style.cssText = `left:${r.left + window.scrollX - 4}px;top:${r.top + window.scrollY - 4}px;width:${r.width + 8}px;height:${r.height + 8}px`;
      const n = document.createElement('span');
      n.textContent = String(i + 1);
      box.appendChild(n);
      document.body.appendChild(box);
    });
    if (!opts.keep) setTimeout(clear, 9000);
    return missing;
  }

  window.SygPomoc = { highlight, find, clear };

  const root = document.getElementById('pz');
  if (!root) return;
  const panel = root.querySelector('.pz-panel');
  const log = root.querySelector('.pz-log');
  const form = root.querySelector('form');
  const input = form.querySelector('textarea');
  const btn = root.querySelector('.pz-btn');
  let state = { open: false, msgs: [], screens: {} };
  try { state = Object.assign(state, JSON.parse(sessionStorage.getItem(KEY) || '{}')); } catch (e) { /* bez pamieci */ }
  const save = () => { try { sessionStorage.setItem(KEY, JSON.stringify(state)); } catch (e) { /* trudno */ } };

  function bubble(m) {
    const d = document.createElement('div');
    d.className = 'pz-m ' + (m.role === 'user' ? 'pz-u' : 'pz-a');
    if (m.role === 'user') d.textContent = m.text; else d.innerHTML = m.html || '';
    if (m.role === 'assistant' && m.id) {
      const r = document.createElement('div');
      r.className = 'pz-rate';
      r.innerHTML = '<span>Pomogło?</span><button type="button" data-r="1">Tak</button><button type="button" data-r="-1">Nie</button>';
      if (m.rated) r.innerHTML = '<span>Dziękujemy za ocenę.</span>';
      r.addEventListener('click', (e) => {
        const b = e.target.closest('button'); if (!b) return;
        fetch('/pomoc/ocena/' + m.id, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ rating: Number(b.dataset.r) }) });
        m.rated = true; save(); r.innerHTML = '<span>Dziękujemy za ocenę.</span>';
      });
      d.appendChild(r);
    }
    log.appendChild(d);
  }

  function renderAll() {
    log.innerHTML = '';
    if (!state.msgs.length) {
      const d = document.createElement('div');
      d.className = 'pz-m pz-a';
      d.innerHTML = '<p>Cześć! Zapytaj, jak coś zrobić w Sygnavo, np. <i>jak wystawić fakturę</i> albo <i>jak dodać logo</i>. Pokażę, gdzie kliknąć.</p><p><a href="/pomoc/artykuly">Wszystkie instrukcje</a></p>';
      log.appendChild(d);
    }
    state.msgs.forEach(bubble);
    log.scrollTop = log.scrollHeight;
  }

  function setOpen(open) {
    state.open = open; save();
    root.classList.toggle('open', open);
    if (open) { renderAll(); setTimeout(() => input.focus(), 50); }
  }

  btn.addEventListener('click', () => setOpen(!state.open));
  root.querySelector('.pz-close').addEventListener('click', () => setOpen(false));
  root.querySelector('.pz-new').addEventListener('click', () => { state.msgs = []; state.screens = {}; save(); renderAll(); });

  let pageScreens = {};
  try { pageScreens = JSON.parse((document.getElementById('pz-screens') || {}).textContent || '{}'); } catch (e) { /* brak */ }
  // Zrzuty w okienku i na stronie instrukcji: powiekszenie i „Pokaz na ekranie”.
  document.addEventListener('click', (e) => {
    const img = e.target.closest('.pz-fig img');
    if (img) { window.open(img.src, '_blank'); return; }
    const b = e.target.closest('.pz-show'); if (!b) return;
    const s = state.screens[b.dataset.zrzut] || pageScreens[b.dataset.zrzut]; if (!s) return;
    if (s.page && s.page !== location.pathname) {
      sessionStorage.setItem('syg-pomoc-show', JSON.stringify(s));
      location.href = s.page;
      return;
    }
    if (window.innerWidth < 900) setOpen(false);
    const miss = highlight(s.steps);
    if (miss.length === (s.steps || []).length) alert('Na tej stronie nie widzę tego przycisku. Sprawdź zrzut powyżej.');
  });

  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); }
  });

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const q = input.value.trim();
    if (!q || form.classList.contains('busy')) return;
    const history = state.msgs.filter((m) => m.text).slice(-8).map((m) => ({ role: m.role, text: m.text }));
    state.msgs.push({ role: 'user', text: q });
    input.value = '';
    renderAll();
    form.classList.add('busy');
    const wait = document.createElement('div');
    wait.className = 'pz-m pz-a pz-wait'; wait.textContent = 'Szukam odpowiedzi…';
    log.appendChild(wait); log.scrollTop = log.scrollHeight;
    try {
      const r = await fetch('/pomoc/pytanie', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: q, page: location.pathname, history }),
      });
      const j = await r.json();
      Object.assign(state.screens, j.screens || {});
      state.msgs.push({ role: 'assistant', text: j.text || '', html: j.html || '', id: j.id || null });
    } catch (err) {
      state.msgs.push({ role: 'assistant', text: '', html: '<p>Brak połączenia. Spróbuj ponownie.</p>' });
    }
    form.classList.remove('busy');
    save(); renderAll();
  });

  if (state.open) setOpen(true);
  // Po przejsciu z „Pokaz na ekranie” na inna strone: podswietl od razu.
  try {
    const pending = JSON.parse(sessionStorage.getItem('syg-pomoc-show') || 'null');
    if (pending) {
      sessionStorage.removeItem('syg-pomoc-show');
      if (window.innerWidth < 900) setOpen(false);
      window.addEventListener('load', () => highlight(pending.steps));
    }
  } catch (e) { /* nic */ }
})();
