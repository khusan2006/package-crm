/* Yetkazish: hand corrections to a load's route before it is saved.
 *
 * ↑ / ↓ move a stop. Each stop carries a hidden input with its load row, and the
 * form posts them in the order they stand, so "Saqlash" saves exactly what is on
 * screen. The map and the km are measured by the server once the order is saved.
 *
 * The route is fetched by «Marshrut tuzish» after the page loads, so this wires
 * itself up on `route:loaded` (fired with the inserted fragment), not on page load.
 */
(function () {
  'use strict';

  function renumber(card) {
    var stops = card.querySelectorAll('[data-route-stop]');
    stops.forEach(function (li, i) {
      li.querySelector('[data-route-n]').textContent = i + 1;
    });
    var count = card.querySelector('[data-route-count]');
    if (count) { count.textContent = stops.length; }
  }

  function init(scope) {
    var form = scope.querySelector('[data-route-form]');
    if (!form || form.__route) { return; }
    form.__route = true;
    var dirty = scope.querySelector('[data-route-dirty]');

    function changed() {
      if (dirty) { dirty.hidden = false; }
      // The driver's message and links describe the order as the server drew it;
      // after a move they're out of date until saved, so they step aside.
      form.querySelectorAll('[data-route-share]').forEach(function (el) { el.hidden = true; });
      form.querySelectorAll('[data-route-share-stale]').forEach(function (el) { el.hidden = false; });
    }

    form.addEventListener('click', function (e) {
      var btn = e.target.closest('[data-move]');
      if (!btn) { return; }
      e.preventDefault();
      var li = btn.closest('[data-route-stop]');
      if (btn.getAttribute('data-move') === 'up') {
        var prev = li.previousElementSibling;
        if (!prev || !prev.hasAttribute('data-route-stop')) { return; }
        li.parentNode.insertBefore(li, prev);
      } else {
        var next = li.nextElementSibling;
        if (!next || !next.hasAttribute('data-route-stop')) { return; }
        li.parentNode.insertBefore(next, li);
      }
      renumber(li.closest('[data-route-truck]'));
      changed();
      btn.focus();
    });

    // Leaving with moves not saved loses them; say so, like any unsaved form.
    var submitting = false;
    form.addEventListener('submit', function () { submitting = true; });
    window.addEventListener('beforeunload', function (e) {
      if (dirty && !dirty.hidden && !submitting) { e.preventDefault(); e.returnValue = ''; }
    });
  }

  document.addEventListener('route:loaded', function (e) { init(e.detail || document); });
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { init(document); });
  } else { init(document); }
})();
