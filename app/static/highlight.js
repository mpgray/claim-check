// Injected into a rendered page by app/snapshot.py. Finds text the pipeline
// already verified (claim spans, receipt quotes) in the live DOM and paints it
// with the CSS Custom Highlight API, which changes no layout and no markup.
//
// Matching uses the same normalisation as app/claims.norm: curly quotes and
// dashes flattened, non-breaking spaces to spaces, whitespace collapsed,
// lower-cased. A miss is reported, never fudged: {found: false}.
(function () {
  'use strict';
  var FLAT = { '‘': "'", '’': "'", '“': '"', '”': '"',
               '–': '-', '—': '-', ' ': ' ' };
  var SKIP = { SCRIPT: 1, STYLE: 1, NOSCRIPT: 1, TEMPLATE: 1, SVG: 1, HEAD: 1 };

  var blockCache = new WeakMap();
  function blockOf(el) {
    var start = el, seen = [];
    while (el && el !== document.body) {
      if (blockCache.has(el)) { var b = blockCache.get(el); seen.forEach(function (s) { blockCache.set(s, b); }); return b; }
      seen.push(el);
      var d = getComputedStyle(el).display;
      if (d && d.indexOf('inline') !== 0 && d !== 'contents') {
        seen.forEach(function (s) { blockCache.set(s, el); });
        return el;
      }
      el = el.parentElement;
    }
    seen.forEach(function (s) { blockCache.set(s, document.body); });
    return document.body;
  }

  function visible(el) {
    if (el.checkVisibility) return el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true });
    return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  }

  // One normalised string for the whole page, and for every character in it
  // the text node and offset it came from.
  function index() {
    var out = [], nodes = [], offs = [];
    var lastBlock = null, lastSpace = true, lastNode = null, lastOff = 0;
    var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
      acceptNode: function (n) {
        var p = n.parentElement;
        if (!p || SKIP[p.tagName.toUpperCase()] || !n.data.trim()) return NodeFilter.FILTER_REJECT;
        return visible(p) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
      }
    });
    var n;
    while ((n = walker.nextNode())) {
      var block = blockOf(n.parentElement);
      if (lastBlock && block !== lastBlock && !lastSpace) {
        // Passages are separate blocks in Python's parse; keep a space between.
        out.push(' '); nodes.push(lastNode); offs.push(lastOff); lastSpace = true;
      }
      lastBlock = block;
      var s = n.data;
      for (var i = 0; i < s.length; i++) {
        var c = FLAT[s[i]] || s[i];
        if (/\s/.test(c)) {
          if (lastSpace) continue;
          out.push(' '); nodes.push(n); offs.push(i); lastSpace = true;
          continue;
        }
        var low = c.toLowerCase();
        for (var k = 0; k < low.length; k++) { out.push(low[k]); nodes.push(n); offs.push(i); }
        lastSpace = false;
      }
      lastNode = n; lastOff = s.length;
    }
    return { text: out.join(''), nodes: nodes, offs: offs };
  }

  function norm(t) {
    var s = '';
    for (var i = 0; i < t.length; i++) s += FLAT[t[i]] || t[i];
    return s.replace(/\s+/g, ' ').trim().toLowerCase();
  }

  function rangeAt(idx, start, end) {
    var r = document.createRange();
    r.setStart(idx.nodes[start], idx.offs[start]);
    var en = idx.nodes[end - 1];
    r.setEnd(en, Math.min(en.data.length, idx.offs[end - 1] + 1));
    return r;
  }

  // Exact match first; then start..end anchors (like a text fragment), for
  // pages whose markup inserts something the Python parse did not see.
  function find(idx, text) {
    var t = norm(text);
    if (!t) return null;
    var at = idx.text.indexOf(t);
    if (at >= 0) return { start: at, end: at + t.length, exact: true };
    if (t.length < 60) return null;
    var head = t.slice(0, 40), tail = t.slice(-40);
    var s = idx.text.indexOf(head);
    while (s >= 0) {
      var e = idx.text.indexOf(tail, s + head.length);
      if (e >= 0 && e + tail.length - s <= t.length * 1.5) return { start: s, end: e + tail.length, exact: false };
      s = idx.text.indexOf(head, s + 1);
    }
    return null;
  }

  function pageRect(range) {
    var b = range.getBoundingClientRect();
    return { x: b.left + window.scrollX, y: b.top + window.scrollY, w: b.width, h: b.height };
  }

  var ranges = {};

  window.__cc = {
    // items: [{id, text, cls}] -> {id: {found, exact, rect}}
    highlight: function (items, colours) {
      if (!window.CSS || !CSS.highlights) return { error: 'no CSS Custom Highlight API' };
      var css = '';
      Object.keys(colours).forEach(function (cls) {
        css += '::highlight(cc-' + cls + '){background-color:' + colours[cls] + ';color:#111;}';
      });
      css += '::highlight(cc-focus){text-decoration:underline 3px #111;text-underline-offset:3px;}';
      var style = document.createElement('style');
      style.textContent = css;
      document.head.appendChild(style);
      var idx = index(), groups = {}, res = {};
      items.forEach(function (it) {
        var m = find(idx, it.text);
        if (!m) { res[it.id] = { found: false }; return; }
        var r = rangeAt(idx, m.start, m.end);
        ranges[it.id] = r;
        (groups[it.cls] = groups[it.cls] || []).push(r);
        res[it.id] = { found: true, exact: m.exact, rect: pageRect(r) };
      });
      Object.keys(groups).forEach(function (cls) {
        CSS.highlights.set('cc-' + cls, new Highlight(...groups[cls]));
      });
      return res;
    },
    // Underline one item so its crop shows which highlight it is about.
    focus: function (id) {
      CSS.highlights.delete('cc-focus');
      if (ranges[id]) CSS.highlights.set('cc-focus', new Highlight(ranges[id]));
      return !!ranges[id];
    },
    // Overlays (cookie bars, sticky headers, chat bubbles) cover the text a
    // crop is about; take them out of the picture. Nothing is clicked.
    unclutter: function () {
      var n = 0;
      document.querySelectorAll('body *').forEach(function (el) {
        var p = getComputedStyle(el).position;
        if (p === 'fixed') { el.style.setProperty('display', 'none', 'important'); n++; }
        else if (p === 'sticky') { el.style.setProperty('position', 'static', 'important'); n++; }
      });
      document.documentElement.style.setProperty('overflow', 'visible', 'important');
      document.body.style.setProperty('overflow', 'visible', 'important');
      return n;
    },
    height: function () {
      return Math.max(document.body.scrollHeight, document.documentElement.scrollHeight);
    }
  };
})();
