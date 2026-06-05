(function () {
  'use strict';

  var STORAGE_KEY = 'sar-tour-v2';
  var PAD    = 10;   // spotlight padding around target
  var MARGIN = 14;   // tooltip margin from edge / target
  var TT_W   = 290;  // tooltip width (matches CSS)

  var STEPS = [
    {
      target: null,
      title:  'Welcome to open-sar-triad',
      body:   'A browser-based discovery portal for open SAR satellite imagery from ICEYE, Umbra, and Capella. This quick tour walks you through every control — or skip it if you already know the ropes.',
      pos:    'center',
    },
    {
      target: '#provider-pills',
      title:  'Provider Toggles',
      body:   'Click any coloured pill to show or hide scenes from that provider. ICEYE (green), Umbra (blue), and Capella (orange) are toggled independently.',
      pos:    'bottom',
    },
    {
      target: '#filters-tray',
      title:  'Filters',
      body:   'All scene filters live here in one collapsible tray. <b>Sensor Mode</b> narrows by acquisition type (Spotlight, Stripmap…). <b>Orbit</b> picks ascending or descending satellite passes. <b>Look</b> picks left or right radar illumination side. <b>Reset All Filters</b> clears every filter at once.',
      pos:    'right',
      before: function () {
        var t = document.getElementById('filters-tray');
        if (t) t.classList.remove('tray-collapsed');
      },
    },
    {
      target: '#stats-tray',
      title:  'Stats',
      body:   'A live bar chart shows visible scene counts per provider, and stacked bars break down sensor modes across all three providers. Both update instantly as you adjust any filter. Click the tray header to collapse when you need more sidebar space.',
      pos:    'right',
      before: function () {
        var t = document.getElementById('stats-tray');
        if (t) t.classList.remove('tray-collapsed');
      },
    },
    {
      target: '#export-section',
      title:  'Export & Share',
      body:   '<b>Export as STAC Collection</b> downloads visible scenes as a GeoJSON file for QGIS or Python. <b>Generate Download Script</b> produces a ready-to-run bash script that saves scene assets into <code>iceye/</code>, <code>umbra/</code>, and <code>capella/</code> folders. <b>Copy Share Link</b> copies a URL that restores every active filter and the map view — send it to a colleague and they land on exactly the same view.',
      pos:    'right',
    },
    {
      target: '#aoi-toolbar',
      title:  'Area of Interest Toolbar',
      body:   'Draw a bounding box or polygon to spatially filter scenes. Upload a custom GeoJSON boundary, or use the globe icon to select a country. The × clears the active AOI.',
      pos:    'bottom',
    },
    {
      target: '#bottom-bar',
      title:  'Date Range Slider',
      body:   'Drag either handle to restrict scenes to a specific time window. The scene count on the right updates as you narrow the range.',
      pos:    'top',
    },
    {
      target: null,
      title:  "Scene Footprints — You're Ready!",
      body:   'Each coloured polygon on the map is a SAR scene. Click one for a quick popup with date and mode, then hit "Details" to open the right-side panel with a thumbnail preview, full metadata, and download links.',
      pos:    'center',
    },
  ];

  var currentStep = 0;
  var overlayEl   = null;
  var tooltipEl   = null;
  var ringEl      = null;
  var helpBtn     = null;
  var svgRect     = null;   // the dark rect inside the SVG
  var holeRect    = null;   // the transparent hole rect
  var resizeTimer = null;

  /* ── Build / destroy DOM ──────────────────────────────────── */
  function buildDOM() {
    // Full-screen SVG overlay for the darkened background + spotlight hole
    overlayEl = document.createElement('div');
    overlayEl.id = 'tour-overlay';

    var ns = 'http://www.w3.org/2000/svg';
    var svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('style', 'position:fixed;inset:0;width:100%;height:100%;pointer-events:none');

    var defs = document.createElementNS(ns, 'defs');
    var mask = document.createElementNS(ns, 'mask');
    mask.setAttribute('id', 'tour-mask');

    var maskFill = document.createElementNS(ns, 'rect');
    maskFill.setAttribute('width',  '200%');
    maskFill.setAttribute('height', '200%');
    maskFill.setAttribute('fill', 'white');

    holeRect = document.createElementNS(ns, 'rect');
    holeRect.setAttribute('id',     'tour-hole');
    holeRect.setAttribute('rx',     '7');
    holeRect.setAttribute('ry',     '7');
    holeRect.setAttribute('fill',   'black');
    holeRect.setAttribute('x',      '-9999');
    holeRect.setAttribute('y',      '-9999');
    holeRect.setAttribute('width',  '0');
    holeRect.setAttribute('height', '0');

    mask.appendChild(maskFill);
    mask.appendChild(holeRect);
    defs.appendChild(mask);

    svgRect = document.createElementNS(ns, 'rect');
    svgRect.setAttribute('width',        '100%');
    svgRect.setAttribute('height',       '100%');
    svgRect.setAttribute('fill',         '#0d1117');
    svgRect.setAttribute('fill-opacity', '0.82');
    svgRect.setAttribute('mask',         'url(#tour-mask)');

    svg.appendChild(defs);
    svg.appendChild(svgRect);
    overlayEl.appendChild(svg);

    // Spotlight highlight ring (purely decorative green border)
    ringEl = document.createElement('div');
    ringEl.id = 'tour-ring';
    ringEl.style.display = 'none';

    // Tooltip card
    tooltipEl = document.createElement('div');
    tooltipEl.id = 'tour-tooltip';

    document.body.appendChild(overlayEl);
    document.body.appendChild(ringEl);
    document.body.appendChild(tooltipEl);

    window.addEventListener('resize', onResize);
  }

  function destroyDOM() {
    window.removeEventListener('resize', onResize);
    overlayEl && overlayEl.remove();
    tooltipEl && tooltipEl.remove();
    ringEl    && ringEl.remove();
    overlayEl = tooltipEl = ringEl = svgRect = holeRect = null;
  }

  /* ── Spotlight ────────────────────────────────────────────── */
  function setSpotlight(rect) {
    if (!rect) {
      holeRect.setAttribute('x',      '-9999');
      holeRect.setAttribute('y',      '-9999');
      holeRect.setAttribute('width',  '0');
      holeRect.setAttribute('height', '0');
      ringEl.style.display = 'none';
      return;
    }

    var x = Math.max(0, rect.left - PAD);
    var y = Math.max(0, rect.top  - PAD);
    var w = rect.width  + PAD * 2;
    var h = rect.height + PAD * 2;

    holeRect.setAttribute('x',      x);
    holeRect.setAttribute('y',      y);
    holeRect.setAttribute('width',  w);
    holeRect.setAttribute('height', h);

    ringEl.style.display = '';
    ringEl.style.left    = x + 'px';
    ringEl.style.top     = y + 'px';
    ringEl.style.width   = w + 'px';
    ringEl.style.height  = h + 'px';
  }

  /* ── Tooltip placement ────────────────────────────────────── */
  function placeTooltip(s, rect) {
    var vw  = window.innerWidth;
    var vh  = window.innerHeight;
    var ttH = tooltipEl.offsetHeight || 220;

    // Reset any previous transform
    tooltipEl.style.transform = '';

    if (!rect || s.pos === 'center') {
      tooltipEl.style.left      = '50%';
      tooltipEl.style.top       = '50%';
      tooltipEl.style.transform = 'translate(-50%,-50%)';
      return;
    }

    var top, left;

    switch (s.pos) {
      case 'bottom':
        top  = rect.bottom + MARGIN;
        left = rect.left + rect.width / 2 - TT_W / 2;
        break;
      case 'top':
        top  = rect.top - ttH - MARGIN;
        left = rect.left + rect.width / 2 - TT_W / 2;
        break;
      case 'right':
        top  = rect.top + rect.height / 2 - ttH / 2;
        left = rect.right + MARGIN;
        break;
      case 'left':
        top  = rect.top + rect.height / 2 - ttH / 2;
        left = rect.left - TT_W - MARGIN;
        break;
      default:
        top  = rect.bottom + MARGIN;
        left = rect.left;
    }

    // Clamp within viewport with margin
    left = Math.max(MARGIN, Math.min(left, vw - TT_W - MARGIN));
    top  = Math.max(MARGIN, Math.min(top,  vh - ttH  - MARGIN));

    tooltipEl.style.left = left + 'px';
    tooltipEl.style.top  = top  + 'px';
  }

  /* ── Render a step ────────────────────────────────────────── */
  function renderStep(i) {
    var s      = STEPS[i];
    var isLast = i === STEPS.length - 1;
    var total  = STEPS.length - 1;   // exclude welcome step from count

    var counterHtml = i > 0
      ? '<span class="tt-counter">' + i + '&thinsp;/&thinsp;' + total + '</span>'
      : '<span></span>';

    var backHtml = i > 0
      ? '<button class="tt-btn tt-back" id="tt-back">← Back</button>'
      : '<span></span>';

    var fwdLabel = isLast ? 'Done ✓' : i === 0 ? 'Start Tour →' : 'Next →';

    tooltipEl.innerHTML =
      '<div class="tt-topbar">' +
        counterHtml +
        '<button class="tt-skip" id="tt-skip">Skip tour</button>' +
      '</div>' +
      '<h3 class="tt-heading">' + s.title + '</h3>' +
      '<p class="tt-body">'    + s.body  + '</p>' +
      '<div class="tt-actions">' +
        backHtml +
        '<button class="tt-btn tt-fwd" id="tt-fwd">' + fwdLabel + '</button>' +
      '</div>';

    // Re-trigger fade animation
    tooltipEl.style.animation = 'none';
    tooltipEl.offsetHeight;  // force reflow
    tooltipEl.style.animation = '';

    document.getElementById('tt-skip').onclick = finish;
    if (i > 0) {
      document.getElementById('tt-back').onclick = function () { go(i - 1); };
    }
    document.getElementById('tt-fwd').onclick = isLast ? finish : function () { go(i + 1); };

    // Run optional pre-step hook (e.g. expand a collapsed tray)
    if (s.before) s.before();

    // Scroll sidebar targets into view so they aren't clipped
    var el   = s.target ? document.querySelector(s.target) : null;
    if (el && el.closest('#sidebar')) {
      el.scrollIntoView({ block: 'center', behavior: 'smooth' });
    }

    var rect = el ? el.getBoundingClientRect() : null;
    setSpotlight(rect);
    requestAnimationFrame(function () { placeTooltip(s, rect); });
  }

  function go(i) {
    currentStep = i;
    renderStep(i);
  }

  /* ── Start / Finish ───────────────────────────────────────── */
  function start() {
    if (overlayEl) return;
    currentStep = 0;
    buildDOM();
    renderStep(0);
    helpBtn && helpBtn.classList.add('tour-running');
  }

  function finish() {
    localStorage.setItem(STORAGE_KEY, '1');
    destroyDOM();
    helpBtn && helpBtn.classList.remove('tour-running');
  }

  /* ── Resize handler ───────────────────────────────────────── */
  function onResize() {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () { renderStep(currentStep); }, 150);
  }

  /* ── Help (?) button ──────────────────────────────────────── */
  function createHelpBtn() {
    helpBtn          = document.createElement('button');
    helpBtn.id       = 'tour-help-btn';
    helpBtn.title    = 'Take the UI tour';
    helpBtn.textContent = '?';
    helpBtn.onclick  = start;
    document.body.appendChild(helpBtn);
  }

  /* ── Init ─────────────────────────────────────────────────── */
  function init() {
    createHelpBtn();

    // If already completed, don't auto-start
    if (localStorage.getItem(STORAGE_KEY)) return;

    // Shared links carry state in the URL hash — skip tour so the
    // recipient lands directly on the filtered view, not the tour.
    if (window.location.hash.length > 1) return;

    var loading = document.getElementById('loading');
    if (!loading || loading.classList.contains('hidden')) {
      setTimeout(start, 500);
      return;
    }

    // Wait for the app's loading overlay to finish
    var obs = new MutationObserver(function () {
      if (loading.classList.contains('hidden')) {
        obs.disconnect();
        setTimeout(start, 700);
      }
    });
    obs.observe(loading, { attributes: true, attributeFilter: ['class'] });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
}());
