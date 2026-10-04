/* API reference page: theme switch, copy buttons, live figures, nav highlight.
 *
 * Kept deliberately small and dependency-free. The page is readable and correct
 * with none of this running, which is why every piece below degrades quietly
 * rather than throwing.
 */
(function () {
  'use strict';

  // ── Theme ────────────────────────────────────────────────
  // Same two-button switch as the console, minus the map layers it also swaps.
  document.querySelectorAll('[data-theme-btn]').forEach(function (b) {
    b.addEventListener('click', function () {
      var t = b.dataset.themeBtn;
      document.documentElement.setAttribute('data-theme', t);
      document.querySelectorAll('[data-theme-btn]').forEach(function (o) {
        o.setAttribute('aria-pressed', String(o.dataset.themeBtn === t));
      });
      try { localStorage.setItem('ost-theme', t); } catch (e) { /* private mode */ }
    });
  });
  try {
    var saved = localStorage.getItem('ost-theme');
    if (saved === 'paper' || saved === 'dark') {
      document.documentElement.setAttribute('data-theme', saved);
      document.querySelectorAll('[data-theme-btn]').forEach(function (o) {
        o.setAttribute('aria-pressed', String(o.dataset.themeBtn === saved));
      });
    }
  } catch (e) { /* storage blocked; the dark default stands */ }

  // ── Copy buttons ─────────────────────────────────────────
  document.querySelectorAll('.code .copy').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var pre = btn.parentElement.querySelector('pre');
      if (!pre) return;
      var done = function () {
        btn.textContent = 'Copied';
        btn.dataset.done = '1';
        setTimeout(function () {
          btn.textContent = 'Copy';
          delete btn.dataset.done;
        }, 1400);
      };
      // clipboard needs a secure context, so fall back to selecting the text
      // rather than leaving the button silently dead over plain http.
      if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(pre.textContent).then(done, function () { select(pre); });
      } else {
        select(pre);
      }
    });
  });

  function select(pre) {
    var r = document.createRange();
    r.selectNodeContents(pre);
    var sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(r);
  }

  // ── Live figures ─────────────────────────────────────────
  // The page claims the API works; this is the page proving it on itself. A
  // relative URL means it also works on a local server or a fork.
  var box = document.getElementById('live');
  var err = document.getElementById('live-err');

  fetch('api/v1/stats.json', { cache: 'no-cache' })
    .then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    })
    .then(function (s) {
      var n = function (v) { return Number(v).toLocaleString(); };
      var cells = [
        ['Scenes', n(s.total)],
        ['ICEYE', n((s.by_provider || {}).iceye || 0)],
        ['Umbra', n((s.by_provider || {}).umbra || 0)],
        ['Capella', n((s.by_provider || {}).capella || 0)],
        ['Earliest', (s.temporal_extent || [])[0] || '—'],
        ['Latest', (s.temporal_extent || [])[1] || '—']
      ];
      box.innerHTML = cells.map(function (c) {
        return '<div><span class="k"></span><span class="v"></span></div>';
      }).join('');
      // Build the text through the DOM rather than the HTML string above, so a
      // surprise in the API body can never become markup on this page.
      box.querySelectorAll('div').forEach(function (d, i) {
        d.querySelector('.k').textContent = cells[i][0];
        d.querySelector('.v').textContent = cells[i][1];
      });
      box.hidden = false;
      err.hidden = true;
    })
    .catch(function (e) {
      err.textContent = 'Could not reach the API just now (' + e.message +
                        '). The endpoints below are still correct.';
    });

  // ── Nav highlight ────────────────────────────────────────
  var links = Array.prototype.slice.call(document.querySelectorAll('.doc-nav a'));
  var sections = links
    .map(function (a) { return document.querySelector(a.getAttribute('href')); })
    .filter(Boolean);

  if ('IntersectionObserver' in window && sections.length) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (!en.isIntersecting) return;
        links.forEach(function (a) {
          a.classList.toggle('on', a.getAttribute('href') === '#' + en.target.id);
        });
      });
    }, { rootMargin: '-90px 0px -70% 0px' });
    sections.forEach(function (s) { io.observe(s); });
  }
})();
