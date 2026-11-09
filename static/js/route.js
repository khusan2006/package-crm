/* Yetkazish: hand corrections to a load's route before it is saved.
 *
 * ↑ / ↓ move a stop; with two trucks, ⇄ moves it to the other one (to the end of
 * its list). Each stop carries hidden inputs with its load row and its truck, and
 * the form posts them in the order they stand, so "Saqlash" saves exactly what is
 * on screen. A truck may be emptied (everyone goes with the other driver): it then
 * says so, and stays on the page so a stop can be moved back. The map and the km are measured by the server once the order is saved.
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
      // Nothing above the first stop or below the last: those buttons are off.
      li.querySelector('[data-move="up"]').disabled = i === 0;
      li.querySelector('[data-move="down"]').disabled = i === stops.length - 1;
    });
    var empty = card.querySelector('[data-route-empty]');
    if (empty) { empty.hidden = stops.length > 0; }
    var count = card.querySelector('[data-route-count]');
    if (count) { count.textContent = stops.length; }
    // The truck's kg follows its stops, so a move that overloads it shows at once.
    var load = card.querySelector('[data-route-load]');
    if (load) {
      var kg = 0;
      stops.forEach(function (li) { kg += parseFloat(li.getAttribute('data-kg')) || 0; });
      var capacity = parseFloat(load.getAttribute('data-capacity')) || 0;
      load.querySelector('[data-route-kg]').textContent =
        Math.round(kg).toString().replace(/\B(?=(\d{3})+(?!\d))/g, '\u00a0');
      load.querySelector('[data-route-load-bar]').style.width =
        (capacity ? Math.min(100, Math.round(kg / capacity * 100)) : 0) + '%';
      var over = capacity > 0 && kg > capacity;
      load.classList.toggle('is-over', over);
      var note = card.querySelector('[data-route-over]');
      if (note) { note.hidden = !over; }
    }
  }

  function init(scope) {
    var form = scope.querySelector('[data-route-form]');
    if (!form || form.__route) { return; }
    form.__route = true;
    var dirty = scope.querySelector('[data-route-dirty]');

    function changed() {
      if (dirty) { dirty.hidden = false; }
      // The way back to the system's order, now that there is something to undo.
      var suggest = scope.querySelector('[data-route-suggest]');
      if (suggest) { suggest.hidden = false; }
      // The driver's message and links describe the order as the server drew it;
      // after a move they're out of date until saved, so they step aside.
      form.querySelectorAll('[data-route-share]').forEach(function (el) { el.hidden = true; });
      form.querySelectorAll('[data-route-share-stale]').forEach(function (el) { el.hidden = false; });
    }

    // ⇄: the stop goes to the end of the other truck's list.
    form.addEventListener('click', function (e) {
      var swap = e.target.closest('[data-move-truck]');
      if (!swap) { return; }
      e.preventDefault();
      var li = swap.closest('[data-route-stop]');
      var cards = Array.prototype.slice.call(form.querySelectorAll('[data-route-truck]'));
      var from = li.closest('[data-route-truck]');
      var to = cards[(cards.indexOf(from) + 1) % cards.length];
      if (to === from) { return; }
      to.querySelector('.route-stops').insertBefore(li, to.querySelector('[data-route-end]'));
      li.querySelector('[data-route-group]').value = cards.indexOf(to);
      renumber(from);
      renumber(to);
      changed();
      swap.focus();
    });

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
      // Keep focus on the moved stop's button, or its other one at the very end.
      (btn.disabled ? li.querySelector('[data-move]:not([disabled])') : btn).focus();
    });

    // Leaving with moves not saved loses them; say so, like any unsaved form. Saving
    // the order, or «Qayta hisoblash» (which asks on its own), is not leaving.
    var submitting = false;
    form.addEventListener('submit', function () { submitting = true; });
    var reset = document.getElementById('route-reset');
    if (reset) { reset.addEventListener('submit', function () { submitting = true; }); }
    window.addEventListener('pageshow', function () { submitting = false; });
    window.addEventListener('beforeunload', function (e) {
      // A re-fetched route replaces this form; its warning goes with it.
      if (form.isConnected && dirty && !dirty.hidden && !submitting) { e.preventDefault(); e.returnValue = ''; }
    });
  }

  document.addEventListener('route:loaded', function (e) { init(e.detail || document); });
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { init(document); });
  } else { init(document); }
})();
