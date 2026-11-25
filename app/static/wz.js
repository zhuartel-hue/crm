// Formularz WZ: pozycje z listy produktow, kontrahent z bazy.
(function () {
  const items = document.getElementById('items');
  document.getElementById('add').addEventListener('click', () => {
    const tpl = items.querySelector('.item').cloneNode(true);
    tpl.querySelectorAll('input').forEach((i) => { i.value = i.name === 'item_qty' ? '1' : i.name === 'item_unit' ? 'szt.' : ''; });
    items.appendChild(tpl);
    tpl.querySelector('[name=item_name]').focus();
  });
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
    if (p) { hidden.value = p.id; el.value = p.name; row.querySelector('[name=item_unit]').value = p.unit || 'szt.'; }
    else if (!el.value) hidden.value = '';
  });
  const sel = document.getElementById('contractor');
  sel.addEventListener('change', () => {
    const o = sel.selectedOptions[0];
    if (!o || !o.value) return;
    document.getElementById('recipient_name').value = o.dataset.name || '';
    document.getElementById('recipient_address').value = o.dataset.address || '';
  });
})();
