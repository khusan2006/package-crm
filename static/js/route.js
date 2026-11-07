/* Yetkazish: hand corrections to a load's route before it is saved.
 *
 * ↑ / ↓ move a stop. Each stop carries a hidden input with its load row, and the
 * form posts them in the order they stand, so "Saqlash" saves exactly what is on
 * screen. The map and the km are measured by the server once the order is saved.
 */
(function () {
  'use strict';

  var form = document.querySelector('[data-route-form]');
  if (!form) { return; }
  var dirty = document.querySelector('[data-route-dirty]');

  function renumber(card) {
    var stops = card.querySelectorAll('[data-route-stop]');
    stops.forEach(function (li, i) {
      li.querySelector('[data-route-n]').textContent = i + 1;
    });
    var count = card.querySelector('[data-route-count]');
    if (count) { count.textContent = stops.length; }
  }

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
    var move = btn.getAttribute('data-move');
    if (move === 'up') {
      var prev = li.previousElementSibling;
      if (!prev || !prev.hasAttribute('data-route-stop')) { return; }
      li.parentNode.insertBefore(li, prev);
    } else if (move === 'down') {
      var next = li.nextElementSibling;
      if (!next || !next.hasAttribute('data-route-stop')) { return; }
      li.parentNode.insertBefore(next, li);
    }
    renumber(li.closest('[data-route-truck]'));
    changed();
    btn.focus();
  });

  // Leaving with moves not saved loses them; say so, like any unsaved form.
  window.addEventListener('beforeunload', function (e) {
    if (dirty && !dirty.hidden && !form.__submitting) { e.preventDefault(); e.returnValue = ''; }
  });
  form.addEventListener('submit', function () { form.__submitting = true; });
})();
