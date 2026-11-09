/* Yetkazish — one load's page.
 *
 * Yuk tarkibi (data-load-items): each stop has product lines (metr, mikron, rulon,
 * narx, kg) and «+ Mahsulot» for more; new stops below are client + purpose + their
 * products («+ Mahsulot» under the row adds another), «+ Yana mijoz» adds another
 * client, × drops one. Metr / mikron boxes are greyed out unless the picked product
 * comes in them. Picking a client moves to its product, a product to its first open
 * box; Enter in the last new rulon adds the next row. Enter never
 * submits the form. Leaving with unsaved changes asks first; "unsaved" is what
 * actually differs from what was saved.
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
    var lineTmpl = items.querySelector('[data-add-line-template]');
    var addButton = items.querySelector('[data-new-row-add]');
    var dirtyNote = items.querySelector('[data-items-dirty]');
    // After a failed save the page shows what was typed — still unsaved as a whole.
    var failed = items.hasAttribute('data-unsaved');
    var submitting = false;

    // Unsaved = a rulon/kg box differs from what was saved, or a new row has a client
    // or a rulon in it. Worked out each time, so undoing a change clears it again.
    var isDirty = function () {
      if (failed) { return true; }
      var some = function (sel, test) { return Array.prototype.some.call(items.querySelectorAll(sel), test); };
      // Boxes on saved stops: changed text, a ticked "remove", another purpose.
      // Compared without spaces: money boxes are regrouped ("15 000") as the page loads.
      var plain = function (v) { return v.replace(/\s/g, ''); };
      if (some('.stops input[type=text]', function (b) { return plain(b.value) !== plain(b.defaultValue); })) { return true; }
      if (some('.stops input[type=checkbox]', function (b) { return b.checked !== b.defaultChecked; })) { return true; }
      if (some('.stops select.stop-purpose', function (s) {
        return Array.prototype.some.call(s.options, function (o) { return o.selected !== o.defaultSelected; });
      })) { return true; }
      // New stops and added products: anything chosen or typed (the purpose select
      // on a new row has a default, so it alone isn't a change).
      return some('[data-new-row], [data-add-line-row]', function (row) {
        return Array.prototype.some.call(row.querySelectorAll('select[data-combobox]'), function (s) { return s.value; }) ||
          Array.prototype.some.call(row.querySelectorAll('input[type=text]:not(.combobox-input)'), function (b) { return b.value.trim(); });
      });
    };
    var refreshDirty = function () {
      if (dirtyNote) { dirtyNote.hidden = !isDirty(); }
    };

    var unmark = function (el) {
      if (el && el.classList.contains('is-bad')) {
        el.classList.remove('is-bad');
        el.removeAttribute('aria-invalid');
      }
    };

    // Metr / mikron only for a product that comes in it: otherwise greyed out and
    // emptied (readonly, not disabled, so the posted lists stay in line).
    var syncVariants = function (scope) {
      var rows = Array.prototype.slice.call(scope.querySelectorAll('[data-product-row]'));
      if (scope.matches && scope.matches('[data-product-row]')) { rows.push(scope); }
      rows.forEach(function (row) {
        var product = row.querySelector('select[data-combobox]:not([name=new_client])');
        var opt = product && product.options[product.selectedIndex];
        row.querySelectorAll('[data-variant]').forEach(function (box) {
          var on = !!(opt && opt.value && opt.hasAttribute('data-' + box.getAttribute('data-variant')));
          box.readOnly = !on;
          box.tabIndex = on ? 0 : -1;
          box.classList.toggle('is-off', !on);
          if (!on && box.value && opt && opt.value) { box.value = ''; unmark(box); }
        });
      });
    };

    var addLine = function (holder, prefix) {
      var row = lineTmpl.content.firstElementChild.cloneNode(true);
      row.querySelectorAll('[name]').forEach(function (el) {
        el.name = el.name.replace('__PREFIX__', prefix);
      });
      holder.appendChild(row);
      if (window.enhanceComboboxes) { window.enhanceComboboxes(row); }
      syncVariants(row);
      focusClient(row);
    };

    var focusClient = function (row) {
      // The picker turns the <select> into a text box; fall back to the select.
      var box = row.querySelector('.combobox-input') || row.querySelector('select');
      if (box) { box.focus(); }
    };

    // Each new client row has a key; its extra products are posted as newline_<key>_….
    var nextKey = function () {
      var max = 0;
      list.querySelectorAll('[data-new-row]').forEach(function (r) {
        max = Math.max(max, parseInt(r.getAttribute('data-key'), 10) || 0);
      });
      return String(max + 1);
    };

    var addRow = function () {
      var row = tmpl.content.firstElementChild.cloneNode(true);
      var key = nextKey();
      row.setAttribute('data-key', key);
      row.querySelector('[name=new_key]').value = key;
      list.appendChild(row);
      syncVariants(row);
      if (window.enhanceComboboxes) { window.enhanceComboboxes(row); }
      focusClient(row);
      return row;
    };

    addButton.addEventListener('click', addRow);

    // «+ Mahsulot» on a saved stop, or under a new client: one more product row.
    items.addEventListener('click', function (e) {
      var add = e.target.closest('[data-add-line]');
      if (add) {
        var stop = add.getAttribute('data-add-line');
        addLine(items.querySelector('[data-add-lines][data-item="' + stop + '"]'), 'addline_' + stop);
        return;
      }
      var addNew = e.target.closest('[data-add-new-line]');
      if (addNew) {
        var block = addNew.closest('[data-new-row]');
        addLine(block.querySelector('[data-new-lines]'), 'newline_' + block.getAttribute('data-key'));
      }
    });

    items.addEventListener('click', function (e) {
      var remove = e.target.closest('[data-new-row-remove]');
      if (!remove) { return; }
      var lineRow = remove.closest('[data-add-line-row]');
      if (lineRow) {
        // An added product row simply goes; the stop keeps its other lines.
        var block = lineRow.closest('[data-new-row]');
        var addBtn = block ? block.querySelector('[data-add-new-line]')
          : items.querySelector('[data-add-line="' + lineRow.parentNode.getAttribute('data-item') + '"]');
        lineRow.remove();
        if (addBtn) { addBtn.focus(); }
        refreshDirty();
        return;
      }
      var row = remove.closest('[data-new-row]');
      if (list.querySelectorAll('[data-new-row]').length > 1) {
        row.remove();
        addButton.focus();  // focus stays on the form, not lost to the page
      } else {
        // Keep one empty row to type into.
        row.querySelectorAll('[data-add-line-row]').forEach(function (r) { r.remove(); });
        row.querySelectorAll('select[data-combobox]').forEach(function (s) { s.value = ''; unmark(s); });
        row.querySelectorAll('input[type=text]').forEach(function (b) { b.value = ''; unmark(b); delete b.dataset.auto; });
        syncVariants(row);
        focusClient(row);
      }
      refreshDirty();
    });

    // Picked (mouse or keys): the red mark goes, and on to the next box — a client
    // to its product, a product to its rulon.
    items.addEventListener('change', function (e) {
      var t = e.target;
      if (t.matches('select[data-combobox]')) {
        unmark(t);
        var row = t.name === 'new_client' ? t.closest('[data-new-row]') : t.closest('[data-product-row]');
        if (t.value && row) {
          if (t.name === 'new_client') {
            var productBox = row.querySelector('select[name=new_product]');
            var box = productBox && productBox.closest('.combobox') ? productBox.closest('.combobox').querySelector('.combobox-input') : productBox;
            if (box) { box.focus(); }
          } else {
            // A product: its catalogue price goes in the Narx box — unless a price was
            // typed by hand, which is never overwritten.
            var price = row.querySelector('input[data-money]');
            var opt = t.options[t.selectedIndex];
            if (price && opt && opt.dataset.price && (!price.value.trim() || price.dataset.auto)) {
              price.value = opt.dataset.price;
              price.dataset.auto = '1';
              price.dispatchEvent(new Event('input', { bubbles: true }));  // regroup "15 000"
            }
            syncVariants(row.parentNode);
            // On to metr when the product comes in it, else straight to rulon.
            var rolls = row.querySelector('input[type=text]:not(.combobox-input):not([data-money]):not([readonly])');
            if (rolls) { rolls.focus(); rolls.select(); }
          }
        }
      }
      refreshDirty();
    });

    items.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter' || !e.target.matches('input')) { return; }
      var picker = e.target.closest('.combobox');
      if (picker) {
        // The picker handles Enter on a highlighted option itself. Otherwise Enter
        // must not fall through to submitting the form: take the first match, if
        // the list shows one — typing "botir" + Enter means Botir.
        if (e.defaultPrevented) { return; }
        e.preventDefault();
        var first = picker.querySelector('.combobox-pop:not([hidden]) .combobox-option');
        if (first) { first.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true })); }
        return;
      }
      e.preventDefault();  // never a surprise submit
      if (e.target.matches('[data-new-row] [name=new_rolls]')) {
        var row = e.target.closest('[data-new-row]');
        var next = row.nextElementSibling;
        if (next) { focusClient(next); } else { addRow(); }
      }
    });

    items.addEventListener('input', function (e) {
      // A price typed by hand stays: picking another product won't replace it.
      if (e.isTrusted && e.target.dataset) { delete e.target.dataset.auto; }
      // Typing a search into the picker isn't a change yet — choosing is.
      if (!e.target.closest('.combobox')) { unmark(e.target); }
      refreshDirty();
    });
    items.addEventListener('submit', function () { submitting = true; });
    window.addEventListener('pageshow', function () { submitting = false; });
    window.addEventListener('beforeunload', function (e) {
      if (!submitting && isDirty()) { e.preventDefault(); e.returnValue = ''; }
    });
    syncVariants(items);
    refreshDirty();
  }

  /* ---------- «Boshqa kunga» dialog ---------- */
  // It arrives in the page's modal, so it is wired by delegation: the button says
  // how many clients go and waits for one; touching the date picks «Boshqa sana».
  var syncMoveDialog = function (form) {
    var n = form.querySelectorAll('input[name=move]:checked').length;
    var submit = form.querySelector('[data-move-submit]');
    submit.disabled = n === 0;
    submit.textContent = n ? n + ' ta mijozni o\'tkazish' : 'Mijozni belgilang';
    var all = form.querySelector('[data-move-all]');
    if (all) {
      all.textContent = n === form.querySelectorAll('input[name=move]').length
        ? 'Hammasini olib tashlash' : 'Hammasini belgilash';
    }
  };
  document.addEventListener('change', function (e) {
    var form = e.target.closest('[data-move-dialog]');
    if (!form) { return; }
    if (e.target.matches('[data-move-other]')) {
      form.querySelector('[data-move-other-radio]').checked = true;
    }
    syncMoveDialog(form);
  });
  document.addEventListener('focusin', function (e) {
    if (e.target.matches('[data-move-dialog] [data-move-other]')) {
      e.target.closest('[data-move-dialog]').querySelector('[data-move-other-radio]').checked = true;
    }
  });
  document.addEventListener('click', function (e) {
    var all = e.target.closest('[data-move-all]');
    if (!all) { return; }
    var form = all.closest('[data-move-dialog]');
    var boxes = form.querySelectorAll('input[name=move]');
    var every = Array.prototype.every.call(boxes, function (b) { return b.checked; });
    boxes.forEach(function (b) { b.checked = !every; });
    syncMoveDialog(form);
  });
  var initMoveDialog = function (scope) {
    var form = scope && scope.querySelector && scope.querySelector('[data-move-dialog]');
    if (form) { syncMoveDialog(form); }
  };
  document.addEventListener('modal:loaded', function (e) { initMoveDialog(e.detail); });
  // After a refused save the dialog is redrawn in place, without modal:loaded.
  new MutationObserver(function () { initMoveDialog(document.getElementById('modal-body')); })
    .observe(document.getElementById('modal-body') || document.createElement('div'), { childList: true });
  initMoveDialog(document);

  /* ---------- Marshrut tuzish ---------- */
  var card = document.querySelector('[data-load-route]');
  if (card) {
    var button = card.querySelector('[data-route-build]');
    var label = card.querySelector('[data-route-build-label]');
    var start = card.querySelector('[data-route-start]');
    var failedNote = card.querySelector('[data-route-failed]');
    var status = card.querySelector('[data-route-status]');
    var slot = card.querySelector('[data-route-slot]');
    var idle = label.textContent;

    var busy = function (on) {
      button.disabled = on;
      button.classList.toggle('is-loading', on);
      label.textContent = on ? 'Marshrut tuzilmoqda…' : idle;
      if (on && status) { status.textContent = 'Marshrut tuzilmoqda…'; }
    };

    var build = function () {
      busy(true);
      failedNote.hidden = true;
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
          if (slot.querySelector('[data-route-form]')) {
            // A route came: the start panel steps aside; focus goes to its summary.
            start.hidden = true;
            if (status) { status.textContent = 'Marshrut tayyor.'; }
            var summary = slot.querySelector('.route-summary');
            if (summary) { summary.focus(); }
          } else {
            // Only a message (no warehouse, missing locations…): keep the button,
            // so it can be tried again once that's fixed.
            idle = 'Qayta urinish';
            if (status) { status.textContent = ''; }
          }
          busy(false);
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
        idle = 'Qayta urinish';
        busy(false);
        if (status) { status.textContent = ''; }
        failedNote.hidden = false;
      });
    };

    button.addEventListener('click', build);
    // «Tavsiya etilgan tartib» on an unsaved order: drop the moves, fetch it again.
    card.addEventListener('click', function (e) {
      if (e.target.closest('[data-route-suggest]')) { build(); }
    });
    if (card.hasAttribute('data-auto')) { build(); }
  }
})();
