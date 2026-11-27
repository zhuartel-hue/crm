// Formularz faktury: pozycje, podglad kwot, wybor kontrahenta i auta.
(function () {
  const items = document.getElementById('items');
  const num = (s) => { const v = parseFloat(String(s || '').replace(/\s/g, '').replace(',', '.')); return isNaN(v) ? 0 : v; };
  const r2 = (v) => Math.round((v + Number.EPSILON) * 100) / 100;
  const fmt = (v) => v.toLocaleString('pl-PL', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const grossBox = document.getElementById('prices_gross');

  function recalc() {
    let net = 0, vat = 0, gross = 0;
    const isGross = grossBox && grossBox.checked;
    items.querySelectorAll('.item').forEach((row) => {
      const q = num(row.querySelector('[name=item_qty]').value);
      const p = num(row.querySelector('[name=item_price]').value);
      const value = r2(q * p);
      let n, v, g, info;
      const rate = row.querySelector('[name=item_rate]').value;
      const pct = /^\d+$/.test(rate) ? +rate : 0;
      if (isGross) { g = value; v = r2(g * pct / (100 + pct)); n = r2(g - v); }
      else { n = value; v = r2(n * pct / 100); g = r2(n + v); }
      info = `Netto ${fmt(n)} · VAT ${fmt(v)} · Brutto ${fmt(g)}`;
      row.querySelector('.calc').textContent = info;
      net += n; vat += v; gross += g;
    });
    document.getElementById('totals').innerHTML = `Netto ${fmt(net)} · VAT ${fmt(vat)} · <b>Brutto ${fmt(gross)} zł</b>`;
    document.querySelectorAll('.price-label').forEach((l) => { l.textContent = isGross ? 'Cena brutto' : 'Cena netto'; });
  }

  document.getElementById('add').addEventListener('click', () => {
    const tpl = items.querySelector('.item').cloneNode(true);
    tpl.querySelectorAll('input').forEach((i) => { i.value = i.name === 'item_qty' ? '1' : i.name === 'item_unit' ? 'szt.' : ''; });
    items.appendChild(tpl); recalc();
  });
  items.addEventListener('click', (e) => {
    if (!e.target.classList.contains('remove')) return;
    const rows = items.querySelectorAll('.item');
    if (rows.length > 1) e.target.closest('.item').remove();
    else e.target.closest('.item').querySelectorAll('input').forEach((i) => { i.value = ''; });
    recalc();
  });
  // Produkt z katalogu: wybor z listy wypelnia nazwe, jednostke, cene i VAT.
  items.addEventListener('input', (e) => {
    const el = e.target;
    if (el.name === 'item_name' && typeof PRODUCTS !== 'undefined') {
      const row = el.closest('.item');
      const hidden = row.querySelector('[name=item_product]');
      const p = PRODUCTS[el.value];
      if (p) {
        el.value = p.name;
        row.dataset.pname = p.name;
        hidden.value = p.id;
        row.querySelector('[name=item_unit]').value = p.unit;
        const price = grossBox && grossBox.checked ? p.gross : p.net;
        if (price !== null) row.querySelector('[name=item_price]').value = price.replace('.', ',');
        row.querySelector('[name=item_rate]').value = p.vat;
      } else if (hidden.value && el.value !== row.dataset.pname) {
        hidden.value = '';
      }
    }
    recalc();
  });
  if (grossBox) grossBox.addEventListener('change', recalc);

  const contractor = document.getElementById('contractor');
  const saveWrap = document.getElementById('save-wrap');
  contractor.addEventListener('change', () => {
    const c = CONTRACTORS[contractor.value];
    saveWrap.style.display = c ? 'none' : '';
    if (!c) return;
    document.getElementById('buyer_name').value = c.name;
    document.getElementById('buyer_nip').value = c.nip;
    document.getElementById('buyer_address').value = c.address;
  });
  saveWrap.style.display = contractor.value ? 'none' : '';

  // Wybor auta na nowej fakturze przeladowuje formularz z danymi auta.
  const car = document.getElementById('car');
  const isNew = !/\/edytuj$/.test(location.pathname);
  car.addEventListener('change', () => {
    if (isNew && car.value) {
      const u = new URL(location.href); u.searchParams.set('auto', car.value); location.href = u;
    }
  });
  recalc();
})();
