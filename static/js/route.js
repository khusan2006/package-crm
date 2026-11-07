/* Yetkazish: hand corrections to the day's route before it is saved.
 *
 * ↑ / ↓ move a stop within its truck, ⇄ moves it to the other truck. Everything
 * happens in the page: each stop carries a hidden "truck:client" input, and the
 * form posts them in the order they stand, so "Saqlash" saves exactly what is on
 * screen. The kg and the load bar follow each move; the map and the km are
 * measured by the server once the plan is saved.
 */
(function () {
  'use strict';

  var form = document.querySelector('[data-route-form]');
  if (!form) { return; }
  var dirty = document.querySelector('[data-route-dirty]');

  function fmtKg(n) {
    return Math.round(n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
  }

  function refresh(card) {
    var truck = card.getAttribute('data-route-truck');
    var capacity = parseFloat(card.getAttribute('data-capacity')) || 0;
    var stops = card.querySelectorAll('[data-route-stop]');
    var kg = 0;
    stops.forEach(function (li, i) {
      li.querySelector('[data-route-n]').textContent = i + 1;
      var input = li.querySelector('input[name=stop]');
      input.value = truck + ':' + input.getAttribute('data-client');
      kg += parseFloat(li.getAttribute('data-kg')) || 0;
      // Every truck has a ⇄ on every stop; the one for its own truck is hidden.
      li.querySelectorAll('[data-move-to]').forEach(function (b) {
        b.hidden = b.getAttribute('data-move-to') === truck;
      });
    });
    card.querySelector('[data-route-count]').textContent = stops.length;
    card.querySelector('[data-route-kg]').textContent = fmtKg(kg);
    var pct = capacity ? Math.min(100, Math.round(kg / capacity * 100)) : 0;
    card.querySelector('[data-route-bar]').style.width = pct + '%';
    var over = kg > capacity;
    card.querySelector('[data-route-load]').classList.toggle('is-over', over);
    card.querySelector('[data-route-over]').hidden = !over;
  }

  function changed() {
    if (dirty) { dirty.hidden = false; }
  }

  form.addEventListener('click', function (e) {
    var btn = e.target.closest('[data-move], [data-move-to]');
    if (!btn) { return; }
    e.preventDefault();
    var li = btn.closest('[data-route-stop]');
    var card = li.closest('[data-route-truck]');
    var move = btn.getAttribute('data-move');

    if (move === 'up') {
      var prev = li.previousElementSibling;
      if (prev && prev.hasAttribute('data-route-stop')) { li.parentNode.insertBefore(li, prev); }
    } else if (move === 'down') {
      var next = li.nextElementSibling;
      if (next && next.hasAttribute('data-route-stop')) { li.parentNode.insertBefore(next, li); }
    } else {
      var target = form.querySelector('[data-route-truck="' + btn.getAttribute('data-move-to') + '"]');
      if (!target) { return; }
      // Joins the other truck at the end, just before its drive back.
      var end = target.querySelector('[data-route-end]');
      end.parentNode.insertBefore(li, end);
      refresh(target);
    }
    refresh(card);
    changed();
    // The pressed button may now be hidden (it named the truck the stop moved to).
    if (btn.hidden) { var other = li.querySelector('[data-move-to]:not([hidden])'); if (other) { other.focus(); } }
    else { btn.focus(); }
  });

  // Leaving with moves not saved loses them; say so, like any unsaved form.
  window.addEventListener('beforeunload', function (e) {
    if (dirty && !dirty.hidden && !form.__submitting) { e.preventDefault(); e.returnValue = ''; }
  });
  form.addEventListener('submit', function () { form.__submitting = true; });
})();
