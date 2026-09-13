/* Position Watch dashboard behaviour: section navbar, allocation ring,
   sector bar, sortable tables and the feedback box. Plain ES5 so it runs in
   any browser; every block is optional and returns early if its section is
   missing. */

(function () {
  var nav = document.querySelector('.topnav');
  if (!nav) return;
  var smooth = !window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var links = Array.prototype.slice.call(nav.querySelectorAll('a'));
  links.forEach(function (a) {
    a.addEventListener('click', function (ev) {
      var target = document.getElementById(a.getAttribute('href').slice(1));
      if (!target) return;
      ev.preventDefault();
      target.scrollIntoView({ behavior: smooth ? 'smooth' : 'auto', block: 'start' });
      target.focus({ preventScroll: true });
    });
  });
  function setHeight() { document.documentElement.style.setProperty('--nav-h', nav.offsetHeight + 'px'); }
  setHeight();
  window.addEventListener('resize', setHeight);
  if (!('IntersectionObserver' in window)) return;
  function mark(id) {
    links.forEach(function (a) {
      var on = a.getAttribute('href') === '#' + id;
      if (on) {
        a.setAttribute('aria-current', 'true');
        var list = nav.querySelector('ul');
        if (a.offsetLeft < list.scrollLeft || a.offsetLeft + a.offsetWidth > list.scrollLeft + list.clientWidth)
          list.scrollTo({ left: a.offsetLeft - 12, behavior: smooth ? 'smooth' : 'auto' });
      } else a.removeAttribute('aria-current');
    });
  }
  // Several sections can sit in the band at once: mark the topmost.
  var targets = links.map(function (a) { return document.getElementById(a.getAttribute('href').slice(1)); })
                     .filter(Boolean);
  var visible = {};
  var observer = new IntersectionObserver(function (entries) {
    entries.forEach(function (e) { visible[e.target.id] = e.isIntersecting; });
    var top = targets.find(function (el) { return visible[el.id]; });
    if (top) mark(top.id);
  }, { rootMargin: '-' + (nav.offsetHeight + 16) + 'px 0px -55% 0px' });
  targets.forEach(function (el) { observer.observe(el); });
})();

(function () {
  var box = document.querySelector('.allocation');
  if (!box) return;
  var svg = box.querySelector('.donut');
  var value = svg.querySelector('.donut-value');
  var label = svg.querySelector('.donut-label');
  var restValue = value.textContent, restLabel = label.textContent;
  function show(key) {
    var seg = null;
    box.querySelectorAll('[data-key]').forEach(function (el) {
      var on = el.getAttribute('data-key') === key;
      el.classList.toggle('on', on);
      if (on && el.classList.contains('seg')) seg = el;
    });
    box.classList.toggle('focusing', !!seg);
    if (seg) {
      value.textContent = seg.getAttribute('data-pct') + '%';
      label.textContent = key;
    }
  }
  function clear() {
    box.classList.remove('focusing');
    box.querySelectorAll('.on').forEach(function (el) { el.classList.remove('on'); });
    value.textContent = restValue; label.textContent = restLabel;
  }
  box.querySelectorAll('[data-key]').forEach(function (el) {
    var key = el.getAttribute('data-key');
    el.addEventListener('pointerenter', function () { show(key); });
    el.addEventListener('focus', function () { show(key); });
    el.addEventListener('pointerleave', clear);
    el.addEventListener('blur', clear);
  });
})();

(function () {
  var box = document.querySelector('.sector-chart');
  if (!box) return;
  var tip = box.querySelector('.sec-tip');
  function focusSector(key) {
    box.classList.toggle('focusing', !!key);
    box.querySelectorAll('[data-key]').forEach(function (el) {
      el.classList.toggle('on', el.getAttribute('data-key') === key);
    });
  }
  function place(seg, x) {
    var b = box.getBoundingClientRect(), s = seg.getBoundingClientRect();
    tip.textContent = seg.getAttribute('data-tip');
    tip.hidden = false;
    var left = (x == null ? s.left + s.width / 2 : x) - b.left - tip.offsetWidth / 2;
    tip.style.left = Math.max(0, Math.min(left, b.width - tip.offsetWidth)) + 'px';
  }
  function hide() { tip.hidden = true; focusSector(null); }
  box.querySelectorAll('.secpos').forEach(function (seg) {
    var key = seg.parentNode.getAttribute('data-key');
    seg.addEventListener('pointermove', function (e) { focusSector(key); place(seg, e.clientX); });
    seg.addEventListener('focus', function () { focusSector(key); place(seg); });
    seg.addEventListener('pointerleave', hide);
    seg.addEventListener('blur', hide);
  });
  box.querySelectorAll('.sectors li').forEach(function (li) {
    li.addEventListener('pointerenter', function () { focusSector(li.getAttribute('data-key')); });
    li.addEventListener('pointerleave', function () { focusSector(null); });
  });
})();

