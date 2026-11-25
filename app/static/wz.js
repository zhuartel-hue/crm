// Formularz WZ: pozycje z listy produktow, kontrahent z bazy.
(function () {
  const items = document.getElementById('items');
  const gross = document.getElementById('prices_gross');
  const num = (s) => { const v = parseFloat(String(s || '').replace(/\s/g, '').replace(',', '.')); return isNaN(v) ? null : v; };
  const fmt = (v) => v.toFixed(2).replace('.', ',').replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
  function newRow(name, unit) {
    let tpl = items.querySelector('.item');
    const last = items.querySelectorAll('.item');
    const lastRow = last[last.length - 1];
    if (name && lastRow && !lastRow.querySelector('[name=item_name]').value) tpl = lastRow;
    else { tpl = tpl.cloneNode(true); items.appendChild(tpl); }
    tpl.querySelectorAll('input').forEach((i) => { i.value = i.name === 'item_qty' ? '1' : i.name === 'item_unit' ? (unit || 'szt.') : ''; });
    tpl.querySelector('[name=item_rate]').value = '23';
    if (name) { tpl.querySelector('[name=item_name]').value = name; tpl.querySelector('[name=item_qty]').select(); }
    tpl.querySelector(name ? '[name=item_qty]' : '[name=item_name]').focus();
    total();
  }
  function total() {
    let sum = 0, any = false;
    items.querySelectorAll('.item').forEach((r) => {
      const q = num(r.querySelector('[name=item_qty]').value), p = num(r.querySelector('[name=item_price]').value);
      const rate = r.querySelector('[name=item_rate]').value, pct = /^\d+$/.test(rate) ? +rate : 0;
      if (q === null || p === null) return;
      any = true;
      const v = Math.round(q * p * 100) / 100;
      sum += gross && gross.checked ? v : v + Math.round(v * pct) / 100;
    });
    document.getElementById('total').textContent = any ? 'Razem brutto: ' + fmt(sum) + ' zł' : '';
  }
  document.getElementById('add').addEventListener('click', () => newRow());
  document.querySelectorAll('[data-quick]').forEach((b) => b.addEventListener('click', () => newRow(b.dataset.quick, b.dataset.unit)));
  items.addEventListener('input', total);
  items.addEventListener('change', total);
  if (gross) gross.addEventListener('change', total);
  items.addEventListener('click', (e) => {
    if (!e.target.classList.contains('remove')) return;
    const rows = items.querySelectorAll('.item');
    if (rows.length > 1) e.target.closest('.item').remove();
    else e.target.closest('.item').querySelectorAll('input').forEach((i) => { i.value = ''; });
  });
  items.addEventListener('input', (e) => {
    const el = e.target;
    if (el.name !== 'item_name') return;
    const row = el.closest('.item');
    const p = PRODUCTS[el.value];
    const hidden = row.querySelector('[name=item_product]');
    if (p) {
      hidden.value = p.id; el.value = p.name; row.querySelector('[name=item_unit]').value = p.unit || 'szt.';
      if (p.vat) row.querySelector('[name=item_rate]').value = p.vat;
      const price = gross && gross.checked ? p.gross : p.net;
      if (price) row.querySelector('[name=item_price]').value = String(price).replace('.', ',');
    }
    else if (!el.value) hidden.value = '';
  });
  total();
  const sel = document.getElementById('contractor');
  sel.addEventListener('change', () => {
    const o = sel.selectedOptions[0];
    if (!o || !o.value) return;
    document.getElementById('recipient_name').value = o.dataset.name || '';
    document.getElementById('recipient_address').value = o.dataset.address || '';
  });
})();
