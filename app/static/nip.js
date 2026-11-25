// Przycisk "Pobierz dane po NIP": pyta /nip/<nip> (biala lista VAT) i uzupelnia pola formularza.
(function () {
  document.querySelectorAll('[data-nip-lookup]').forEach((btn) => {
    const cfg = JSON.parse(btn.dataset.nipLookup);
    const $ = (id) => (id ? document.getElementById(id) : null);
    const info = $(cfg.info);
    btn.addEventListener('click', async () => {
      const nip = ($(cfg.nip).value || '').replace(/\D/g, '');
      if (nip.length !== 10) { if (info) info.textContent = 'Wpisz 10 cyfr NIP.'; return; }
      btn.disabled = true; if (info) info.textContent = 'Sprawdzam w wykazie VAT…';
      try {
        const r = await fetch('/nip/' + nip, { headers: { Accept: 'application/json' } });
        const d = await r.json();
        if (!r.ok) { if (info) info.textContent = d.error || 'Błąd'; return; }
        $(cfg.nip).value = d.nip;
        if ($(cfg.name)) $(cfg.name).value = d.name;
        if ($(cfg.street)) $(cfg.street).value = d.street;
        if ($(cfg.postal)) $(cfg.postal).value = d.postal_code;
        if ($(cfg.city)) $(cfg.city).value = d.city;
        if ($(cfg.address)) $(cfg.address).value = [d.street, [d.postal_code, d.city].join(' ').trim()].filter(Boolean).join('\n');
        if ($(cfg.company)) $(cfg.company).checked = true;
        if (info) info.textContent = 'Status VAT: ' + (d.status_vat || '—') + (d.regon ? ' · REGON ' + d.regon : '') +
          (d.accounts.length ? ' · rachunki na białej liście: ' + d.accounts.join(', ') : ' · brak rachunków w wykazie');
      } catch (e) {
        if (info) info.textContent = 'Brak połączenia.';
      } finally { btn.disabled = false; }
    });
  });
})();
