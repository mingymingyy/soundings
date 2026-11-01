// Runs before first paint: theme, plus the hooks shared by every page.
(function () {
  var root = document.documentElement;
  var saved = null;
  try { saved = localStorage.getItem('soundings-theme'); } catch (err) { /* storage blocked */ }
  root.dataset.theme = saved === 'light' ? 'light' : 'dark';
  root.classList.add('js');

  function setTheme(theme) {
    root.dataset.theme = theme;
    try { localStorage.setItem('soundings-theme', theme); } catch (err) { /* storage blocked */ }
    var btn = document.getElementById('theme');
    if (btn) {
      btn.setAttribute('aria-pressed', String(theme === 'light'));
      btn.setAttribute('aria-label', theme === 'light' ? 'Switch to dark theme' : 'Switch to light theme');
    }
    var meta = document.querySelector('meta[name=theme-color]');
    if (meta) meta.content = theme === 'light' ? '#f4f1ea' : '#0b1017';
  }

  document.addEventListener('DOMContentLoaded', function () {
    setTheme(root.dataset.theme);
    var theme = document.getElementById('theme');
    if (theme) theme.addEventListener('click', function () { setTheme(root.dataset.theme === 'dark' ? 'light' : 'dark'); });

    var menu = document.getElementById('menu');
    var open = document.getElementById('menu-open');
    var close = document.getElementById('menu-close');
    if (menu && open && close && menu.showModal) {
      open.addEventListener('click', function () { menu.showModal(); });
      close.addEventListener('click', function () { menu.close(); });
      menu.addEventListener('click', function (event) {
        if (event.target.closest('a')) menu.close();
      });
    }

    var bar = document.getElementById('progress');
    if (bar) {
      var queued = false;
      var paint = function () {
        queued = false;
        var height = document.documentElement.scrollHeight - innerHeight;
        bar.style.transform = 'scaleY(' + (height > 0 ? Math.min(1, scrollY / height) : 0) + ')';
      };
      addEventListener('scroll', function () {
        if (!queued) { queued = true; requestAnimationFrame(paint); }
      }, { passive: true });
      paint();
    }
  });
})();
