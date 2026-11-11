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
      if (some('.stops input[type=text]:not(.combobox-input)', function (b) { return plain(b.value) !== plain(b.defaultValue); })) { return true; }
      if (some('.stops select[data-combobox]', function (s) {
        return Array.prototype.some.call(s.options, function (o) { return o.selected !== o.defaultSelected; });
      })) { return true; }
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

    // The client's last product, to start the next one from: an added row with a
    // product picked, else the stop's last saved line (not one ticked to go).
    var lastLine = function (rows, saved) {
      for (var i = rows.length - 1; i >= 0; i--) {
        var product = rows[i].querySelector('select[data-combobox]');
        if (product && product.value) {
          var box = function (sel) { var b = rows[i].querySelector(sel); return b ? b.value : ''; };
          return { product: product.value, size: box('[data-variant=size]'), micron: box('[data-variant=micron]'),
                   rolls: box('.new-row-rolls'), price: box('.new-row-price') };
        }
      }
      for (var j = saved.length - 1; j >= 0; j--) {
        var tr = saved[j];
        var drop = tr.querySelector('.line-drop input');
        if (drop && drop.checked) { continue; }
        var val = function (sel) { var b = tr.querySelector(sel); return b ? b.value : ''; };
        return { product: tr.getAttribute('data-line-product'), size: val('[name^=size_]'),
                 micron: val('[name^=micron_]'), rolls: val('.pick-rolls'), price: val('.pick-price') };
      }
      return null;
    };

    // «+ Mahsulot»: a new product row — a copy of the client's last product, so a
    // second roll of the same film needs only its metr / mikron changed.
    var addLine = function (holder, prefix, from) {
      var row = lineTmpl.content.firstElementChild.cloneNode(true);
      row.querySelectorAll('[name]').forEach(function (el) {
        el.name = el.name.replace('__PREFIX__', prefix);
      });
      if (from) {
        row.querySelector('select[data-combobox]').value = from.product;
        var set = function (sel, v) { var b = row.querySelector(sel); if (b) { b.value = v || ''; } };
        set('[data-variant=size]', from.size);
        set('[data-variant=micron]', from.micron);
        set('.new-row-rolls', from.rolls);
        set('.new-row-price', from.price);
        // Copied, not typed here: picking another product still brings its price.
        if (from.price) { row.querySelector('.new-row-price').dataset.auto = '1'; }
      }
      holder.appendChild(row);
      if (window.enhanceComboboxes) { window.enhanceComboboxes(row); }
      syncVariants(row);
      var open = from && row.querySelector('[data-variant]:not([readonly])');
      if (open) { open.focus(); open.select(); } else if (from) { var r = row.querySelector('.new-row-rolls'); r.focus(); r.select(); } else { focusClient(row); }
      refreshDirty();
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
        var holder = items.querySelector('[data-add-lines][data-item="' + stop + '"]');
        var saved = holder.closest('.stop') ? holder.closest('.stop').querySelectorAll('tr[data-line-product]') : [];
        addLine(holder, 'addline_' + stop, lastLine(holder.querySelectorAll('[data-add-line-row]'), saved));
        return;
      }
      var addNew = e.target.closest('[data-add-new-line]');
      if (addNew) {
        var block = addNew.closest('[data-new-row]');
        var lines = block.querySelector('[data-new-lines]');
        addLine(lines, 'newline_' + block.getAttribute('data-key'), lastLine(lines.querySelectorAll('[data-product-row]'), []));
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
      if (e.target.matches('input.pick-kg')) { settleKg(e.target); return; }
      if (e.target.matches('[data-new-row] [name=new_rolls]')) {
        var row = e.target.closest('[data-new-row]');
        var next = row.nextElementSibling;
        if (next) { focusClient(next); } else { addRow(); }
      }
    });

    // Kg works like a calculator: weighing roll by roll, they type "50+48,5+51"
    // and the box shows the sum beside it; leaving the box (or Enter) puts the
    // sum in. The server adds up a "+" too, so a save straight from the box is safe.
    function kgSum(value) {
      var raw = value.replace(/[\s\u00a0\u202f]/g, '').replace(/,/g, '.');
      if (raw.indexOf('+') === -1) { return null; }
      var parts = raw.split('+').filter(function (p) { return p !== ''; });
      if (!parts.length) { return null; }
      var grams = 0;
      for (var i = 0; i < parts.length; i++) {
        if (!/^[0-9]+(\.[0-9]{1,3})?$/.test(parts[i])) { return null; }
        grams += Math.round(parseFloat(parts[i]) * 1000);
      }
      return String(grams / 1000);
    }
    function kgHint(input) {
      var hint = input.parentNode.querySelector('.kg-sum');
      var sum = kgSum(input.value);
      if (sum === null) { if (hint) { hint.remove(); } return; }
      if (!hint) {
        hint = document.createElement('span');
        hint.className = 'kg-sum';
        hint.setAttribute('aria-live', 'polite');
        input.after(hint);
      }
      hint.textContent = '= ' + sum.replace('.', ',') + ' kg';
      // Under the box, right edges lined up, overlapping its border a little.
      hint.style.top = (input.offsetTop + input.offsetHeight - 5) + 'px';
      hint.style.left = (input.offsetLeft + input.offsetWidth - 6) + 'px';
    }
    function settleKg(input) {
      var sum = kgSum(input.value);
      if (sum === null) { return; }
      input.value = sum;
      kgHint(input);
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
    items.addEventListener('focusout', function (e) {
      if (e.target.matches && e.target.matches('input.pick-kg')) { settleKg(e.target); }
    });

    items.addEventListener('input', function (e) {
      if (e.target.matches('input.pick-kg')) { kgHint(e.target); }
      // A price typed by hand stays: picking another product won't replace it.
      if (e.isTrusted && e.target.dataset) { delete e.target.dataset.auto; }
      // Typing a search into the picker isn't a change yet — choosing is.
      if (!e.target.closest('.combobox')) { unmark(e.target); }
      refreshDirty();
    });
    /* ---------- Saving by itself (the day page: data-autosave) ----------
       A box of a client already on the list is saved as it is left: the whole form
       is posted in the background without the new-client rows, the row says
       «Saqlandi» (or what is wrong, in red) and nothing reloads. Adding or removing
       a product changes the rows themselves, so that posts the form normally, the
       row staying open. New clients go in with «Qo'shish». */
    if (items.hasAttribute('data-autosave')) {
      var saving = null, again = null;
      var savedBox = /^(rolls|price|kg|size|micron|product|purpose)_\d+$/;
      var stopOf = function (el) { return el.closest('.lane-row') || el.closest('.stop'); };
      var note = function (stop, text, bad) {
        var n = stop && stop.querySelector('[data-autosave-note]');
        if (!n) { return; }
        n.textContent = text;
        n.classList.toggle('is-bad', !!bad);
        n.hidden = !text;
      };
      var settle = function (stop) {
        // What is on screen is what is saved now: nothing here is "unsaved" any more.
        stop.querySelectorAll('input[type=text]').forEach(function (b) { b.defaultValue = b.value; });
        stop.querySelectorAll('select').forEach(function (s) {
          Array.prototype.forEach.call(s.options, function (o) { o.defaultSelected = o.selected; });
        });
        // The row's own line: its kg added up again.
        var total = 0, missing = false;
        stop.querySelectorAll('input.pick-kg').forEach(function (b) {
          var v = parseFloat(b.value.replace(/[\s\u00a0\u202f]/g, '').replace(',', '.'));
          if (isNaN(v)) { missing = true; } else { total += v; }
        });
        var kg = stop.querySelector('.lane-kg');
        if (kg) {
          kg.innerHTML = (total ? String(Math.round(total * 1000) / 1000).replace('.', ',') + ' kg' : '—')
            + (missing ? '<span class="cell-sub route-pending">tortilmagan</span>' : '');
        }
      };
      var save = function (stop) {
        if (saving) { again = stop; return; }
        var data = new FormData(items);
        Array.from(data.keys()).forEach(function (k) {
          if (/^(new_|newline_|addline_)/.test(k)) { data.delete(k); }
        });
        data.set('action', 'items');
        note(stop, 'Saqlanmoqda…');
        // getAttribute: the form's hidden «action» field shadows form.action.
        saving = fetch(items.getAttribute('action'), {
          method: 'POST', body: data, credentials: 'same-origin',
          headers: { 'X-Requested-With': 'XMLHttpRequest' }
        }).then(function (r) {
          return r.json().catch(function () { return { ok: false, problems: ['Saqlanmadi — sahifani yangilang.'], bad: [] }; });
        }).then(function (res) {
          items.querySelectorAll('.stops .is-bad').forEach(function (b) { b.classList.remove('is-bad'); b.removeAttribute('aria-invalid'); });
          if (res.ok) {
            settle(stop);
            note(stop, res.saved ? 'Saqlandi ✓' : '');
          } else {
            (res.bad || []).forEach(function (name) {
              var box = items.querySelector('[name="' + name + '"]');
              if (box) {
                box.classList.add('is-bad');
                box.setAttribute('aria-invalid', 'true');
                var shown = box.closest('.combobox') && box.closest('.combobox').querySelector('.combobox-input');
                if (shown) { shown.classList.add('is-bad'); }
              }
            });
            note(stop, 'Saqlanmadi: ' + (res.problems || []).join('; '), true);
          }
          refreshDirty();
        }).catch(function () {
          note(stop, 'Saqlanmadi — internet? Qayta urinib ko\'ring.', true);
        }).then(function () {
          saving = null;
          if (again) { var next = again; again = null; save(next); }
        });
      };
      var reloadWith = function (stop) {
        var open = items.querySelector('[name=ochiq]');
        var row = stop && stop.closest('.lane-row');
        if (open && row) { open.value = row.id.replace('stop-', ''); }
        submitting = true;
        if (items.requestSubmit) { items.requestSubmit(); } else { items.submit(); }
      };
      items.addEventListener('change', function (e) {
        var t = e.target;
        if (!t.name || !t.closest('.stops')) { return; }
        var stop = stopOf(t);
        if (savedBox.test(t.name)) { save(stop); return; }
        if (/^drop_\d+$/.test(t.name)) { reloadWith(stop); return; }
        // A product added to a client: once it has a product and rulon, it goes in.
        var added = t.closest('[data-add-line-row]');
        if (added && t.matches('.new-row-rolls') && t.value.trim()) {
          var product = added.querySelector('select[data-combobox]');
          if (product && product.value) { reloadWith(stop); }
        }
      });
    }

    items.addEventListener('submit', function () { submitting = true; });
    window.addEventListener('pageshow', function () { submitting = false; });
    window.addEventListener('beforeunload', function (e) {
      if (!submitting && isDirty()) { e.preventDefault(); e.returnValue = ''; }
    });
    syncVariants(items);
    refreshDirty();
  }

  /* ---------- «Jo'natish» ---------- */
  // The box shows the time the page was opened; pressed later, «Jo'natish» means
  // now. So until someone changes it by hand, it is set to the current minute
  // just before sending.
  var start = document.querySelector('[data-load-start]');
  if (start) {
    var when = start.querySelector('[data-load-start-now]');
    if (when) {
      when.addEventListener('input', function () { delete when.dataset.loadStartNow; });
      start.addEventListener('submit', function () {
        if (!('loadStartNow' in when.dataset)) { return; }
        var d = new Date();
        d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
        when.value = d.toISOString().slice(0, 16);
      });
    }
  }

  /* ---------- «Boshqa kunga» dialog ---------- */
  // It arrives in the page's modal, so it is wired by delegation: the button says
  // how many clients go and waits for one; touching the date picks «Boshqa sana».
  // A client box is ticked when all its products are, half-ticked when some are; it
  // is posted (name=move) only when ticked — the whole client goes.
  var syncMoveDialog = function (form) {
    var whole = 0, part = 0;
    form.querySelectorAll('[data-move-client]').forEach(function (row) {
      var box = row.querySelector('[data-move-whole]');
      var lines = row.querySelectorAll('[data-move-line]');
      var on = row.querySelectorAll('[data-move-line]:checked').length;
      if (lines.length) {
        box.indeterminate = on > 0 && on < lines.length;
        if (on === lines.length) { box.checked = true; } else if (on < lines.length) { box.checked = false; }
      }
      if (box.checked) { whole += 1; } else if (box.indeterminate) { part += 1; }
    });
    var n = whole + part;
    var submit = form.querySelector('[data-move-submit]');
    submit.disabled = n === 0;
    submit.textContent = !n ? 'Mijozni belgilang'
      : part ? n + ' ta mijozni o\'tkazish (' + part + ' tasi qisman)'
      : n + ' ta mijozni o\'tkazish';
    var all = form.querySelector('[data-move-all]');
    if (all) {
      all.textContent = whole === form.querySelectorAll('[data-move-whole]').length
        ? 'Hammasini olib tashlash' : 'Hammasini belgilash';
    }
  };
  document.addEventListener('change', function (e) {
    var form = e.target.closest('[data-move-dialog]');
    if (!form) { return; }
    if (e.target.matches('[data-move-other]')) {
      form.querySelector('[data-move-other-radio]').checked = true;
    }
    if (e.target.matches('[data-move-whole]')) {
      // The client ticked or unticked: all its products follow.
      e.target.closest('[data-move-client]').querySelectorAll('[data-move-line]')
        .forEach(function (b) { b.checked = e.target.checked; });
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
    var boxes = form.querySelectorAll('[data-move-whole], [data-move-line]');
    var every = Array.prototype.every.call(form.querySelectorAll('[data-move-whole]'),
      function (b) { return b.checked; });
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
