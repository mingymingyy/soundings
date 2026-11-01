// Research ledger: repo entries merged with this browser's edits (IndexedDB), PDFs, export and import.
(function () {
  var TAGS = ['Rates', 'FX', 'Commodities', 'Equities'];
  var PANELS = { rates: 'Rates', fx: 'FX', commodities: 'Commodities', equities: 'Equities and volatility', banks: 'Central banks', calendar: 'Calendar' };
  var MAX_PDF = 50 * 1024 * 1024;
  var DATA_DIR = '../data/research/';
  var $ = function (id) { return document.getElementById(id); };

  var state = { repo: [], local: new Map(), notes: [], tag: '', query: '', current: null, editing: null, pendingFiles: [], kept: [], objectUrls: [], persistent: true };
  var memory = { notes: new Map(), files: new Map() };
  var dbPromise = null;

  // --- storage ------------------------------------------------------------
  function openDb() {
    if (dbPromise) return dbPromise;
    dbPromise = new Promise(function (resolve) {
      if (!window.indexedDB) return resolve(null);
      var request;
      try { request = indexedDB.open('soundings-research', 1); } catch (err) { return resolve(null); }
      request.onupgradeneeded = function () {
        var db = request.result;
        if (!db.objectStoreNames.contains('notes')) db.createObjectStore('notes', { keyPath: 'id' });
        if (!db.objectStoreNames.contains('files')) db.createObjectStore('files');
      };
      request.onerror = function () { resolve(null); };
      request.onsuccess = function () { resolve(request.result); };
    });
    return dbPromise;
  }

  function txn(store, mode, work) {
    return openDb().then(function (db) {
      if (!db) { state.persistent = false; return work(null); }
      return new Promise(function (resolve, reject) {
        var tx = db.transaction(store, mode);
        var result = work(tx.objectStore(store));
        tx.oncomplete = function () { resolve(result && 'result' in result ? result.result : result); };
        tx.onerror = tx.onabort = function () { reject(tx.error); };
      });
    });
  }

  function getLocalNotes() {
    return txn('notes', 'readonly', function (s) { return s ? s.getAll() : { result: Array.from(memory.notes.values()) }; });
  }
  function putNote(note) {
    return txn('notes', 'readwrite', function (s) { if (s) s.put(note); else memory.notes.set(note.id, note); });
  }
  function dropNote(id) {
    return txn('notes', 'readwrite', function (s) { if (s) s.delete(id); else memory.notes.delete(id); });
  }
  function putFile(key, blob) {
    return txn('files', 'readwrite', function (s) { if (s) s.put(blob, key); else memory.files.set(key, blob); });
  }
  function getFile(key) {
    return txn('files', 'readonly', function (s) { return s ? s.get(key) : { result: memory.files.get(key) }; });
  }
  function dropFile(key) {
    return txn('files', 'readwrite', function (s) { if (s) s.delete(key); else memory.files.delete(key); });
  }

  // --- helpers ------------------------------------------------------------
  function el(tag, props, children) {
    var node = document.createElement(tag);
    Object.keys(props || {}).forEach(function (key) {
      if (key === 'class') node.className = props[key];
      else if (key === 'text') node.textContent = props[key];
      else if (key.indexOf('on') === 0) node.addEventListener(key.slice(2), props[key]);
      else node.setAttribute(key, props[key]);
    });
    (children || []).forEach(function (child) { if (child) node.append(child); });
    return node;
  }
  function formatSize(bytes) {
    return bytes >= 1048576 ? (bytes / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(bytes / 1024)) + ' KB';
  }
  function formatDate(day) {
    var parts = String(day).split('-');
    if (parts.length !== 3) return String(day);
    var months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    return Number(parts[2]) + ' ' + months[Number(parts[1]) - 1] + ' ' + parts[0];
  }
  function todaySgt() {
    return new Date(Date.now() + 8 * 3600 * 1000).toISOString().slice(0, 10);
  }
  function slug(text) {
    return String(text).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 48) || 'note';
  }
  function newId(date, title) {
    return date + '-' + slug(title) + '-' + Math.random().toString(36).slice(2, 6);
  }
  function setStatus(message) { renderStatus(message); }
  function download(blob, name) {
    var url = URL.createObjectURL(blob);
    var link = el('a', { href: url, download: name });
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 2000);
  }
  function validNote(note) {
    return note && typeof note === 'object' && typeof note.id === 'string' && note.id && typeof note.title === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(String(note.date)) && TAGS.indexOf(note.tag) >= 0;
  }
  function clean(note) {
    return {
      id: note.id,
      date: note.date,
      title: note.title,
      tag: note.tag,
      panels: (note.panels || []).filter(function (p) { return PANELS[p]; }),
      body: typeof note.body === 'string' ? note.body : '',
      attachments: (note.attachments || []).map(function (a) { return { name: String(a.name), file: String(a.file), size: Number(a.size) || 0 }; }),
      updated: note.updated || null
    };
  }

  // --- model --------------------------------------------------------------
  function merge() {
    var map = new Map(state.repo.map(function (note) { return [note.id, note]; }));
    state.local.forEach(function (note, id) {
      if (note.deleted) map.delete(id); else map.set(id, note);
    });
    state.notes = Array.from(map.values()).sort(function (a, b) {
      return String(b.date).localeCompare(String(a.date)) || String(a.title).localeCompare(String(b.title));
    });
  }
  function isLocal(id) { return state.local.has(id); }
  function matches(note) {
    if (state.tag && note.tag !== state.tag) return false;
    if (!state.query) return true;
    var hay = (note.title + ' ' + note.tag + ' ' + note.body + ' ' + (note.attachments || []).map(function (a) { return a.name; }).join(' ')).toLowerCase();
    return state.query.split(/\s+/).every(function (term) { return hay.indexOf(term) >= 0; });
  }

  // --- rendering ----------------------------------------------------------
  function renderStatus(message) {
    var status = $('status');
    status.textContent = '';
    var count = state.notes.length;
    var pending = state.local.size;
    status.append(count + (count === 1 ? ' note.' : ' notes.'));
    if (pending) {
      status.append(' ');
      status.append(el('span', { class: 'pending', text: pending + (pending === 1 ? ' change' : ' changes') + ' saved only in this browser.' }));
      status.append(' Export, unzip into data/research/ and commit to keep them in the repo.');
    }
    if (!state.persistent) status.append(' This browser blocks local storage, so edits last until you close the tab. Export before leaving.');
    if (message) status.append(' ' + message);
  }

  function renderChips() {
    document.querySelectorAll('.chip').forEach(function (chip) {
      var tag = chip.dataset.tag;
      chip.setAttribute('aria-pressed', String(tag === state.tag));
      var total = tag ? state.notes.filter(function (n) { return n.tag === tag; }).length : state.notes.length;
      chip.querySelector('.count').textContent = total;
    });
  }

  function renderList() {
    var list = $('list');
    list.textContent = '';
    var shown = state.notes.filter(matches);
    if (!shown.length && state.notes.length) {
      list.append(el('li', { class: 'list-empty', text: 'No notes match this search and filter.' }));
    }
    shown.forEach(function (note) {
      var meta = el('span', { class: 'n-meta' }, [formatDate(note.date) + '  ', el('span', { class: 'badge', text: note.tag })]);
      if (isLocal(note.id)) { meta.append(' '); meta.append(el('span', { class: 'badge local', text: 'Local' })); }
      if ((note.attachments || []).length) meta.append('  ' + note.attachments.length + ' PDF');
      var button = el('button', {
        type: 'button', class: 'note-btn', 'aria-current': String(note.id === state.current),
        onclick: function () { select(note.id, true); }
      }, [el('span', { class: 'n-title', text: note.title }), meta]);
      list.append(el('li', {}, [button]));
    });
  }

  function clearObjectUrls() {
    state.objectUrls.forEach(function (url) { URL.revokeObjectURL(url); });
    state.objectUrls = [];
  }

  async function attachmentUrl(file) {
    var blob = await getFile(file);
    if (blob) {
      var url = URL.createObjectURL(blob);
      state.objectUrls.push(url);
      return url;
    }
    return DATA_DIR + file;
  }

  function renderEmpty() {
    var reader = $('reader');
    reader.textContent = '';
    if (state.notes.length) {
      reader.append(el('div', { class: 'empty-state' }, [
        el('h2', { text: 'Choose a note.' }),
        el('p', { text: 'Pick one from the list to read it and its attached PDFs.' })
      ]));
      return;
    }
    reader.append(el('div', { class: 'empty-state' }, [
      el('h2', { text: 'No research yet.' }),
      el('p', { text: 'Write your first note, attach a PDF, and link it to the panels it speaks to. It will show up beside those panels on the dashboard.' }),
      el('button', { type: 'button', class: 'btn primary', text: 'Write the first note', onclick: function () { openEditor(null); } })
    ]));
  }

  async function renderReader() {
    clearObjectUrls();
    var note = state.notes.find(function (n) { return n.id === state.current; });
    if (!note) return renderEmpty();
    var reader = $('reader');
    var meta = el('p', { class: 'r-meta' }, [formatDate(note.date), el('span', { class: 'badge', text: note.tag })]);
    if (isLocal(note.id)) meta.append(el('span', { class: 'badge local', text: 'Local, not yet in repo' }));
    var links = el('p', { class: 'r-links' });
    if ((note.panels || []).length) {
      links.append(el('span', { text: 'Linked panels' }));
      note.panels.forEach(function (panel) {
        if (PANELS[panel]) links.append(el('a', { href: '../#' + panel, text: PANELS[panel] }));
      });
    }
    var body = el('div', { class: 'md' });
    body.innerHTML = window.renderMarkdown(note.body); // renderMarkdown escapes everything it is given
    var nodes = [el('h2', { class: 'r-title', text: note.title }), meta];
    if (links.childElementCount) nodes.push(links);
    nodes.push(body);

    if ((note.attachments || []).length) {
      var pdfs = el('section', { class: 'pdfs', 'aria-label': 'PDF attachments' }, [el('h3', { text: 'Attachments' })]);
      for (var i = 0; i < note.attachments.length; i++) {
        pdfs.append(await pdfRow(note.attachments[i]));
      }
      nodes.push(pdfs);
    }
    nodes.push(el('div', { class: 'r-actions' }, [
      el('button', { type: 'button', class: 'btn', text: 'Edit', onclick: function () { openEditor(note); } })
    ]));
    reader.textContent = '';
    nodes.forEach(function (node) { reader.append(node); });
  }

  async function pdfRow(attachment) {
    var url = await attachmentUrl(attachment.file);
    var viewer = null;
    var toggle = el('button', {
      type: 'button', class: 'link-btn', text: 'Preview', 'aria-expanded': 'false',
      onclick: function () {
        if (viewer) {
          viewer.remove(); viewer = null;
          toggle.textContent = 'Preview'; toggle.setAttribute('aria-expanded', 'false');
          return;
        }
        viewer = el('iframe', { class: 'pdf-view', src: url, title: 'Preview of ' + attachment.name });
        row.after(viewer);
        toggle.textContent = 'Close preview'; toggle.setAttribute('aria-expanded', 'true');
      }
    });
    var row = el('div', { class: 'pdf-row' }, [
      el('span', {}, [attachment.name, el('span', { class: 'sz', text: formatSize(attachment.size) })]),
      el('span', { class: 'acts' }, [
        toggle,
        el('a', { href: url, target: '_blank', rel: 'noopener', text: 'Open' }),
        el('a', { href: url, download: attachment.name, text: 'Download' })
      ])
    ]);
    return row;
  }

  function renderAll(message) {
    merge();
    renderStatus(message);
    renderChips();
    renderList();
    return renderReader();
  }

  function select(id, move) {
    state.current = id;
    history.replaceState(null, '', '#' + encodeURIComponent(id));
    renderList();
    renderReader().then(function () {
      if (move) {
        $('reader').focus({ preventScroll: true });
        if (matchMedia('(max-width: 760px)').matches) $('reader').scrollIntoView({ block: 'start' });
      }
    });
  }

  // --- editor -------------------------------------------------------------
  var form = $('editor-form');
  var editor = $('editor');

  function openEditor(note, presetPanel) {
    state.editing = note;
    state.pendingFiles = [];
    state.kept = note ? (note.attachments || []).slice() : [];
    form.reset();
    $('editor-h').textContent = note ? 'Edit note' : 'New note';
    $('editor-delete').hidden = !note;
    $('form-error').hidden = true;
    form.elements.title.value = note ? note.title : '';
    form.elements.date.value = note ? note.date : todaySgt();
    form.elements.tag.value = note ? note.tag : (state.tag || guessTag(presetPanel));
    form.elements.body.value = note ? note.body : '';
    form.querySelectorAll('input[name=panels]').forEach(function (box) {
      box.checked = note ? (note.panels || []).indexOf(box.value) >= 0 : box.value === presetPanel;
    });
    updatePreview();
    renderFiles();
    editor.showModal();
    form.elements.title.focus();
  }

  function guessTag(panel) {
    return { rates: 'Rates', fx: 'FX', commodities: 'Commodities', equities: 'Equities' }[panel] || 'Rates';
  }
  function updatePreview() {
    $('preview').innerHTML = window.renderMarkdown(form.elements.body.value); // escaped by the renderer
  }

  function renderFiles() {
    var list = $('pdf-files');
    list.textContent = '';
    state.kept.forEach(function (attachment, index) {
      list.append(el('li', {}, [
        el('span', { text: attachment.name + ', ' + formatSize(attachment.size) }),
        el('button', { type: 'button', class: 'link-btn', text: 'Remove', 'aria-label': 'Remove ' + attachment.name, onclick: function () { state.kept.splice(index, 1); renderFiles(); } })
      ]));
    });
    state.pendingFiles.forEach(function (file, index) {
      list.append(el('li', {}, [
        el('span', { text: file.name + ', ' + formatSize(file.size) + ', not saved yet' }),
        el('button', { type: 'button', class: 'link-btn', text: 'Remove', 'aria-label': 'Remove ' + file.name, onclick: function () { state.pendingFiles.splice(index, 1); renderFiles(); } })
      ]));
    });
  }

  function showFormError(message) {
    var box = $('form-error');
    box.textContent = message;
    box.hidden = false;
  }

  async function addFiles(files) {
    $('form-error').hidden = true;
    for (var i = 0; i < files.length; i++) {
      var file = files[i];
      var head = new Uint8Array(await file.slice(0, 5).arrayBuffer());
      var isPdf = String.fromCharCode.apply(null, head) === '%PDF-';
      if (!isPdf) { showFormError(file.name + ' is not a PDF, so it was not attached.'); continue; }
      if (file.size > MAX_PDF) { showFormError(file.name + ' is over the 50 MB limit, so it was not attached.'); continue; }
      state.pendingFiles.push(file);
    }
    renderFiles();
  }

  async function saveEditor(event) {
    event.preventDefault();
    var title = form.elements.title.value.trim();
    var date = form.elements.date.value;
    if (!title) { showFormError('Add a title before saving.'); form.elements.title.focus(); return; }
    if (!date) { showFormError('Add a date before saving.'); form.elements.date.focus(); return; }
    var previous = state.editing;
    var id = previous ? previous.id : newId(date, title);
    var attachments = state.kept.slice();
    try {
      for (var i = 0; i < state.pendingFiles.length; i++) {
        var file = state.pendingFiles[i];
        var name = file.name.replace(/\.pdf$/i, '');
        var key = 'attachments/' + slug(id).slice(0, 40) + '-' + slug(name) + '-' + Math.random().toString(36).slice(2, 6) + '.pdf';
        await putFile(key, file);
        attachments.push({ name: file.name, file: key, size: file.size });
      }
      if (previous) {
        (previous.attachments || []).forEach(function (old) {
          if (!attachments.some(function (a) { return a.file === old.file; })) dropFile(old.file);
        });
      }
      var note = {
        id: id, date: date, title: title,
        tag: form.elements.tag.value,
        panels: Array.from(form.querySelectorAll('input[name=panels]:checked')).map(function (box) { return box.value; }),
        body: form.elements.body.value,
        attachments: attachments,
        updated: new Date().toISOString()
      };
      await putNote(note);
      state.local.set(id, note);
      state.current = id;
      history.replaceState(null, '', '#' + encodeURIComponent(id));
      editor.close();
      await renderAll('Saved.');
    } catch (err) {
      showFormError('Could not save in this browser: ' + (err && err.message ? err.message : 'storage error') + '. Export your text before closing this page.');
    }
  }

  async function deleteEditing() {
    var note = state.editing;
    if (!note) return;
    if (!confirm('Delete "' + note.title + '"? Attached PDFs saved in this browser go with it.')) return;
    (note.attachments || []).forEach(function (a) { dropFile(a.file); });
    if (state.repo.some(function (n) { return n.id === note.id; })) {
      var tomb = { id: note.id, deleted: true, updated: new Date().toISOString() };
      await putNote(tomb);
      state.local.set(note.id, tomb);
    } else {
      await dropNote(note.id);
      state.local.delete(note.id);
    }
    state.current = null;
    history.replaceState(null, '', location.pathname);
    editor.close();
    await renderAll('Deleted.');
  }

  // --- zip export and import ---------------------------------------------
  var crcTable = (function () {
    var table = new Uint32Array(256);
    for (var n = 0; n < 256; n++) {
      var c = n;
      for (var k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      table[n] = c >>> 0;
    }
    return table;
  })();
  function crc32(bytes) {
    var crc = 0xffffffff;
    for (var i = 0; i < bytes.length; i++) crc = crcTable[(crc ^ bytes[i]) & 255] ^ (crc >>> 8);
    return (crc ^ 0xffffffff) >>> 0;
  }
  function makeZip(files) {
    var encoder = new TextEncoder();
    var parts = [];
    var central = [];
    var offset = 0;
    files.forEach(function (file) {
      var name = encoder.encode(file.name);
      var crc = crc32(file.bytes);
      var local = new DataView(new ArrayBuffer(30));
      local.setUint32(0, 0x04034b50, true); local.setUint16(4, 20, true); local.setUint16(6, 0x0800, true);
      local.setUint16(8, 0, true); local.setUint16(10, 0, true); local.setUint16(12, 0x21, true);
      local.setUint32(14, crc, true); local.setUint32(18, file.bytes.length, true); local.setUint32(22, file.bytes.length, true);
      local.setUint16(26, name.length, true); local.setUint16(28, 0, true);
      parts.push(local.buffer, name, file.bytes);
      var dir = new DataView(new ArrayBuffer(46));
      dir.setUint32(0, 0x02014b50, true); dir.setUint16(4, 20, true); dir.setUint16(6, 20, true); dir.setUint16(8, 0x0800, true);
      dir.setUint16(10, 0, true); dir.setUint16(12, 0, true); dir.setUint16(14, 0x21, true);
      dir.setUint32(16, crc, true); dir.setUint32(20, file.bytes.length, true); dir.setUint32(24, file.bytes.length, true);
      dir.setUint16(28, name.length, true); dir.setUint32(42, offset, true);
      central.push(dir.buffer, name);
      offset += 30 + name.length + file.bytes.length;
    });
    var centralSize = central.reduce(function (sum, part) { return sum + part.byteLength; }, 0);
    var end = new DataView(new ArrayBuffer(22));
    end.setUint32(0, 0x06054b50, true); end.setUint16(8, files.length, true); end.setUint16(10, files.length, true);
    end.setUint32(12, centralSize, true); end.setUint32(16, offset, true);
    return new Blob(parts.concat(central, [end.buffer]), { type: 'application/zip' });
  }

  async function readZip(buffer) {
    var view = new DataView(buffer);
    var pos = buffer.byteLength - 22;
    while (pos >= 0 && view.getUint32(pos, true) !== 0x06054b50) pos--;
    if (pos < 0) throw new Error('That file is not a readable zip.');
    var count = view.getUint16(pos + 10, true);
    var cursor = view.getUint32(pos + 16, true);
    var decoder = new TextDecoder();
    var entries = {};
    for (var i = 0; i < count; i++) {
      if (view.getUint32(cursor, true) !== 0x02014b50) throw new Error('That zip is damaged.');
      var method = view.getUint16(cursor + 10, true);
      var compressed = view.getUint32(cursor + 20, true);
      var nameLen = view.getUint16(cursor + 28, true);
      var extraLen = view.getUint16(cursor + 30, true);
      var commentLen = view.getUint16(cursor + 32, true);
      var localAt = view.getUint32(cursor + 42, true);
      var name = decoder.decode(new Uint8Array(buffer, cursor + 46, nameLen));
      var dataAt = localAt + 30 + view.getUint16(localAt + 26, true) + view.getUint16(localAt + 28, true);
      var raw = new Uint8Array(buffer, dataAt, compressed);
      if (!name.endsWith('/')) {
        if (method === 0) entries[name] = new Blob([raw]);
        else if (method === 8 && 'DecompressionStream' in window) entries[name] = await new Response(new Blob([raw]).stream().pipeThrough(new DecompressionStream('deflate-raw'))).blob();
        else throw new Error('This zip uses a compression method the browser cannot read.');
      }
      cursor += 46 + nameLen + extraLen + commentLen;
    }
    return entries;
  }

  async function exportAll() {
    var encoder = new TextEncoder();
    var notes = state.notes.map(clean);
    var files = [{ name: 'entries.json', bytes: encoder.encode(JSON.stringify(notes, null, 2) + '\n') }];
    var missing = 0;
    for (var i = 0; i < notes.length; i++) {
      for (var j = 0; j < notes[i].attachments.length; j++) {
        var key = notes[i].attachments[j].file;
        var blob = await getFile(key);
        if (!blob) {
          try {
            var response = await fetch(DATA_DIR + key);
            if (response.ok) blob = await response.blob();
          } catch (err) { /* counted below */ }
        }
        if (blob) files.push({ name: key, bytes: new Uint8Array(await blob.arrayBuffer()) });
        else missing++;
      }
    }
    download(makeZip(files), 'soundings-research-' + todaySgt() + '.zip');
    renderStatus(missing ? 'Exported, but ' + missing + ' PDF could not be read and is missing from the zip.' : 'Exported ' + notes.length + (notes.length === 1 ? ' note.' : ' notes.'));
  }

  async function importFile(file) {
    try {
      var entries;
      var blobs = {};
      if (/\.json$/i.test(file.name) || file.type === 'application/json') {
        entries = JSON.parse(await file.text());
      } else {
        blobs = await readZip(await file.arrayBuffer());
        var manifest = blobs['entries.json'] || blobs['data/research/entries.json'];
        if (!manifest) throw new Error('That zip has no entries.json.');
        entries = JSON.parse(await manifest.text());
      }
      if (!Array.isArray(entries)) throw new Error('entries.json must hold a list of notes.');
      var valid = entries.filter(validNote).map(clean);
      var skipped = entries.length - valid.length;
      var pdfs = 0;
      for (var i = 0; i < valid.length; i++) {
        var note = valid[i];
        note.attachments = note.attachments.filter(function (a) { return /^attachments\/[\w.-]+\.pdf$/.test(a.file); });
        for (var j = 0; j < note.attachments.length; j++) {
          var key = note.attachments[j].file;
          var blob = blobs[key] || blobs['data/research/' + key];
          if (blob) { await putFile(key, new Blob([blob], { type: 'application/pdf' })); pdfs++; }
        }
        var same = state.repo.find(function (n) { return n.id === note.id; });
        if (same && JSON.stringify(clean(same)) === JSON.stringify(note)) {
          await dropNote(note.id);
          state.local.delete(note.id);
        } else {
          await putNote(note);
          state.local.set(note.id, note);
        }
      }
      await renderAll('Imported ' + valid.length + (valid.length === 1 ? ' note' : ' notes') + ' and ' + pdfs + ' PDF' + (pdfs === 1 ? '' : 's') + (skipped ? ', skipped ' + skipped + ' invalid' : '') + '.');
    } catch (err) {
      renderStatus('Import failed: ' + (err && err.message ? err.message : 'unreadable file') + ' Nothing was changed.');
    }
  }

  // --- wiring -------------------------------------------------------------
  $('new-note').addEventListener('click', function () { openEditor(null); });
  $('export').addEventListener('click', exportAll);
  $('import').addEventListener('click', function () { $('import-file').click(); });
  $('import-file').addEventListener('change', function (event) {
    if (event.target.files[0]) importFile(event.target.files[0]);
    event.target.value = '';
  });
  $('search').addEventListener('input', function (event) {
    state.query = event.target.value.trim().toLowerCase();
    renderList();
  });
  document.querySelectorAll('.chip').forEach(function (chip) {
    chip.addEventListener('click', function () {
      state.tag = chip.dataset.tag;
      renderChips();
      renderList();
    });
  });
  form.addEventListener('submit', saveEditor);
  form.elements.body.addEventListener('input', updatePreview);
  $('editor-cancel').addEventListener('click', function () { editor.close(); });
  $('editor-delete').addEventListener('click', deleteEditing);
  $('pdf-input').addEventListener('change', function (event) {
    addFiles(Array.from(event.target.files));
    event.target.value = '';
  });
  var drop = $('drop');
  ['dragenter', 'dragover'].forEach(function (name) {
    drop.addEventListener(name, function (event) { event.preventDefault(); drop.classList.add('over'); });
  });
  ['dragleave', 'drop'].forEach(function (name) {
    drop.addEventListener(name, function (event) { event.preventDefault(); drop.classList.remove('over'); });
  });
  drop.addEventListener('drop', function (event) { addFiles(Array.from(event.dataTransfer.files)); });

  // --- start --------------------------------------------------------------
  (async function start() {
    try {
      var response = await fetch(DATA_DIR + 'entries.json', { cache: 'no-cache' });
      if (response.ok) state.repo = (await response.json()).filter(validNote).map(clean);
    } catch (err) { /* repo file unreachable: local notes still work */ }
    var local = [];
    try { local = (await getLocalNotes()) || []; } catch (err) { state.persistent = false; }
    if (!(await openDb())) state.persistent = false;
    local.forEach(function (note) { state.local.set(note.id, note); });
    merge();

    var hash = decodeURIComponent(location.hash.slice(1));
    if (hash && state.notes.some(function (n) { return n.id === hash; })) state.current = hash;
    else if (state.notes.length && matchMedia('(min-width: 761px)').matches) state.current = state.notes[0].id;

    await renderAll();
    var params = new URLSearchParams(location.search);
    var preset = params.get('new');
    if (preset !== null) {
      history.replaceState(null, '', location.pathname);
      openEditor(null, PANELS[preset] ? preset : null);
    }
  })();
})();
