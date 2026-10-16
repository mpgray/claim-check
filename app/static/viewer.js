// Snapshot viewer: the captured article (or a receipt's source) as stacked
// tiles in a dialog, with a numbered marker on every highlighted claim and
// prev/next to step through them. No new tab, no hunting for the highlight.
(function () {
  'use strict';
  var body = document.getElementById('report-body');
  if (!body) return;
  var rid = body.dataset.id;
  var manifest = null, entries = [], index = 0, rendered = null, zoom = 'fit';
  var dlg, vbody, vpage, vtitle, vspan, vcount, vbadge;

  function load() {
    if (manifest) return Promise.resolve(manifest);
    return fetch('/r/' + encodeURIComponent(rid) + '/view.json').then(function (r) { return r.json(); })
      .then(function (m) { manifest = m; return m; });
  }

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  function build() {
    if (dlg) return;
    dlg = el('dialog', 'viewer');
    dlg.setAttribute('aria-label', 'Snapshot viewer');
    var head = el('div', 'vhead');
    vtitle = el('strong', 'vtitle');
    var nav = el('div', 'vnav');
    var prev = el('button', 'vbtn', '‹ Prev');
    var next = el('button', 'vbtn', 'Next ›');
    vcount = el('span', 'vcount');
    var zoomBtn = el('button', 'vbtn', 'Actual size');
    var close = el('button', 'vbtn vclose', 'Close');
    prev.type = next.type = zoomBtn.type = close.type = 'button';
    prev.onclick = function () { go(index - 1); };
    next.onclick = function () { go(index + 1); };
    zoomBtn.onclick = function () {
      zoom = zoom === 'fit' ? 'actual' : 'fit';
      zoomBtn.textContent = zoom === 'fit' ? 'Actual size' : 'Fit width';
      rendered = null;
      go(index);
    };
    close.onclick = function () { dlg.close(); };
    nav.append(prev, vcount, next, zoomBtn, close);
    head.append(vtitle, nav);
    var info = el('div', 'vinfo');
    vbadge = el('span', 'badge');
    vspan = el('span', 'vspan');
    info.append(vbadge, vspan);
    vbody = el('div', 'vbody');
    vpage = el('div', 'vpage');
    vbody.append(vpage);
    dlg.append(head, info, vbody);
    document.body.append(dlg);
    dlg.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowRight') { go(index + 1); e.preventDefault(); }
      if (e.key === 'ArrowLeft') { go(index - 1); e.preventDefault(); }
    });
    dlg.addEventListener('click', function (e) { if (e.target === dlg) dlg.close(); });
    window.addEventListener('resize', function () { if (dlg.open) { rendered = null; go(index); } });
  }

  function scale(view) {
    return zoom === 'fit' ? Math.min(1, (vbody.clientWidth || view.width) / view.width) : 1;
  }

  function render(view, markers) {
    var s = scale(view);
    vpage.innerHTML = '';
    vpage.style.width = view.width * s + 'px';
    vpage.style.height = view.height * s + 'px';
    view.tiles.forEach(function (t) {
      var img = el('img');
      img.src = manifest.base + t.name;
      img.loading = 'lazy';
      img.alt = '';
      img.style.top = t.top * s + 'px';
      img.style.height = t.height * s + 'px';
      img.style.width = view.width * s + 'px';
      vpage.append(img);
    });
    markers.forEach(function (m) {
      var box = el('button', 'vmark v-' + m.verdict);
      box.type = 'button';
      box.dataset.i = m.i;
      box.style.left = (m.rect.x - 4) * s + 'px';
      box.style.top = (m.rect.y - 4) * s + 'px';
      box.style.width = (m.rect.w + 8) * s + 'px';
      box.style.height = (m.rect.h + 8) * s + 'px';
      box.setAttribute('aria-label', 'Claim ' + m.id);
      var tag = el('span', 'vtag', '#' + m.id);
      box.append(tag);
      box.onclick = function () { go(m.i); };
      vpage.append(box);
    });
    rendered = view;
  }

  function go(i) {
    if (!entries.length) return;
    index = (i + entries.length) % entries.length;
    var e = entries[index];
    if (rendered !== e.view) {
      var markers = entries.map(function (x, j) { return { i: j, id: x.id, verdict: x.verdict, rect: x.rect, view: x.view }; })
        .filter(function (x) { return x.view === e.view; });
      render(e.view, markers);
    }
    vpage.querySelectorAll('.vmark').forEach(function (b) { b.classList.toggle('on', +b.dataset.i === index); });
    vcount.textContent = (index + 1) + ' of ' + entries.length;
    vbadge.textContent = e.verdict;
    vbadge.className = 'badge v-' + e.verdict;
    vspan.textContent = (e.kind === 'source' ? 'Quote: “' + e.text + '”' : '#' + e.id + ' ' + e.text);
    var s = scale(e.view);
    vbody.scrollTo({ top: Math.max(0, e.rect.y * s - vbody.clientHeight / 3),
                     left: Math.max(0, e.rect.x * s - 40), behavior: 'smooth' });
  }

  function open(kind, claimId) {
    return load().then(function (m) {
      entries = [];
      if (kind === 'article') {
        if (!m.article) return false;
        m.claims.forEach(function (c) {
          var r = m.article.rects[String(c.id)];
          if (r) entries.push({ id: c.id, verdict: c.verdict, text: c.span, rect: r, view: m.article, kind: 'article' });
        });
      } else {
        m.claims.forEach(function (c) {
          if (c.source && c.source.rect) entries.push({ id: c.id, verdict: c.verdict, text: c.quote, rect: c.source.rect, view: c.source, kind: 'source' });
        });
      }
      if (!entries.length) return false;
      build();
      vtitle.textContent = kind === 'article' ? 'Article snapshot · ' + m.captured_at : 'Receipt sources as captured';
      rendered = null;
      dlg.showModal();
      var start = 0;
      entries.forEach(function (e, j) { if (e.id === claimId) start = j; });
      go(start);
      return true;
    });
  }

  document.addEventListener('click', function (e) {
    var t = e.target.closest('[data-view]');
    if (!t || e.metaKey || e.ctrlKey || e.shiftKey) return;
    e.preventDefault();
    var href = t.getAttribute('href');
    open(t.dataset.view, t.dataset.claim ? +t.dataset.claim : null).then(function (ok) {
      if (!ok && href) window.open(href, '_blank', 'noopener');
    });
  });
  // A report re-rendered live gets a fresh manifest on the next open.
  body.addEventListener('cc:refreshed', function () { manifest = null; });
})();
