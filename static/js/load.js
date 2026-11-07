/* Yetkazish loads: choosing clients fast, and editing rulon / kg in the table.
 *
 * Mijoz qo'shish (data-load-pick): every client is a row with a rulon box; typing a
 * number is choosing them. The search, seller and "only chosen" filters just hide
 * rows — nothing typed is lost — and the bar keeps a running count. Keyboard: in the
 * search, ↓ or Enter jumps to the first visible rulon box; in a box, Enter goes back
 * to the search (it never submits by accident) and ↑ / ↓ move between rows.
 *
 * Yuk tarkibi (data-load-items): marks the table as changed and warns before
 * leaving with unsaved rulon / kg.
 */
(function () {
  'use strict';

  function rollsOf(input) {
    var n = parseInt(input.value.replace(/\s/g, ''), 10);
    return n > 0 ? n : 0;
  }

  function warnOnLeave(form, isDirty) {
    var submitting = false;
    form.addEventListener('submit', function () { submitting = true; });
    window.addEventListener('beforeunload', function (e) {
      if (!submitting && isDirty()) { e.preventDefault(); e.returnValue = ''; }
    });
  }

  /* ---------- Mijoz qo'shish ---------- */
  var pick = document.querySelector('[data-load-pick]');
  if (pick) {
    var search = pick.querySelector('[data-pick-search]');
    var seller = pick.querySelector('[data-pick-seller]');
    var chosenOnly = pick.querySelector('[data-pick-chosen]');
    var rows = Array.prototype.slice.call(pick.querySelectorAll('[data-pick-row]'));
    var empty = pick.querySelector('[data-pick-empty]');
    var count = pick.querySelector('[data-pick-count]');
    var total = pick.querySelector('[data-pick-rolls-total]');
    var submit = pick.querySelector('[data-pick-submit]');

    var visibleBoxes = function () {
      return rows.filter(function (r) { return !r.hidden; })
        .map(function (r) { return r.querySelector('[data-pick-rolls]'); })
        .filter(Boolean);
    };

    var filter = function () {
      var words = search.value.toLowerCase().split(/\s+/).filter(Boolean);
      var who = seller.value;
      var shown = 0;
      rows.forEach(function (r) {
        var box = r.querySelector('[data-pick-rolls]');
        var text = r.getAttribute('data-search');
        var ok = words.every(function (w) { return text.indexOf(w) !== -1; }) &&
          (!who || r.getAttribute('data-seller') === who) &&
          (!chosenOnly.checked || (box && rollsOf(box) > 0));
        r.hidden = !ok;
        if (ok) { shown++; }
      });
      empty.hidden = shown > 0;
    };

    var tally = function () {
      var n = 0, sum = 0;
      rows.forEach(function (r) {
        var box = r.querySelector('[data-pick-rolls]');
        var v = box ? rollsOf(box) : 0;
        r.classList.toggle('is-chosen', v > 0);
        if (v > 0) { n++; sum += v; }
      });
      count.textContent = n;
      total.textContent = sum;
      submit.disabled = n === 0;
    };

    search.addEventListener('input', filter);
    seller.addEventListener('change', filter);
    chosenOnly.addEventListener('change', filter);
    pick.addEventListener('input', function (e) {
      if (e.target.matches('[data-pick-rolls]')) { tally(); }
    });

    search.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown' || e.key === 'Enter') {
        e.preventDefault();
        var first = visibleBoxes()[0];
        if (first) { first.focus(); first.select(); }
      }
    });
    pick.addEventListener('keydown', function (e) {
      if (!e.target.matches('[data-pick-rolls]')) { return; }
      var boxes = visibleBoxes();
      var i = boxes.indexOf(e.target);
      if (e.key === 'Enter') {
        // Back to the search for the next client — never a surprise submit.
        e.preventDefault();
        search.focus();
        search.select();
      } else if (e.key === 'ArrowDown' && boxes[i + 1]) {
        e.preventDefault();
        boxes[i + 1].focus();
        boxes[i + 1].select();
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        if (boxes[i - 1]) { boxes[i - 1].focus(); boxes[i - 1].select(); } else { search.focus(); }
      }
    });

    warnOnLeave(pick, function () { return count.textContent !== '0'; });
    filter();
    tally();
  }

  /* ---------- Yuk tarkibi ---------- */
  var items = document.querySelector('[data-load-items]');
  if (items) {
    var dirtyNote = items.querySelector('[data-items-dirty]');
    var changed = false;
    items.addEventListener('input', function () {
      changed = true;
      if (dirtyNote) { dirtyNote.hidden = false; }
    });
    warnOnLeave(items, function () { return changed; });
  }
})();
