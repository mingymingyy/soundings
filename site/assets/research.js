// Research library: repo entries merged with this browser's edits (IndexedDB), a report view with
// a PDF.js viewer, and export and import.
(function () {
  var TAGS = ['Rates', 'FX', 'Commodities', 'Equities'];
  var PANELS = { rates: 'Rates', fx: 'FX', commodities: 'Commodities', equities: 'Equities and volatility', banks: 'Central banks', calendar: 'Calendar' };
  var MAX_PDF = 50 * 1024 * 1024;
  var DATA_DIR = '../data/research/';
  var PDFJS = '../assets/vendor/pdf.min.mjs';
  var PDFJS_WORKER = new URL('../assets/vendor/pdf.worker.min.mjs', location.href).href;
  var $ = function (id) { return document.getElementById(id); };

  var state = { repo: [], covers: {}, local: new Map(), notes: [], issues: {}, tag: '', query: '', editing: null, pendingFiles: [], kept: [], objectUrls: [], persistent: true, viewer: null };
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
      request.onblocked = function () { resolve(null); };
      request.onsuccess = function () { resolve(request.result); };
    });
    return dbPromise;
  }

  // Each operation has a stored path and an in-memory path, for browsers that block IndexedDB.
  function stored(store, mode, viaDb, viaMemory) {
    return openDb().then(function (db) {
      if (!db) { state.persistent = false; return viaMemory(); }
      return new Promise(function (resolve, reject) {
        var tx = db.transaction(store, mode);
        var request = viaDb(tx.objectStore(store));
        tx.oncomplete = function () { resolve(request ? request.result : undefined); };
        tx.onerror = tx.onabort = function () { reject(tx.error); };
      });
    });
  }

  function getLocalNotes() {
    return stored('notes', 'readonly', function (s) { return s.getAll(); }, function () { return Array.from(memory.notes.values()); });
  }
  function putNote(note) {
    return stored('notes', 'readwrite', function (s) { s.put(note); }, function () { memory.notes.set(note.id, note); });
  }
  function dropNote(id) {
    return stored('notes', 'readwrite', function (s) { s.delete(id); }, function () { memory.notes.delete(id); });
  }
  function putFile(key, blob) {
    return stored('files', 'readwrite', function (s) { s.put(blob, key); }, function () { memory.files.set(key, blob); });
  }
  function getFile(key) {
    return stored('files', 'readonly', function (s) { return s.get(key); }, function () { return memory.files.get(key); });
  }
  function dropFile(key) {
    return stored('files', 'readwrite', function (s) { s.delete(key); }, function () { memory.files.delete(key); });
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
      summary: typeof note.summary === 'string' ? note.summary : '',
      panels: (note.panels || []).filter(function (p) { return PANELS[p]; }),
      body: typeof note.body === 'string' ? note.body : '',
      attachments: (note.attachments || []).map(function (a) { return { name: String(a.name), file: String(a.file), size: Number(a.size) || 0 }; }),
      updated: note.updated || null
    };
  }
  function summaryOf(note) {
    if (note.summary) return note.summary;
    var first = String(note.body || '').split(/\n\s*\n/).find(function (block) { return block.trim() && !/^(#|\||>|-|\*|\d+\.)/.test(block.trim()); });
    return first ? first.replace(/[*_`]/g, '').trim().slice(0, 320) : '';
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
    // Issue numbers run from the oldest report.
    state.issues = {};
    state.notes.slice().reverse().forEach(function (note, index) { state.issues[note.id] = index + 1; });
  }
  function isLocal(id) { return state.local.has(id); }
  function matches(note) {
    if (state.tag && note.tag !== state.tag) return false;
    if (!state.query) return true;
    var hay = (note.title + ' ' + note.tag + ' ' + (note.summary || '') + ' ' + note.body + ' ' + (note.attachments || []).map(function (a) { return a.name; }).join(' ')).toLowerCase();
    return state.query.split(/\s+/).every(function (term) { return hay.indexOf(term) >= 0; });
  }
  function currentId() {
    return decodeURIComponent(location.hash.slice(1));
  }
  function currentNote() {
    var id = currentId();
    return state.notes.find(function (n) { return n.id === id; }) || null;
  }

  // --- library ------------------------------------------------------------
  function renderStatus(message) {
    var status = $('status');
    status.textContent = '';
    var pending = state.local.size;
    status.append('Notes and PDFs, tied to the dashboard panels they speak to.');
    if (pending) {
      status.append(' ');
      status.append(el('span', { class: 'pending', text: pending + (pending === 1 ? ' change' : ' changes') + ' saved only in this browser.' }));
      status.append(' Export, unzip into data/research/ and commit to keep them in the repo.');
    }
    if (!state.persistent) status.append(' This browser blocks local storage, so edits last until you close the tab. Export before leaving.');
    if (message) status.append(' ' + message);
  }

  function cover(note, eager) {
    var box = el('div', { class: 'latest-cover' });
    var info = state.covers[note.id];
    if (info) {
      box.append(el('img', {
        src: DATA_DIR + info.file, alt: '', width: info.w, height: info.h,
        loading: eager ? 'eager' : 'lazy', decoding: 'async'
      }));
    } else {
      box.append(el('div', { class: 'text-cover' }, [
        el('span', { text: note.title }),
        el('small', { text: formatDate(note.date) })
      ]));
    }
    return box;
  }

  function renderLatest() {
    var box = $('latest');
    box.textContent = '';
    var note = state.notes[0];
    if (!note) {
      box.append(el('div', { class: 'empty-state' }, [
        el('h2', { text: 'No research yet.' }),
        el('p', { text: 'Write your first note, attach a PDF, and link it to the panels it speaks to. It will show up beside those panels on the dashboard.' }),
        el('button', { type: 'button', class: 'btn primary', text: 'Write the first note', onclick: function () { openEditor(null); } })
      ]));
      return;
    }
    var meta = el('p', { class: 'meta-line' }, [
      el('span', { text: 'Published ' + formatDate(note.date) }),
      el('span', { class: 'badge', text: note.tag })
    ]);
    if (isLocal(note.id)) meta.append(el('span', { class: 'badge local', text: 'Local' }));
    var summary = summaryOf(note);
    box.append(
      cover(note, true),
      el('div', { class: 'latest-copy' }, [
        el('div', { class: 'latest-title' }, [
          el('p', { class: 'kicker', text: 'Latest issue, No. ' + state.issues[note.id] }),
          el('h2', { text: note.title }),
          meta
        ]),
        summary ? el('p', { class: 'latest-summary', text: summary }) : null,
        el('a', { class: 'btn primary', href: '#' + encodeURIComponent(note.id), text: 'Read' })
      ])
    );
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
    var filtered = Boolean(state.tag || state.query);
    $('result-count').textContent = shown.length + ' of ' + state.notes.length + (state.notes.length === 1 ? ' report' : ' reports');
    $('reset').hidden = !filtered;
    if (!shown.length) {
      list.append(el('li', { class: 'list-empty', text: state.notes.length ? 'No reports match this search and filter.' : 'Reports you add will be listed here.' }));
    }
    shown.forEach(function (note) {
      var title = el('span', { class: 'n-title', text: note.title });
      if (isLocal(note.id)) title.append(el('small', { text: 'Saved only in this browser' }));
      list.append(el('li', {}, [
        el('a', { class: 'note-btn', href: '#' + encodeURIComponent(note.id) }, [
          el('span', { class: 'n-no', text: String(state.issues[note.id]) }),
          title,
          el('span', { class: 'n-date', text: formatDate(note.date) }),
          el('span', { class: 'n-tag' }, [el('span', { class: 'badge', text: note.tag })])
        ])
      ]));
    });
  }

  // --- report view --------------------------------------------------------
  function clearObjectUrls() {
    state.objectUrls.forEach(function (url) { URL.revokeObjectURL(url); });
    state.objectUrls = [];
  }

  async function attachmentSource(file) {
    var blob = await getFile(file);
    if (blob) {
      var url = URL.createObjectURL(blob);
      state.objectUrls.push(url);
      return { url: url, blob: blob };
    }
    return { url: DATA_DIR + file, blob: null };
  }

  function closeViewer() {
    if (state.viewer) {
      state.viewer.dispose();
      state.viewer = null;
    }
  }

  function viewerError(host, source, message) {
    host.textContent = '';
    host.append(el('div', { class: 'viewer-error', role: 'alert' }, [
      el('strong', { text: 'This PDF could not be shown here.' }),
      el('span', { text: message + ' ' }),
      el('a', { href: source.url, target: '_blank', rel: 'noopener', text: 'Open it in a new tab' })
    ]));
  }

  // Pages render only when near the screen, and are released again once far away.
  function mountViewer(host, counter, source) {
    var disposed = false;
    var doc = null;
    var pages = [];
    var observers = [];
    var ratio = Math.SQRT2;

    host.textContent = '';
    var status = el('p', { role: 'status', text: 'Loading PDF' });
    host.append(el('div', { class: 'viewer-state' }, [status, el('div', { class: 'skeleton sheet' })]));

    function targetWidth() {
      return host.clientWidth || 600;
    }

    async function draw(entry) {
      if (entry.rendered || entry.task || disposed) return;
      var page = await doc.getPage(entry.number);
      if (disposed) return;
      var base = page.getViewport({ scale: 1 });
      var density = Math.min(window.devicePixelRatio || 1, 2);
      // Render above screen density on small screens, so a pinch zoom stays sharp.
      var pixels = Math.max(targetWidth() * density, 900);
      var viewport = page.getViewport({ scale: pixels / base.width });
      var canvas = el('canvas', { width: Math.floor(viewport.width), height: Math.floor(viewport.height), role: 'img', 'aria-label': 'Page ' + entry.number + ' of ' + doc.numPages });
      entry.task = page.render({ canvasContext: canvas.getContext('2d'), viewport: viewport });
      try {
        await entry.task.promise;
        if (disposed) return;
        entry.sheet.textContent = '';
        entry.sheet.append(canvas);
        entry.sheet.classList.remove('is-loading');
        entry.rendered = true;
      } catch (err) {
        if (err && err.name !== 'RenderingCancelledException') entry.sheet.classList.remove('is-loading');
      } finally {
        entry.task = null;
      }
    }

    function release(entry) {
      if (entry.task) { entry.task.cancel(); entry.task = null; }
      if (entry.rendered) {
        entry.sheet.textContent = '';
        entry.sheet.classList.add('is-loading');
        entry.rendered = false;
      }
    }

    (async function start() {
      try {
        var pdfjs = await import(PDFJS);
        if (disposed) return;
        pdfjs.GlobalWorkerOptions.workerSrc = PDFJS_WORKER;
        var options = { isEvalSupported: false, disableFontFace: false };
        if (source.blob) options.data = new Uint8Array(await source.blob.arrayBuffer());
        else options.url = source.url;
        var task = pdfjs.getDocument(options);
        task.onProgress = function (progress) {
          if (progress.total) status.textContent = 'Loading PDF, ' + Math.min(100, Math.round((progress.loaded / progress.total) * 100)) + '%';
        };
        doc = await task.promise;
        if (disposed) { doc.destroy(); return; }
        var first = await doc.getPage(1);
        var view = first.getViewport({ scale: 1 });
        ratio = view.height / view.width;
        host.textContent = '';
        var stack = el('div', { class: 'pdf-pages' });
        host.append(stack);
        counter.textContent = 'Page 1 of ' + doc.numPages;
        var nearby = new IntersectionObserver(function (entries) {
          entries.forEach(function (io) {
            var entry = pages[Number(io.target.dataset.index)];
            if (io.isIntersecting) draw(entry); else release(entry);
          });
        }, { rootMargin: '150% 0px' });
        var reading = new IntersectionObserver(function (entries) {
          entries.forEach(function (io) {
            if (io.isIntersecting) counter.textContent = 'Page ' + io.target.dataset.number + ' of ' + doc.numPages;
          });
        }, { rootMargin: '-45% 0px -50% 0px' });
        observers.push(nearby, reading);
        for (var i = 1; i <= doc.numPages; i++) {
          var sheet = el('div', { class: 'pdf-sheet is-loading' });
          sheet.style.aspectRatio = '1 / ' + ratio.toFixed(4);
          var figure = el('figure', { class: 'pdf-page', 'data-index': i - 1, 'data-number': i }, [
            sheet,
            el('figcaption', { text: 'Page ' + i + ' of ' + doc.numPages })
          ]);
          stack.append(figure);
          pages.push({ number: i, sheet: sheet, rendered: false, task: null });
          nearby.observe(figure);
          reading.observe(figure);
        }
      } catch (err) {
        if (!disposed) viewerError(host, source, 'The viewer did not load or the file is unreadable.');
      }
    })();

    return {
      dispose: function () {
        disposed = true;
        observers.forEach(function (io) { io.disconnect(); });
        pages.forEach(function (entry) { if (entry.task) entry.task.cancel(); });
        if (doc) doc.destroy();
      }
    };
  }

  function related(note) {
    return state.notes
      .filter(function (other) { return other.id !== note.id; })
      .map(function (other) {
        var shared = (other.panels || []).filter(function (p) { return (note.panels || []).indexOf(p) >= 0; }).length;
        return { note: other, score: (other.tag === note.tag ? 2 : 0) + shared };
      })
      .filter(function (item) { return item.score > 0; })
      .sort(function (a, b) { return b.score - a.score || String(b.note.date).localeCompare(String(a.note.date)); })
      .slice(0, 3)
      .map(function (item) { return item.note; });
  }

  function issueLink(note, label, css) {
    if (!note) {
      return el('span', { class: 'btn ' + css, 'aria-disabled': 'true' }, [el('small', { text: label }), el('span', { text: 'None' })]);
    }
    return el('a', { class: 'btn ' + css, href: '#' + encodeURIComponent(note.id) }, [el('small', { text: label }), el('span', { text: note.title })]);
  }

  async function renderReport(note, moveFocus) {
    closeViewer();
    clearObjectUrls();
    var host = $('report');
    host.textContent = '';
    var index = state.notes.indexOf(note);
    var older = state.notes[index + 1] || null;
    var newer = state.notes[index - 1] || null;

    var meta = el('p', { class: 'meta-line' }, [
      el('span', { text: 'No. ' + state.issues[note.id] }),
      el('span', { text: 'Published ' + formatDate(note.date) }),
      el('span', { class: 'badge', text: note.tag })
    ]);
    if (isLocal(note.id)) meta.append(el('span', { class: 'badge local', text: 'Local, not yet in repo' }));

    var links = el('p', { class: 'report-links' });
    if ((note.panels || []).length) {
      links.append(el('span', { text: 'Linked panels' }));
      note.panels.forEach(function (panel) {
        if (PANELS[panel]) links.append(el('a', { href: '../#' + panel, text: PANELS[panel] }));
      });
    }

    var heading = el('h1', { tabindex: '-1', text: note.title });
    var attachments = note.attachments || [];
    var hasPdf = attachments.length > 0;
    var grid = el('div', { class: 'report-grid' + (hasPdf ? '' : ' no-pdf') });

    if (hasPdf) {
      var viewerHost = el('div', {});
      var counter = el('span', { class: 'page-count', role: 'status', text: 'Opening' });
      var openLink = el('a', { target: '_blank', rel: 'noopener', text: 'Open' });
      var downloadLink = el('a', { text: 'Download' });
      var nameLabel = el('span', {});
      var show = async function (attachment) {
        closeViewer();
        var source = await attachmentSource(attachment.file);
        openLink.href = source.url;
        downloadLink.href = source.url;
        downloadLink.setAttribute('download', attachment.name);
        nameLabel.textContent = attachment.name + ', ' + formatSize(attachment.size);
        state.viewer = mountViewer(viewerHost, counter, source);
      };
      var picker = null;
      if (attachments.length > 1) {
        picker = el('div', { class: 'attachment-list', role: 'group', 'aria-label': 'Attachments' });
        attachments.forEach(function (attachment) {
          picker.append(el('button', { type: 'button', class: 'btn', text: attachment.name, onclick: function () { show(attachment); } }));
        });
      }
      grid.append(el('section', { class: 'viewer', 'aria-label': 'PDF reader' }, [
        el('div', { class: 'viewer-bar' }, [counter, el('span', { class: 'acts' }, [openLink, downloadLink])]),
        picker,
        viewerHost
      ]));
      show(attachments[0]);
    }

    if (note.body) {
      var body = el('div', { class: 'md' });
      body.innerHTML = window.renderMarkdown(note.body); // renderMarkdown escapes everything it is given
      if (hasPdf) {
        var notes = el('details', { class: 'notes' }, [el('summary', { text: 'Key points' }), body]);
        if (matchMedia('(min-width: 1101px)').matches) notes.open = true;
        grid.append(notes);
      } else {
        grid.append(body);
      }
    }

    var relatedNotes = related(note);
    var relatedRow = el('section', { class: 'related', 'aria-labelledby': 'related-h' }, [el('h2', { id: 'related-h', text: 'Related reports' })]);
    if (relatedNotes.length) {
      var row = el('div', { class: 'related-row' });
      relatedNotes.forEach(function (other) {
        row.append(el('a', { class: 'related-card', href: '#' + encodeURIComponent(other.id) }, [
          cover(other, false),
          el('span', {}, [el('strong', { text: other.title }), el('small', { text: formatDate(other.date) + ', ' + other.tag })])
        ]));
      });
      relatedRow.append(row);
    } else {
      relatedRow.append(el('p', { class: 'related-none', text: 'No other reports share this tag or its panels yet.' }));
    }

    host.append(
      el('a', { class: 'back', href: '#', text: 'All research' }),
      heading,
      meta,
      links.childElementCount ? links : null,
      el('div', { class: 'report-actions' }, [
        el('button', { type: 'button', class: 'btn', text: 'Edit', onclick: function () { openEditor(note); } })
      ]),
      grid,
      el('nav', { class: 'issue-nav', 'aria-label': 'Move between issues' }, [
        issueLink(older, 'Previous issue', 'prev'),
        issueLink(newer, 'Next issue', 'next')
      ]),
      relatedRow
    );
    if (moveFocus) heading.focus({ preventScroll: true });
  }

  function route(moveFocus) {
    var note = currentNote();
    var library = $('library');
    var report = $('report');
    if (note) {
      library.hidden = true;
      report.hidden = false;
      document.title = note.title + ' | Soundings';
      renderReport(note, moveFocus);
      scrollTo(0, 0);
    } else {
      closeViewer();
      clearObjectUrls();
      report.textContent = '';
      report.hidden = true;
      library.hidden = false;
      document.title = 'Research | Soundings';
      if (moveFocus) $('lib-h').focus({ preventScroll: true });
    }
  }

  function renderAll(message) {
    merge();
    renderStatus(message);
    renderLatest();
    renderChips();
    renderList();
    route(false);
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
    form.elements.summary.value = note ? (note.summary || '') : '';
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
        summary: form.elements.summary.value.trim(),
        panels: Array.from(form.querySelectorAll('input[name=panels]:checked')).map(function (box) { return box.value; }),
        body: form.elements.body.value,
        attachments: attachments,
        updated: new Date().toISOString()
      };
      await putNote(note);
      state.local.set(id, note);
      editor.close();
      if (currentId() !== id) location.hash = encodeURIComponent(id);
      renderAll('Saved.');
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
    editor.close();
    history.replaceState(null, '', location.pathname);
    renderAll('Deleted.');
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
      renderAll('Imported ' + valid.length + (valid.length === 1 ? ' note' : ' notes') + ' and ' + pdfs + ' PDF' + (pdfs === 1 ? '' : 's') + (skipped ? ', skipped ' + skipped + ' invalid' : '') + '.');
    } catch (err) {
      renderStatus('Import failed: ' + (err && err.message ? err.message : 'unreadable file') + ' Nothing was changed.');
    }
  }

  // --- wiring -------------------------------------------------------------
  function resetFilters() {
    state.tag = '';
    state.query = '';
    $('search').value = '';
    renderChips();
    renderList();
    $('search').focus();
  }

  $('new-note').addEventListener('click', function () { openEditor(null); });
  $('export').addEventListener('click', exportAll);
  $('import').addEventListener('click', function () { $('import-file').click(); });
  $('reset').addEventListener('click', resetFilters);
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
  addEventListener('hashchange', function () { route(true); });

  // --- start --------------------------------------------------------------
  (async function start() {
    try {
      var response = await fetch(DATA_DIR + 'entries.json', { cache: 'no-cache' });
      if (!response.ok) throw new Error(String(response.status));
      var repo = (await response.json()).filter(validNote);
      repo.forEach(function (note) { if (note.cover) state.covers[note.id] = note.cover; });
      state.repo = repo.map(clean);
    } catch (err) {
      state.loadFailed = true; // local notes still work, and the status line says what happened
    }
    var local = [];
    try { local = (await getLocalNotes()) || []; } catch (err) { state.persistent = false; }
    local.forEach(function (note) { state.local.set(note.id, note); });

    renderAll(state.loadFailed ? 'Published reports could not be loaded, so only notes in this browser are shown.' : '');
    var params = new URLSearchParams(location.search);
    var preset = params.get('new');
    if (preset !== null) {
      history.replaceState(null, '', location.pathname);
      openEditor(null, PANELS[preset] ? preset : null);
    }
  })();
})();
