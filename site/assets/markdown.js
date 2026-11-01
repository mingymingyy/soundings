// A small Markdown renderer. All input is escaped first, and links only allow safe schemes.
window.renderMarkdown = (function () {
  function esc(text) {
    return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function safeUrl(url) {
    var clean = url.trim();
    if (/^(https?:|mailto:)/i.test(clean) || /^[#./]/.test(clean)) return clean;
    return /^[a-z][a-z0-9+.-]*:/i.test(clean) ? '' : clean;
  }

  function inline(raw) {
    var codes = [];
    var text = raw.replace(/`([^`]+)`/g, function (_, code) {
      codes.push('<code>' + esc(code) + '</code>');
      return '\u0000' + (codes.length - 1) + '\u0000';
    });
    text = esc(text);
    text = text.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, function (match, label, url) {
      var href = safeUrl(url.replace(/&amp;/g, '&'));
      if (!href) return label;
      var external = /^https?:/i.test(href) ? ' rel="noopener" target="_blank"' : '';
      return '<a href="' + esc(href) + '"' + external + '>' + label + '</a>';
    });
    text = text.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>').replace(/__([^_]+)__/g, '<strong>$1</strong>');
    text = text.replace(/(^|[^*])\*([^*\s][^*]*)\*/g, '$1<em>$2</em>').replace(/(^|[\s(])_([^_\s][^_]*)_/g, '$1<em>$2</em>');
    text = text.replace(/~~([^~]+)~~/g, '<del>$1</del>');
    return text.replace(/\u0000(\d+)\u0000/g, function (_, i) { return codes[Number(i)]; });
  }

  function cells(line) {
    return line.trim().replace(/^\||\|$/g, '').split('|').map(function (c) { return c.trim(); });
  }

  return function render(source) {
    var lines = String(source || '').replace(/\r\n?/g, '\n').split('\n');
    var out = [];
    var i = 0;
    while (i < lines.length) {
      var line = lines[i];
      if (!line.trim()) { i++; continue; }

      var fence = line.match(/^\s*```/);
      if (fence) {
        var code = [];
        i++;
        while (i < lines.length && !/^\s*```/.test(lines[i])) code.push(lines[i++]);
        i++;
        out.push('<pre><code>' + esc(code.join('\n')) + '</code></pre>');
        continue;
      }
      var heading = line.match(/^(#{1,4})\s+(.*)$/);
      if (heading) {
        out.push('<h' + heading[1].length + '>' + inline(heading[2]) + '</h' + heading[1].length + '>');
        i++;
        continue;
      }
      if (/^\s*(---+|\*\*\*+)\s*$/.test(line)) { out.push('<hr>'); i++; continue; }
      if (/^\s*>/.test(line)) {
        var quote = [];
        while (i < lines.length && /^\s*>/.test(lines[i])) quote.push(lines[i++].replace(/^\s*>\s?/, ''));
        out.push('<blockquote>' + render(quote.join('\n')) + '</blockquote>');
        continue;
      }
      if (line.indexOf('|') >= 0 && i + 1 < lines.length && /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/.test(lines[i + 1])) {
        var head = cells(line).map(function (c) { return '<th scope="col">' + inline(c) + '</th>'; }).join('');
        i += 2;
        var body = '';
        while (i < lines.length && lines[i].indexOf('|') >= 0 && lines[i].trim()) {
          body += '<tr>' + cells(lines[i++]).map(function (c) { return '<td>' + inline(c) + '</td>'; }).join('') + '</tr>';
        }
        out.push('<table><thead><tr>' + head + '</tr></thead><tbody>' + body + '</tbody></table>');
        continue;
      }
      var bullet = /^\s*[-*+]\s+/;
      var number = /^\s*\d+[.)]\s+/;
      if (bullet.test(line) || number.test(line)) {
        var ordered = number.test(line);
        var pattern = ordered ? number : bullet;
        var items = '';
        while (i < lines.length && pattern.test(lines[i])) {
          items += '<li>' + inline(lines[i++].replace(pattern, '')) + '</li>';
        }
        out.push((ordered ? '<ol>' : '<ul>') + items + (ordered ? '</ol>' : '</ul>'));
        continue;
      }
      var para = [];
      while (i < lines.length && lines[i].trim() && !/^(#{1,4}\s|\s*```|\s*>|\s*[-*+]\s+|\s*\d+[.)]\s+)/.test(lines[i])) para.push(lines[i++]);
      if (!para.length) para.push(lines[i++]);
      out.push('<p>' + inline(para.join(' ')) + '</p>');
    }
    return out.join('\n');
  };
})();
