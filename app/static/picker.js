// Wyszukiwarka kontrahenta zamiast dlugiej listy: kazdy <select name="contractor_id"> dostaje pole, w ktore
// wpisuje sie nazwe, NIP albo miasto; wybor ustawia ukryty select i wysyla "change" (reszta formularza dziala jak dawniej).
(function () {
  const norm = s => (s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/ł/g, 'l')
    .replace(/[-\s]+/g, ' ');
  // select.searchable (np. status oferty): ta sama wyszukiwarka, pusta fraza pokazuje wszystkie opcje.
  function init(root) {
  (root || document).querySelectorAll('select[name=contractor_id], select.searchable').forEach(sel => {
    if (sel.dataset.picker) return;
    sel.dataset.picker = '1';
    const all = sel.classList.contains('searchable');
    const opts = [...sel.options].filter(o => o.value).map(o => ({
      value: o.value, label: o.textContent.trim(), key: norm(o.textContent + ' ' + (o.dataset.search || '')),
      digits: (o.textContent + ' ' + (o.dataset.search || '')).replace(/\D/g, '')
    }));
    const empty = sel.querySelector('option[value=""]');
    const wrap = document.createElement('div');
    wrap.className = 'picker';
    const input = document.createElement('input');
    input.type = 'search';
    input.autocomplete = 'off';
    input.placeholder = sel.dataset.placeholder || 'wpisz nazwę, NIP albo miasto';
    input.setAttribute('aria-label', sel.getAttribute('aria-label') || 'Szukaj kontrahenta');
    if (all) { wrap.classList.add('picker-sm'); sel.classList.forEach(c => c !== 'searchable' && input.classList.add(c)); }
    const list = document.createElement('div');
    list.className = 'picker-list';
    list.hidden = true;
    wrap.append(input, list);
    sel.hidden = true;
    sel.after(wrap);
    const current = () => opts.find(o => o.value === sel.value);
    const show = () => { input.value = current() ? current().label : ''; };
    show();
    let items = [], active = -1;
    const choose = o => {
      sel.value = o ? o.value : '';
      sel.dispatchEvent(new Event('change', { bubbles: true }));
      show();
      list.hidden = true;
    };
    const render = () => {
      const q = norm(input.value).trim();
      const words = q.split(' ').filter(Boolean);
      const qd = input.value.replace(/\D/g, '');
      items = !q ? (all ? opts.slice() : []) : opts.filter(o => words.every(w => o.key.includes(w)) || (qd.length >= 3 && o.digits.includes(qd)))
        .slice(0, all ? 50 : 15);
      list.innerHTML = '';
      items.forEach((o, i) => {
        const b = document.createElement('button');
        b.type = 'button';
        b.textContent = o.label;
        b.className = i === active ? 'on' : '';
        b.addEventListener('mousedown', e => { e.preventDefault(); choose(o); });
        list.append(b);
      });
      if (q && !items.length) {
        const p = document.createElement('div');
        p.className = 'muted';
        p.textContent = all ? 'Brak takiej pozycji.' : 'Nie ma w bazie: ' + (empty ? 'wpisz dane ręcznie poniżej albo pobierz po NIP.' : 'dodaj go w Kontrahentach.');
        list.append(p);
      }
      list.hidden = !q && !all;
      if (all && !list.hidden) {  // nad tabela z przewijaniem: lista przypieta do okna
        const r = input.getBoundingClientRect();
        Object.assign(list.style, { position: 'fixed', left: r.left + 'px', top: (r.bottom + 2) + 'px',
          width: Math.max(220, r.width) + 'px', right: 'auto' });
      }
    };
    input.addEventListener('input', () => {
      active = -1;
      if (!input.value.trim() && sel.value && !all) choose(null);
      render();
    });
    input.addEventListener('focus', () => { if (current()) input.select(); if (all) { input.value = ''; render(); } });
    input.addEventListener('blur', () => { list.hidden = true; show(); });
    input.addEventListener('keydown', e => {
      if (list.hidden || !items.length) return;
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        active = (active + (e.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length;
        render();
      } else if (e.key === 'Enter') {
        e.preventDefault();
        choose(items[Math.max(active, 0)]);
      } else if (e.key === 'Escape') {
        list.hidden = true;
      }
    });
  });
  }
  window.sygnavoPicker = init;
  init();
})();
