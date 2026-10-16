// Live report: follow server-sent events and swap in claim cards as they finish.
(function () {
  'use strict';

  // Relative times for any <time data-ts="unix seconds">.
  function rel(ts) {
    var s = Math.max(0, Date.now() / 1000 - ts);
    if (s < 60) return 'just now';
    if (s < 3600) return Math.floor(s / 60) + ' min ago';
    if (s < 86400) return Math.floor(s / 3600) + ' h ago';
    return new Date(ts * 1000).toLocaleDateString();
  }
  function stamp() {
    document.querySelectorAll('time[data-ts]').forEach(function (t) {
      var ts = parseFloat(t.dataset.ts);
      if (!ts) return;
      t.textContent = rel(ts);
      t.title = new Date(ts * 1000).toLocaleString();
    });
  }
  stamp();
  setInterval(stamp, 30000);

  document.addEventListener('click', function (e) {
    var btn = e.target.closest('[data-copy]');
    if (!btn) return;
    navigator.clipboard.writeText(btn.dataset.copy).then(function () {
      var old = btn.textContent;
      btn.textContent = 'Copied';
      setTimeout(function () { btn.textContent = old; }, 1500);
    });
  });

  // Feedback on a verdict: one vote per claim per browser (remembered locally).
  function votedKey(rid, cid) { return 'cc-vote:' + rid + ':' + cid; }
  function markVotes(root) {
    var b = document.getElementById('report-body');
    if (!b) return;
    root.querySelectorAll('.fb').forEach(function (fb) {
      var v = null;
      try { v = localStorage.getItem(votedKey(b.dataset.id, fb.dataset.claim)); } catch (_) {}
      fb.querySelectorAll('.fbtn').forEach(function (btn) { btn.classList.toggle('on', btn.dataset.vote === v); });
    });
  }
  document.addEventListener('click', function (e) {
    var btn = e.target.closest('.fbtn');
    if (!btn) return;
    var fb = btn.closest('.fb'), b = document.getElementById('report-body');
    var key = votedKey(b.dataset.id, fb.dataset.claim);
    try { if (localStorage.getItem(key)) return; } catch (_) {}
    fetch('/r/' + encodeURIComponent(b.dataset.id) + '/claim/' + fb.dataset.claim + '/feedback', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ vote: btn.dataset.vote })
    }).then(function (r) { return r.json(); }).then(function (counts) {
      try { localStorage.setItem(key, btn.dataset.vote); } catch (_) {}
      fb.querySelector('[data-vote="up"] span').textContent = counts.up || '';
      fb.querySelector('[data-vote="down"] span').textContent = counts.down || '';
      markVotes(fb.parentElement);
      var thanks = fb.querySelector('.thanks') || fb.appendChild(document.createElement('span'));
      thanks.className = 'thanks';
      thanks.textContent = 'Thanks, noted.';
    });
  });
  markVotes(document);
  document.addEventListener('cc:refreshed', function () { markVotes(document); }, true);

  var body = document.getElementById('report-body');
  if (!body || body.dataset.status !== 'running') return;
  var rid = body.dataset.id;
  var progress = document.getElementById('progress');
  var base = '/r/' + encodeURIComponent(rid);

  function get(url) {
    return fetch(url, { headers: { 'X-Requested-With': 'fetch' } }).then(function (r) {
      if (!r.ok) throw new Error(r.status);
      return r.text();
    });
  }
  function refreshBody() {
    return get(base + '/body').then(function (html) {
      body.innerHTML = html;
      body.dispatchEvent(new Event('cc:refreshed'));
    });
  }
  function refreshTitle() {
    return fetch('/api/r/' + encodeURIComponent(rid)).then(function (r) { return r.json(); })
      .then(function (r) {
        if (r.title) {
          document.getElementById('rtitle').textContent = r.title;
          document.title = r.title + ' · Claim Check';
        }
        var d = document.getElementById('rdate');
        if (d && r.date) { d.querySelector('span').textContent = r.date; d.hidden = false; }
      });
  }
  function swapClaim(id) {
    return get(base + '/claim/' + id).then(function (html) {
      var old = document.getElementById('claim-' + id);
      if (!old) return refreshBody();
      var tpl = document.createElement('template');
      tpl.innerHTML = html.trim();
      var fresh = tpl.content.firstElementChild;
      fresh.classList.add('just-done');
      old.replaceWith(fresh);
    });
  }
  function setCounts(d) {
    if (!d || !d.counts) return;
    Object.keys(d.counts).forEach(function (v) {
      var el = body.querySelector('[data-count="' + v + '"]');
      if (el) el.textContent = d.counts[v];
    });
    var pending = d.total - Object.keys(d.counts).reduce(function (a, v) { return a + d.counts[v]; }, 0);
    if (d.total) progress.textContent = pending > 0
      ? 'Checking claims: ' + (d.total - pending) + ' of ' + d.total + ' done…'
      : 'All ' + d.total + ' claims checked.';
  }

  progress.hidden = false;
  progress.textContent = 'Fetching the article…';
  var finished = false;

  function finish() {
    if (finished) return;
    finished = true;
    progress.hidden = true;
    body.dataset.status = 'done';
    refreshBody();
    refreshTitle();
  }

  function onEvent(kind, d) {
    if (kind === 'fetched') { progress.textContent = 'Finding the claims…'; refreshTitle(); refreshBody(); }
    else if (kind === 'claims') { refreshBody().then(function () { setCounts(d); }); }
    else if (kind === 'claim') { swapClaim(d.id); setCounts(d); }
    else if (kind === 'snapshotting') { progress.textContent = 'Capturing snapshots of the article and sources…'; }
    else if (kind === 'snapshots') { refreshBody(); }
    else if (kind === 'done' || kind === 'error') { finish(); }
  }

  if (window.EventSource) {
    var es = new EventSource(base + '/events');
    ['fetched', 'claims', 'claim', 'snapshotting', 'snapshots', 'done', 'error'].forEach(function (k) {
      es.addEventListener(k, function (e) {
        var d = {};
        try { d = JSON.parse(e.data); } catch (_) {}
        onEvent(k, d);
        if (k === 'done' || k === 'error') es.close();
      });
    });
    es.onerror = function () {
      if (finished) return;
      es.close();
      poll();
    };
  } else {
    poll();
  }

  // Fallback: poll the JSON until the report is no longer running.
  function poll() {
    fetch('/api/r/' + encodeURIComponent(rid)).then(function (r) { return r.json(); }).then(function (r) {
      if (r.status !== 'running') return finish();
      refreshBody();
      setTimeout(poll, 2000);
    }).catch(function () { setTimeout(poll, 4000); });
  }
})();
