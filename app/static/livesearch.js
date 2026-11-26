// Wyszukiwanie na zywo: listy z polem "q" filtruja sie w trakcie pisania, bez Enter.
// Pobieramy te sama strone z nowym q i podmieniamy wszystko pod formularzem wyszukiwania.
(function () {
  document.querySelectorAll('form input[name="q"]').forEach(function (inp) {
    var form = inp.form;
    if (!form || (form.getAttribute('method') || 'get').toLowerCase() !== 'get') return;
    inp.setAttribute('autocomplete', 'off');
    var timer = null, ctl = null, last = inp.value;
    function go() {
      if (inp.value === last) return;
      last = inp.value;
      var url = new URL(form.getAttribute('action') || location.pathname, location.href);
      url.search = new URLSearchParams(new FormData(form)).toString();
      if (ctl) ctl.abort();
      ctl = new AbortController();
      fetch(url, { signal: ctl.signal, credentials: 'same-origin' }).then(function (r) {
        return r.ok ? r.text() : null;
      }).then(function (html) {
        if (html === null) return;
        var doc = new DOMParser().parseFromString(html, 'text/html');
        var idx = Array.prototype.indexOf.call(document.forms, form);
        var nf = doc.forms[idx];
        if (!nf) return;
        var parent = form.parentNode, a = form.nextElementSibling;
        while (a) { var n = a.nextElementSibling; if (a.tagName !== 'SCRIPT') parent.removeChild(a); a = n; }
        var b = nf.nextElementSibling;
        while (b) { var m = b.nextElementSibling; if (b.tagName !== 'SCRIPT') parent.appendChild(document.importNode(b, true)); b = m; }
        history.replaceState(null, '', url);
        if (window.sygnavoTables) window.sygnavoTables.apply(parent);
        if (window.sygnavoPicker) window.sygnavoPicker(parent);
      }).catch(function () {});
    }
    inp.addEventListener('input', function () { clearTimeout(timer); timer = setTimeout(go, 250); });
  });
})();