(function () {
  document.querySelectorAll('table.sortable').forEach(function (table) {
    var tbody = table.tBodies[0];
    table.querySelectorAll('th button.sort').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var th = btn.parentNode;
        var idx = Array.prototype.indexOf.call(th.parentNode.children, th);
        var numeric = btn.getAttribute('data-type') === 'num';
        var current = th.getAttribute('aria-sort');
        var dir = current ? (current === 'descending' ? 'ascending' : 'descending')
                          : (numeric ? 'descending' : 'ascending');
        table.querySelectorAll('th[aria-sort]').forEach(function (h) { h.removeAttribute('aria-sort'); });
        th.setAttribute('aria-sort', dir);
        var rows = Array.prototype.slice.call(tbody.rows);
        rows.sort(function (a, b) {
          var x = a.cells[idx].getAttribute('data-v'), y = b.cells[idx].getAttribute('data-v');
          var c;
          if (numeric) {
            var xv = parseFloat(x), yv = parseFloat(y);
            if (isNaN(xv)) xv = -Infinity;
            if (isNaN(yv)) yv = -Infinity;
            c = xv === yv ? 0 : (xv < yv ? -1 : 1);
          } else {
            c = String(x || '').localeCompare(String(y || ''));
          }
          return dir === 'ascending' ? c : -c;
        });
        rows.forEach(function (r) { tbody.appendChild(r); });
      });
    });
  });
})();

(function () {
  var form = document.getElementById('fb-form');
  if (!form) return;
  var TO = form.getAttribute('data-to'), SUBJECT = form.getAttribute('data-subject');
  var text = document.getElementById('fb-text');
  var btn = document.getElementById('fb-send');
  var status = document.getElementById('fb-status');
  var gmail = null, mcp = null;

  function say(msg, kind) { status.textContent = msg; status.className = 'fb-status ' + (kind || ''); }
  function mailto(body) {
    return 'mailto:' + TO + '?subject=' + encodeURIComponent(SUBJECT) + '&body=' + encodeURIComponent(body);
  }

  (async function init() {
    if (!(window.claude && window.claude.use)) return;
    mcp = await window.claude.use('mcp');
    if (!mcp) return;
    try {
      var list = await mcp.listTools();
      gmail = (list.servers || []).find(function (s) { return /gmail/i.test(s.server); }) || null;
    } catch (e) { gmail = null; }
    if (gmail) btn.textContent = 'Send feedback';
  })();

  var MESSAGES = {
    needs_reauth: 'Gmail needs reconnecting (claude.ai Settings \u2192 Connectors), then send again.',
    server_not_connected: 'Gmail isn\u2019t connected here. Add it in claude.ai Settings \u2192 Connectors, or use \u201cEmail it instead\u201d.',
    selection_required: 'Choose which Gmail connector to use in the prompt claude.ai showed, then send again.',
    not_in_manifest: 'Gmail access for this page was declined. Use \u201cEmail it instead\u201d.',
    blocked_by_policy: 'Your organisation blocks sending from this page. Use \u201cEmail it instead\u201d.',
    approval_required: 'Sending needs approval that pages can\u2019t request yet. Use \u201cEmail it instead\u201d.',
    not_granted: 'This view can\u2019t use Gmail. Use \u201cEmail it instead\u201d.',
    capability_disabled: 'This view can\u2019t use Gmail. Use \u201cEmail it instead\u201d.',
    server_unavailable: 'Couldn\u2019t confirm it sent. Check your Sent folder before sending again.',
    upstream_error: 'Couldn\u2019t confirm it sent. Check your Sent folder before sending again.'
  };

  form.addEventListener('submit', async function (ev) {
    ev.preventDefault();
    var note = text.value.trim();
    if (!note) { say('Write a note first.', 'warn'); text.focus(); return; }
    if (!gmail) { window.location.href = mailto(note); say('Opening your email app\u2026'); return; }
    btn.disabled = true; say('Sending\u2026');
    try {
      await mcp.callTool(gmail.server, 'send_message', {
        to: [TO], subject: SUBJECT, body: note + '\n\n\u2014 sent from Position Watch'
      });
      text.value = '';
      say('Sent. Tomorrow\u2019s 7am review will read it.', 'ok');
    } catch (err) {
      var code = err && err.code;
      if (code === 'tool_error') say('Gmail refused the message: ' + (err.message || 'no reason given') + '.', 'warn');
      else say(MESSAGES[code] || ('Didn\u2019t send (' + (code || 'unknown error') + '). Use \u201cEmail it instead\u201d.'), 'warn');
    } finally {
      btn.disabled = false;
    }
  });
})();
