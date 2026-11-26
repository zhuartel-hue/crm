// Wyszukiwarka kontrahenta zamiast dlugiej listy: kazdy <select name="contractor_id"> dostaje pole, w ktore
// wpisuje sie nazwe, NIP albo miasto; wybor ustawia ukryty select i wysyla "change" (reszta formularza dziala jak dawniej).
(function () {
  const norm = s => (s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/ł/g, 'l')
    .replace(/[-\s]+/g, ' ');
  document.querySelectorAll('select[name=contractor_id]').forEach(sel => {
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
    input.placeholder = 'wpisz nazwę, NIP albo miasto';
    input.setAttribute('aria-label', 'Szukaj kontrahenta');
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
      items = !q ? [] : opts.filter(o => words.every(w => o.key.includes(w)) || (qd.length >= 3 && o.digits.includes(qd)))
        .slice(0, 15);
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
        p.textContent = 'Nie ma w bazie: ' + (empty ? 'wpisz dane ręcznie poniżej albo pobierz po NIP.' : 'dodaj go w Kontrahentach.');
        list.append(p);
      }
      list.hidden = !q;
    };
    input.addEventListener('input', () => {
      active = -1;
      if (!input.value.trim() && sel.value) choose(null);
      render();
    });
    input.addEventListener('focus', () => { if (current()) input.select(); });
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
})();
