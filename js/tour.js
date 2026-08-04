(function () {
  'use strict';

  var STORAGE_KEY = 'sar-tour-v6';
  var PAD    = 10;   // spotlight padding around target
  var MARGIN = 14;   // tooltip margin from edge / target
  var TT_W   = 290;  // tooltip width (matches CSS)

  var STEPS = [
    {
      target: null,
      title:  'Welcome to open-sar-triad',
      body:   'Explore open SAR scenes from ICEYE, Umbra, and Capella.',
      pos:    'center',
      logo:   true,
    },
    {
      target: '#mapLegend',
      title:  'Provider Toggles',
      body:   'Show or hide ICEYE, Umbra, and Capella scenes independently.',
      pos:    'right',
    },
    {
      target: '#filters-mod',
      title:  'Filters',
      body:   'Filter by mode, orbit direction, and radar look side. Reset clears them all.',
      pos:    'right',
      before: function () { if (window.expandTray) window.expandTray('#filters-mod'); },
    },
    {
      target: '#stats-mod',
      title:  'Stats',
      body:   'Live counts by provider, plus mode breakdown for the current view.',
      pos:    'right',
      before: function () { if (window.expandTray) window.expandTray('#stats-mod'); },
    },
    {
      target: '#recent-mod',
      title:  'Recent Scenes',
      body:   'Newly ingested scenes, with latest acquisitions as a fallback.',
      pos:    'right',
      before: function () { if (window.expandTray) window.expandTray('#recent-mod'); },
    },
    {
      target: '#sel-mod',
      title:  'Download list',
      body:   'Hand-pick what to export: click footprints on the map, add the whole filter at once, then show only your list on the map.',
      pos:    'right',
      before: function () { if (window.expandTray) window.expandTray('#sel-mod'); },
    },
    {
      target: '#export-mod',
      title:  'Export & Share',
      body:   'Export your selection (or all visible scenes), generate a download script for a chosen data format, or copy a shareable state link.',
      pos:    'right',
      before: function () { if (window.expandTray) window.expandTray('#export-mod'); },
    },
    {
      target: '#aoi-toolbar',
      title:  'Area of Interest Toolbar',
      body:   'Draw an AOI, upload GeoJSON, pick a country, or clear spatial filters.',
      pos:    'right',
    },
    {
      target: '#home-control',
      title:  'Home View',
      body:   'Clear AOI/country filters and return to the opening map view.',
      pos:    'right',
    },
    {
      target: '.basemap-toggle',
      title:  'Map / Satellite Basemap',
      body:   'Switch between vector map and satellite imagery.',
      pos:    'right',
    },
    {
      target: '#timeline',
      title:  'Date Range Slider',
      body:   'Drag the handles to filter by acquisition month.',
      pos:    'top',
    },
    {
      target: '#map',
      title:  'Georeferenced Preview Drape',
      body:   'Scene previews drape on the map. Blend, hide, fullscreen, or close from the control.',
      pos:    'right',
      before: openTourScene,
    },
    {
      target: '#detail-panel',
      title:  '05 Scene Metadata',
      body:   'Review scene metadata, links, downloads, and Capella format chips.',
      pos:    'left',
      before: openTourScene,
    },
    {
      target: '.cfoot',
      title:  'GitHub & Ko-fi',
      body:   'Find the code on GitHub or support the project on Ko-fi.',
      pos:    'top',
    },
    {
      target: null,
      title:  "Scene Footprints — You're Ready!",
      body:   'Click any scene to drape its preview and inspect metadata.',
      pos:    'center',
    },
  ];

  var MOBILE_STEPS = [
    {
      target: null,
      title:  'open-sar-triad on mobile',
      body:   'Map-first on mobile. Use the bottom sheet when you need controls.',
      pos:    'center',
      logo:   true,
    },
    {
      target: '#mapLegend',
      title:  'Provider Toggles',
      body:   'Show or hide each provider.',
      pos:    'bottom',
    },
    {
      target: '#timeline',
      title:  'Acquisition Window',
      body:   'Drag handles to filter by acquisition month.',
      pos:    'top',
    },
    {
      target: '#filters-mod',
      title:  'Acquisition Filters',
      body:   'Filter by mode, orbit, and look direction.',
      pos:    'top',
      before: function () {
        document.getElementById('app').classList.remove('collapsed');
        if (window.expandTray) window.expandTray('#filters-mod');
      },
    },
    {
      target: '#stats-mod',
      title:  'Coverage',
      body:   'Visible scene counts and mode breakdown.',
      pos:    'top',
      before: function () {
        document.getElementById('app').classList.remove('collapsed');
        if (window.expandTray) window.expandTray('#stats-mod');
      },
    },
    {
      target: '#recent-mod',
      title:  'Recent Scenes',
      body:   'New scenes from refresh, or latest acquisitions.',
      pos:    'top',
      before: function () {
        document.getElementById('app').classList.remove('collapsed');
        if (window.expandTray) window.expandTray('#recent-mod');
      },
    },
    {
      target: '#export-mod',
      title:  'Export & Share',
      body:   'Export your selection or all visible scenes, generate a script for a chosen data format, or copy a share link.',
      pos:    'top',
      before: function () {
        document.getElementById('app').classList.remove('collapsed');
        if (window.expandTray) window.expandTray('#export-mod');
      },
    },
    {
      target: '#aoi-toolbar',
      title:  'AOI Tools',
      body:   'Draw, upload, pick a country, or clear AOI.',
      pos:    'top',
    },
    {
      target: '.basemap-toggle',
      title:  'Map / Satellite',
      body:   'Use SAT for optical context, MAP for a clean basemap.',
      pos:    'bottom',
    },
    {
      target: '#map',
      title:  'Preview Drape',
      body:   'Tap a scene to drape its preview on the map.',
      pos:    'top',
      before: function () {
        document.getElementById('app').classList.add('collapsed');
        openTourScene();
      },
    },
    {
      target: '#detail-panel',
      title:  'Scene Metadata',
      body:   'Review metadata, links, downloads, and Capella formats.',
      pos:    'top',
      before: function () {
        document.getElementById('app').classList.add('collapsed');
        openTourScene();
      },
    },
    {
      target: '#collapseBtn',
      title:  'Map Space',
      body:   'Collapse or expand the bottom sheet.',
      pos:    'top',
    },
    {
      target: '.cfoot',
      title:  'GitHub & Ko-fi',
      body:   'Code on GitHub. Support on Ko-fi.',
      pos:    'top',
      before: function () {
        document.getElementById('app').classList.remove('collapsed');
      },
    },
    {
      target: null,
      title:  'Ready',
      body:   'Tap a scene to preview it, or collapse the sheet and explore.',
      pos:    'center',
    },
  ];

  function openTourScene() {
    if (window.__tourSceneOpened) return;
    var detailBtn = document.querySelector('[data-detail-id]');
    var recentBtn = document.querySelector('[data-recent-id]');
    var id = detailBtn ? detailBtn.getAttribute('data-detail-id')
      : recentBtn ? recentBtn.getAttribute('data-recent-id')
      : null;
    if (id && window.showDetailById) {
      window.__tourSceneOpened = true;
      window.showDetailById(id);
    }
  }

  function isMobile() {
    return window.matchMedia && window.matchMedia('(max-width: 860px)').matches;
  }

  function steps() {
    return isMobile() ? MOBILE_STEPS : STEPS;
  }

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
    if (!rect || rect.width <= 0 || rect.height <= 0 ||
        rect.right <= 0 || rect.bottom <= 0 ||
        rect.left >= window.innerWidth || rect.top >= window.innerHeight) {
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
    if (x + w > window.innerWidth) w = window.innerWidth - x;
    if (y + h > window.innerHeight) h = window.innerHeight - y;
    if (w <= 0 || h <= 0) {
      holeRect.setAttribute('x',      '-9999');
      holeRect.setAttribute('y',      '-9999');
      holeRect.setAttribute('width',  '0');
      holeRect.setAttribute('height', '0');
      ringEl.style.display = 'none';
      return;
    }

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
    var list   = steps();
    var s      = list[i];
    var isLast = i === list.length - 1;
    var total  = list.length - 1;   // exclude welcome step from count

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
      (s.logo ? '<img class="tt-logo" src="assets/logo.svg" alt="open-sar-triad" />' : '') +
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

    // Scroll sidebar targets into view so they aren't clipped.
    // Use 'instant' so the scroll completes synchronously — smooth scroll
    // is async and getBoundingClientRect would measure the pre-scroll position.
    var el = s.target ? document.querySelector(s.target) : null;
    if (el && el.closest('.console')) {
      el.scrollIntoView({ block: 'center', behavior: 'instant' });
    }

    // Measure rect and place spotlight/tooltip after the browser has
    // processed the scroll and any before() layout changes.
    requestAnimationFrame(function () {
      var rect = el ? el.getBoundingClientRect() : null;
      setSpotlight(rect);
      placeTooltip(s, rect);
    });
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
    // Reuse the #helpBtn element already present in the HTML
    helpBtn = document.getElementById('helpBtn');
    if (!helpBtn) {
      // Fallback: create the button if it somehow isn't in the DOM
      helpBtn          = document.createElement('button');
      helpBtn.id       = 'helpBtn';
      helpBtn.title    = 'Take the UI tour';
      helpBtn.textContent = '?';
      document.body.appendChild(helpBtn);
    }
    helpBtn.onclick = start;
  }

  /* ── Init ─────────────────────────────────────────────────── */
  function init() {
    createHelpBtn();

    // If already completed, don't auto-start
    if (localStorage.getItem(STORAGE_KEY)) return;

    // Mobile needs the first screen clear and map-first. Keep the tour
    // available from the help button, but do not auto-block the interface.
    if (isMobile()) return;

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
