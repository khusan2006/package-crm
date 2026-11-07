/* Yetkazish — one load's page.
 *
 * Yuk tarkibi (data-load-items): new rows are a client picker and a rulon box;
 * «+ Yana mijoz» adds another pair, × drops one. Picking a client moves to its
 * rulon box; Enter in the last rulon box adds the next row. Nothing submits by
 * accident, and leaving with unsaved changes asks first.
 *
 * Marshrut (data-load-route): «Marshrut tuzish» fetches the route fragment and
 * shows a loading state until it is on the page and its map is drawn.
 */
(function () {
  'use strict';

  /* ---------- Yuk tarkibi ---------- */
  var items = document.querySelector('[data-load-items]');
  if (items) {
    var list = items.querySelector('[data-new-rows]');
    var tmpl = items.querySelector('[data-new-row-template]');
    var dirtyNote = items.querySelector('[data-items-dirty]');
    var changed = false;
    var submitting = false;

    var markChanged = function () {
      changed = true;
      if (dirtyNote) { dirtyNote.hidden = false; }
    };

    var focusClient = function (row) {
      // The picker turns the <select> into a text box; fall back to the select.
      var box = row.querySelector('.combobox-input') || row.querySelector('select');
      if (box) { box.focus(); }
    };

    var addRow = function () {
      var row = tmpl.content.firstElementChild.cloneNode(true);
      list.appendChild(row);
      if (window.enhanceComboboxes) { window.enhanceComboboxes(row); }
      focusClient(row);
      return row;
    };

    items.querySelector('[data-new-row-add]').addEventListener('click', addRow);

    items.addEventListener('click', function (e) {
      var remove = e.target.closest('[data-new-row-remove]');
      if (!remove) { return; }
      var row = remove.closest('[data-new-row]');
      if (list.querySelectorAll('[data-new-row]').length > 1) {
        row.remove();
      } else {
        // Keep one empty pair to type into.
        row.querySelector('select').value = '';
        var box = row.querySelector('.combobox-input');
        if (box) { box.value = ''; }
        row.querySelector('[name=new_rolls]').value = '';
      }
    });

    // A client picked: straight on to their rulon.
    items.addEventListener('change', function (e) {
      if (e.target.matches('select[name=new_client]') && e.target.value) {
        var rolls = e.target.closest('[data-new-row]').querySelector('[name=new_rolls]');
        rolls.focus();
        rolls.select();
      }
      markChanged();
    });

    items.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter' || !e.target.matches('input')) { return; }
      if (e.target.closest('.combobox')) { return; }  // the picker uses Enter to choose
      e.preventDefault();  // never a surprise submit
      if (e.target.matches('[name=new_rolls]')) {
        var row = e.target.closest('[data-new-row]');
        var next = row.nextElementSibling;
        if (next) { focusClient(next); } else { addRow(); }
      }
    });

    items.addEventListener('input', markChanged);
    items.addEventListener('submit', function () { submitting = true; });
    window.addEventListener('beforeunload', function (e) {
      if (changed && !submitting) { e.preventDefault(); e.returnValue = ''; }
    });
  }

  /* ---------- Marshrut tuzish ---------- */
  var card = document.querySelector('[data-load-route]');
  if (card) {
    var button = card.querySelector('[data-route-build]');
    var label = card.querySelector('[data-route-build-label]');
    var start = card.querySelector('[data-route-start]');
    var failed = card.querySelector('[data-route-failed]');
    var slot = card.querySelector('[data-route-slot]');

    var busy = function (on) {
      button.disabled = on;
      button.classList.toggle('is-loading', on);
      label.textContent = on ? 'Marshrut tuzilmoqda…' : 'Marshrut tuzish';
    };

    var build = function () {
      busy(true);
      failed.hidden = true;
      fetch(card.getAttribute('data-url'), {
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
        credentials: 'same-origin'
      }).then(function (r) {
        if (!r.ok) { throw new Error(r.status); }
        return r.text();
      }).then(function (html) {
        slot.innerHTML = html;
        var map = slot.querySelector('[data-geo-route]');
        var done = function () {
          busy(false);
          start.hidden = true;
        };
        if (map) {
          // Loading lasts until the map is drawn (or 20 s, if the tiles stall).
          var timer = setTimeout(done, 20000);
          map.addEventListener('route:map-ready', function () { clearTimeout(timer); done(); },
            { once: true });
        }
        document.dispatchEvent(new CustomEvent('route:loaded', { detail: slot }));
        if (!map) { done(); }
      }).catch(function () {
        busy(false);
        failed.hidden = false;
      });
    };

    button.addEventListener('click', build);
    if (card.hasAttribute('data-auto')) { build(); }
  }
})();
