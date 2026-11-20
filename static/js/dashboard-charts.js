/* Asosiy sahifa grafiklari — ApexCharts.
 *
 * The server renders every chart as plain SVG first, so the page reads fine before
 * (or without) this script. Once ApexCharts has loaded, each chart host is swapped for
 * an interactive one fed from the `dash-charts` JSON block — the same numbers, nothing
 * recomputed here. Clicking a point / slice keeps the old behaviour: it drills the
 * page into that day, month, aging bucket or payment method.
 *
 * Colours come from the app's CSS variables and are re-read whenever the theme
 * toggles, so dark mode follows without a reload. */
(function () {
  'use strict';

  var dataEl = document.getElementById('dash-charts');
  if (!dataEl || typeof window.ApexCharts !== 'function') { return; }
  var DATA = JSON.parse(dataEl.textContent);
  var live = [];

  // --- helpers -------------------------------------------------------------

  var canvas = document.createElement('canvas');
  canvas.width = canvas.height = 1;
  var ctx2d = canvas.getContext('2d', { willReadFrequently: true });

  /* Any CSS colour expression (var(), color-mix(), …) resolved against `scope`, as
     an rgb() string ApexCharts can parse. */
  function cssColor(expr, scope) {
    var probe = document.createElement('span');
    probe.style.display = 'none';
    probe.style.color = expr;
    (scope || document.body).appendChild(probe);
    var computed = getComputedStyle(probe).color;
    probe.remove();
    ctx2d.clearRect(0, 0, 1, 1);
    ctx2d.fillStyle = '#000';
    ctx2d.fillStyle = computed;
    ctx2d.fillRect(0, 0, 1, 1);
    var d = ctx2d.getImageData(0, 0, 1, 1).data;
    return 'rgb(' + d[0] + ',' + d[1] + ',' + d[2] + ')';
  }

  function palette() {
    return {
      dark: document.documentElement.getAttribute('data-theme') === 'dark',
      text: cssColor('var(--text)'),
      faint: cssColor('var(--text-faint)'),
      border: cssColor('var(--border)'),
      surface: cssColor('var(--surface)'),
      accent: cssColor('var(--accent)'),
      success: cssColor('var(--success)')
    };
  }

  function money(v) {
    return Math.round(Number(v) || 0).toLocaleString('ru-RU');
  }

  function short(v) {
    v = Number(v) || 0;
    var a = Math.abs(v);
    if (a >= 1e9) { return (v / 1e9).toFixed(1) + ' mlrd'; }
    if (a >= 1e6) { return (v / 1e6).toFixed(1) + ' mln'; }
    if (a >= 1e3) { return (v / 1e3).toFixed(0) + ' ming'; }
    return v.toFixed(0);
  }

  /* Same as the template's {% qs_replace %}: change some params, keep the rest,
     always reset pagination. */
  function drill(params) {
    var url = new URL(window.location.href);
    Object.keys(params).forEach(function (k) {
      if (params[k] === '' || params[k] == null) { url.searchParams.delete(k); }
      else { url.searchParams.set(k, params[k]); }
    });
    url.searchParams.delete('page');
    window.location.href = url.toString();
  }

  function base(p) {
    return {
      fontFamily: 'inherit',
      background: 'transparent',
      foreColor: p.faint,
      toolbar: { show: false },
      zoom: { enabled: false },
      animations: { enabled: true, easing: 'easeinout', speed: 550 }
    };
  }

  function mount(host, options) {
    var el = document.createElement('div');
    host.replaceChildren(el);
    var chart = new ApexCharts(el, options);
    chart.render();
    live.push(chart);
  }

  // --- area charts (daily / monthly) ---------------------------------------

  function areaChart(host, p) {
    var rows = DATA[host.dataset.chart] || [];
    // The server drew "no data" text instead of a chart: leave it be. The flag lets
    // a theme rebuild find its own chart again after the SVG is gone.
    if (!rows.length || !(host.dataset.apex || host.querySelector('.line-chart'))) { return; }
    host.dataset.apex = '1';
    var hovered = -1;
    var opts = {
      chart: Object.assign(base(p), {
        type: 'area',
        height: 260,
        events: {
          mouseMove: function (e, c, cfg) { hovered = cfg.dataPointIndex; },
          mouseLeave: function () { hovered = -1; },
          click: function () {
            var r = rows[hovered];
            if (r) { drill({ dan: r.dan, gacha: r.gacha }); }
          }
        }
      }),
      series: [
        { name: host.dataset.aLabel, data: rows.map(function (r) { return r.a; }) },
        { name: host.dataset.bLabel, data: rows.map(function (r) { return r.b; }) }
      ],
      colors: [p.accent, p.success],
      stroke: { curve: 'smooth', width: 2.5, lineCap: 'round' },
      fill: {
        type: 'gradient',
        gradient: { shadeIntensity: 1, opacityFrom: p.dark ? 0.32 : 0.26, opacityTo: 0, stops: [0, 92] }
      },
      dataLabels: { enabled: false },
      markers: { size: 0, strokeColors: p.surface, strokeWidth: 2, hover: { size: 5 } },
      grid: {
        borderColor: p.border,
        strokeDashArray: 4,
        xaxis: { lines: { show: false } },
        padding: { left: 6, right: 10, top: -6 }
      },
      xaxis: {
        categories: rows.map(function (r) { return r.label; }),
        tickAmount: Math.min(rows.length - 1, 12),
        axisBorder: { show: false },
        axisTicks: { show: false },
        crosshairs: { stroke: { color: p.border, dashArray: 3 } },
        tooltip: { enabled: false },
        labels: { rotate: 0, hideOverlappingLabels: true, style: { fontSize: '11px' } }
      },
      yaxis: {
        min: 0,
        forceNiceScale: true,
        labels: { formatter: short, style: { fontSize: '11px' } }
      },
      legend: { show: false },
      tooltip: {
        shared: true,
        intersect: false,
        theme: p.dark ? 'dark' : 'light',
        y: { formatter: function (v) { return money(v) + " so'm"; } }
      },
      theme: { mode: p.dark ? 'dark' : 'light' }
    };
    mount(host, opts);
  }

  // --- donuts (debt aging / payment methods) -------------------------------

  function donutChart(host, p) {
    var segs = (DATA[host.dataset.donut] || []).filter(function (s) { return s.value > 0; });
    if (!segs.length) { return; }
    var param = host.dataset.param;
    var total = segs.reduce(function (sum, s) { return sum + s.value; }, 0);
    var opts = {
      chart: Object.assign(base(p), {
        type: 'donut',
        height: 196,
        width: 196,
        events: {
          dataPointSelection: function (e, c, cfg) {
            var s = segs[cfg.dataPointIndex];
            if (s && s.key) { drill(Object.fromEntries([[param, s.key]])); }
          }
        }
      }),
      series: segs.map(function (s) { return s.value; }),
      labels: segs.map(function (s) { return s.label; }),
      colors: segs.map(function (s) { return cssColor(s.color); }),
      stroke: { width: 3, colors: [p.surface] },
      legend: { show: false },
      dataLabels: { enabled: false },
      states: { hover: { filter: { type: 'darken', value: 0.88 } }, active: { filter: { type: 'none' } } },
      plotOptions: {
        pie: {
          expandOnClick: false,
          donut: {
            size: '74%',
            labels: {
              show: true,
              name: { show: true, fontSize: '11px', color: p.faint, offsetY: 18 },
              value: {
                show: true, fontSize: '19px', fontWeight: 700, color: p.text, offsetY: -12,
                formatter: function (v) { return short(v); }
              },
              total: {
                show: true, showAlways: true, label: "so'm", fontSize: '11px', color: p.faint,
                formatter: function () { return short(total); }
              }
            }
          }
        }
      },
      tooltip: {
        theme: p.dark ? 'dark' : 'light',
        y: { formatter: function (v) { return money(v) + " so'm"; } }
      },
      theme: { mode: p.dark ? 'dark' : 'light' }
    };
    host.classList.add('donut-wrap--apex');
    mount(host, opts);
  }

  // --- KPI sparklines -------------------------------------------------------

  function sparkline(svg, p) {
    var key = svg.dataset.spark;
    var sparks = DATA.sparks || {};
    var values = sparks[key];
    if (!values || !values.length) { return; }
    var kpi = svg.closest('.kpi, .dash-pos');
    var color = cssColor('var(--kpi, var(--accent))', kpi);
    var unit = svg.dataset.unit || "so'm";
    var host = document.createElement('div');
    // Keep the placeholder's own classes (e.g. dash-pos-spark positions it).
    host.className = (svg.getAttribute('class') || 'kpi-spark') + ' kpi-spark--apex';
    svg.replaceWith(host);
    var el = document.createElement('div');
    host.appendChild(el);
    var chart = new ApexCharts(el, {
      chart: Object.assign(base(p), { type: 'area', height: 44, sparkline: { enabled: true } }),
      series: [{ name: '', data: values }],
      colors: [color],
      stroke: { curve: 'smooth', width: 2 },
      fill: { type: 'gradient', gradient: { shadeIntensity: 1, opacityFrom: 0.35, opacityTo: 0, stops: [0, 100] } },
      markers: { size: 0, strokeColors: p.surface, strokeWidth: 2, hover: { size: 4 } },
      tooltip: {
        theme: p.dark ? 'dark' : 'light',
        x: { show: false },
        marker: { show: false },
        fixed: { enabled: false },
        y: {
          title: { formatter: function () { return ''; } },
          formatter: function (v, o) {
            var label = (sparks.labels || [])[o.dataPointIndex] || '';
            return label + ': ' + money(v) + (unit ? ' ' + unit : '');
          }
        }
      }
    });
    chart.render();
    live.push(chart);
  }

  // --- wiring ---------------------------------------------------------------

  /* Hosts are found once; on a theme change only the charts are rebuilt, inside
     the same hosts. */
  var areaHosts = Array.prototype.slice.call(document.querySelectorAll('[data-chart]'));
  var donutHosts = Array.prototype.slice.call(document.querySelectorAll('[data-donut]'));
  var sparkSvgs = Array.prototype.slice.call(document.querySelectorAll('svg[data-spark]'));
  var sparkHosts = [];

  function renderAll() {
    var p = palette();
    areaHosts.forEach(function (h) { areaChart(h, p); });
    donutHosts.forEach(function (h) { donutChart(h, p); });
    if (sparkSvgs.length) {
      sparkSvgs.forEach(function (svg) {
        var placeholder = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        placeholder.dataset.spark = svg.dataset.spark;
        placeholder.setAttribute('class', svg.getAttribute('class') || 'kpi-spark');
        if (svg.dataset.unit != null) { placeholder.dataset.unit = svg.dataset.unit; }
        sparkHosts.push({ kpi: svg.closest('.kpi, .dash-pos'), template: placeholder });
        sparkline(svg, p);
      });
      sparkSvgs = [];
    } else {
      sparkHosts.forEach(function (s) {
        var old = s.kpi.querySelector('.kpi-spark--apex');
        var fresh = s.template.cloneNode(true);
        old.replaceWith(fresh);
        sparkline(fresh, p);
      });
    }
  }

  renderAll();

  new MutationObserver(function () {
    live.forEach(function (c) { c.destroy(); });
    live = [];
    renderAll();
  }).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
})();
