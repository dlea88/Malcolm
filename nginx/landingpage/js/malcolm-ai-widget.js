(function() {
  'use strict';

  var STATUS_URL = '/mapi/ai/v1/status';
  var INTENT_URL = '/mapi/ai/v1/ask';
  var COMMANDS_URL = '/mapi/ai/v1/commands';
  var WHEEL_URL = '/assets/img/malcolm_wheel.svg';
  var HOST_ID = 'malcolm-ai-widget-host';
  var STORAGE_KEY = 'maw-chat-history';
  var STATE_KEY = 'maw-panel-open';
  var COLLAPSE_KEY = 'maw-collapsed';
  var POS_KEY = 'maw-position';
  var SIZE_KEY = 'maw-size';
  var DRAG_THRESHOLD = 5;
  var DEFAULT_WIDTH = 380;
  var DEFAULT_HEIGHT = 480;
  var MIN_WIDTH = 280;
  var MIN_HEIGHT = 300;
  var MAX_WIDTH = 700;
  var MAX_HEIGHT = 800;
  var DASH_UUID_RE = /\/dashboards\/.*#\/view\/([0-9a-f-]{36})/;

  var HISTORY_CAP = 200;

  // --- Chat history persistence via localStorage (survives tab close) ---
  function loadHistory() {
    try { return JSON.parse(localStorage.getItem(STORAGE_KEY)) || []; }
    catch (e) { return []; }
  }
  function saveHistory(messages) {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(messages)); }
    catch (e) {}
  }
  function appendHistory(entry) {
    var msgs = loadHistory();
    msgs.push(entry);
    if (msgs.length > HISTORY_CAP) msgs = msgs.slice(-HISTORY_CAP);
    saveHistory(msgs);
  }
  function clearHistory() {
    try { localStorage.removeItem(STORAGE_KEY); }
    catch (e) {}
  }
  function exportHistory() {
    var msgs = loadHistory();
    var blob = new Blob([JSON.stringify(msgs, null, 2)], {type: 'application/json'});
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = 'malcolm-ai-chat-' + new Date().toISOString().slice(0, 10) + '.json';
    a.click();
    URL.revokeObjectURL(url);
  }
  function savePanelState(open) {
    try { sessionStorage.setItem(STATE_KEY, open ? '1' : '0'); }
    catch (e) {}
  }
  function loadPanelState() {
    try { return sessionStorage.getItem(STATE_KEY) === '1'; }
    catch (e) { return false; }
  }
  function saveCollapseState(collapsed) {
    try { sessionStorage.setItem(COLLAPSE_KEY, collapsed ? '1' : '0'); }
    catch (e) {}
  }
  function loadCollapseState() {
    try { return sessionStorage.getItem(COLLAPSE_KEY) === '1'; }
    catch (e) { return false; }
  }
  function savePosition(bottom, right) {
    try { sessionStorage.setItem(POS_KEY, JSON.stringify({bottom: bottom, right: right})); }
    catch (e) {}
  }
  function loadPosition() {
    try { return JSON.parse(sessionStorage.getItem(POS_KEY)); }
    catch (e) { return null; }
  }
  function saveSize(w, h) {
    try { sessionStorage.setItem(SIZE_KEY, JSON.stringify({w: w, h: h})); }
    catch (e) {}
  }
  function loadSize() {
    try { return JSON.parse(sessionStorage.getItem(SIZE_KEY)); }
    catch (e) { return null; }
  }

  function scrapeContext() {
    var path = window.location.pathname;
    var hash = window.location.hash;
    var ctx = {};

    if (path.indexOf('/dashboards') === 0) {
      ctx.current_tool = 'opensearch_dashboards';
      var m = hash.match(DASH_UUID_RE) || (path + hash).match(DASH_UUID_RE);
      if (!m) m = path.match(DASH_UUID_RE);
      if (m) ctx.dashboard_id = m[1];
      var tm = hash.match(/time:\(from:'?([^',)]+)'?,to:'?([^',)]+)'?\)/);
      if (tm) { ctx.time_range_from = tm[1]; ctx.time_range_to = tm[2]; }
      var filterRe = /match_phrase:\(([^:]+):'([^']+)'\)/g;
      var filters = {};
      var fm;
      while ((fm = filterRe.exec(hash)) !== null) { filters[fm[1]] = fm[2]; }
      if (Object.keys(filters).length > 0) ctx.existing_filters = filters;
    } else if (path.indexOf('/arkime') === 0) {
      ctx.current_tool = 'arkime';
      var params = new URLSearchParams(window.location.search);
      var expr = params.get('expression');
      if (expr) {
        ctx.tool_context = ctx.tool_context || {};
        ctx.tool_context.arkime_expression = expr;
      }
      var dateParam = params.get('date');
      if (dateParam) {
        ctx.time_range_from = 'now-' + dateParam + 'h';
        ctx.time_range_to = 'now';
      } else {
        var startTime = params.get('startTime');
        var stopTime = params.get('stopTime');
        if (startTime) ctx.time_range_from = startTime;
        if (stopTime) ctx.time_range_to = stopTime;
      }
    } else if (path.indexOf('/netbox') === 0) {
      ctx.current_tool = 'netbox';
      // Extract asset type and ID from NetBox URL paths like /netbox/dcim/devices/7/
      var nbMatch = path.match(/\/netbox\/([a-z]+)\/([a-z-]+)\/(\d+)\//);
      if (nbMatch) {
        ctx.tool_context = ctx.tool_context || {};
        ctx.tool_context.netbox_section = nbMatch[1];   // e.g. "dcim", "ipam"
        ctx.tool_context.netbox_type = nbMatch[2];       // e.g. "devices", "ip-addresses"
        ctx.tool_context.netbox_id = nbMatch[3];         // e.g. "7"
      }
      // Extract search query from ?q= param
      var nbParams = new URLSearchParams(window.location.search);
      var nbQuery = nbParams.get('q');
      if (nbQuery) {
        ctx.tool_context = ctx.tool_context || {};
        ctx.tool_context.netbox_query = nbQuery;
      }
    }

    return Object.keys(ctx).length > 0 ? ctx : null;
  }

  fetch(STATUS_URL, {credentials: 'same-origin'})
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (data && data.enabled) boot();
    })
    .catch(function(e) { console.warn('Malcolm AI: status check failed:', e); });

  function boot() {
    attach();
    new MutationObserver(function() {
      if (!document.getElementById(HOST_ID)) attach();
    }).observe(document.body, {childList: true});
  }

  function attach() {
    if (document.getElementById(HOST_ID)) return;

    var host = document.createElement('div');
    host.id = HOST_ID;
    var savedPos = loadPosition();
    var posBottom = savedPos ? savedPos.bottom : 24;
    var posRight = savedPos ? savedPos.right : 24;
    host.style.cssText = 'position:fixed;bottom:' + posBottom + 'px;right:' + posRight + 'px;z-index:999999;';
    document.body.appendChild(host);

    // Inject styles into document head (no Shadow DOM for Playwright testing)
    var styleEl = document.getElementById('maw-styles');
    if (!styleEl) {
      styleEl = document.createElement('style');
      styleEl.id = 'maw-styles';
      styleEl.textContent = WIDGET_CSS;
      document.head.appendChild(styleEl);
    }

    var container = document.createElement('div');
    container.className = 'maw-root';
    container.innerHTML = WIDGET_HTML; // Safe: WIDGET_HTML is a static template defined in this file
    host.appendChild(container);

    var btn = host.querySelector('.maw-btn');
    var panel = host.querySelector('.maw-panel');
    var closeBtn = host.querySelector('.maw-close');
    var clearBtn = host.querySelector('.maw-clear');
    var exportBtn = host.querySelector('.maw-export');
    var collapseBtn = host.querySelector('.maw-collapse');
    var form = host.querySelector('.maw-form');
    var input = host.querySelector('.maw-input');
    var submitBtn = host.querySelector('.maw-submit');
    var chatLog = host.querySelector('.maw-chat-log');
    var headerEl = host.querySelector('.maw-header');
    var resizeHandle = host.querySelector('.maw-resize');
    var cmdPopup = host.querySelector('.maw-cmd-popup');
    var isOpen = loadPanelState();
    var isCollapsed = loadCollapseState();

    // Restore panel size
    var savedSize = loadSize();
    var panelW = savedSize ? savedSize.w : DEFAULT_WIDTH;
    var panelH = savedSize ? savedSize.h : DEFAULT_HEIGHT;
    panel.style.width = panelW + 'px';
    panel.style.height = panelH + 'px';

    // Restore panel + collapse state
    if (isOpen) {
      panel.classList.add('open');
      btn.classList.add('active');
      // Defer reposition to after DOM is ready
      setTimeout(function() { repositionPanel(); }, 0);
    }
    if (isCollapsed) {
      panel.classList.add('collapsed');
      collapseBtn.textContent = '\u25b2';
    }

    // Restore chat history
    var history = loadHistory();
    history.forEach(function(entry) {
      if (entry.type === 'user') renderUserMsg(entry.text);
      else if (entry.type === 'bot') {
        var histActions;
        if (entry.actions && entry.actions.length) {
          histActions = entry.actions;
        } else if (entry.url) {
          histActions = [{url: entry.url, label: 'Navigate \u2192'}];
        } else {
          histActions = [];
        }
        renderBotMsg(entry.tool, entry.message, histActions);
      }
      else if (entry.type === 'error') renderErrorMsg(entry.text);
      else if (entry.type === 'cancel') {
        var cel = document.createElement('div');
        cel.className = 'maw-msg maw-msg-cancel';
        cel.textContent = entry.text;
        chatLog.appendChild(cel);
      }
    });
    scrollToBottom();

    function repositionPanel() {
      var vw = window.innerWidth;
      var vh = window.innerHeight;
      var pw = panelW;
      var ph = panelH;
      var gap = 12; // gap between button and panel
      var btnSize = 56;

      // Widget position in viewport coordinates
      var btnLeft = vw - posRight - btnSize;
      var btnBottom = posBottom;
      var btnTop = vh - posBottom - btnSize;

      // Horizontal: panel extends left (right-anchored) by default.
      // Only flip to extend right when the icon is in the left 25% of the screen.
      var btnCenterX = btnLeft + btnSize / 2;
      if (btnCenterX < vw * 0.25) {
        // Icon is on the left side — panel extends right
        panel.style.left = btnLeft + 'px';
        panel.style.right = '';
      } else {
        // Icon is on the right side — panel extends left (default)
        panel.style.right = posRight + 'px';
        panel.style.left = '';
      }

      // Vertical: prefer panel above button; flip below if too close to top
      if (btnTop > ph + gap) {
        // Panel above button
        panel.style.bottom = (posBottom + btnSize + gap) + 'px';
        panel.style.top = '';
      } else {
        // Panel below button
        panel.style.top = (btnTop + btnSize + gap) + 'px';
        panel.style.bottom = '';
      }
    }

    function toggle() {
      isOpen = !isOpen;
      panel.classList.toggle('open', isOpen);
      btn.classList.toggle('active', isOpen);
      savePanelState(isOpen);
      if (isOpen) { repositionPanel(); scrollToBottom(); input.focus(); }
    }

    function toggleCollapse() {
      isCollapsed = !isCollapsed;
      panel.classList.toggle('collapsed', isCollapsed);
      collapseBtn.textContent = isCollapsed ? '\u25b2' : '\u25bc';
      saveCollapseState(isCollapsed);
      if (!isCollapsed) { scrollToBottom(); }
    }

    function scrollToBottom() {
      chatLog.scrollTop = chatLog.scrollHeight;
    }

    // --- Render functions (DOM only, no persistence) ---
    function renderUserMsg(text) {
      var el = document.createElement('div');
      el.className = 'maw-msg maw-msg-user';
      el.textContent = text;
      chatLog.appendChild(el);
    }

    function renderBotMsg(tool, message, actions) {
      var el = document.createElement('div');
      el.className = 'maw-msg maw-msg-bot';
      var toolTag = document.createElement('span');
      toolTag.className = 'maw-tool-tag';
      toolTag.textContent = tool;
      el.appendChild(toolTag);
      if (message) {
        var msg = document.createElement('span');
        msg.className = 'maw-msg-text';
        msg.textContent = ' ' + message;
        el.appendChild(msg);
      }
      // Render all action cards, not just the first
      var cards = actions || [];
      for (var i = 0; i < cards.length; i++) {
        var card = cards[i];
        var cardUrl = card.url || '';
        if (!cardUrl || (!cardUrl.startsWith('/') && !cardUrl.startsWith(window.location.origin))) continue;
        var link = document.createElement('a');
        link.className = 'maw-msg-link';
        link.href = cardUrl;
        link.textContent = card.label || 'Navigate \u2192';
        if (card.description) link.title = card.description;
        (function(u) {
          link.addEventListener('click', function(e) {
            e.preventDefault();
            window.location.href = u;
          });
        })(cardUrl);
        el.appendChild(link);
      }
      chatLog.appendChild(el);
    }

    function renderErrorMsg(text) {
      var el = document.createElement('div');
      el.className = 'maw-msg maw-msg-error';
      el.textContent = text;
      chatLog.appendChild(el);
    }

    // --- Add functions (render + persist to sessionStorage) ---
    function addUserMessage(text) {
      renderUserMsg(text);
      appendHistory({type: 'user', text: text});
      scrollToBottom();
    }

    function addBotMessage(tool, message, actions) {
      renderBotMsg(tool, message, actions);
      var acts = (actions && actions.length) ? actions.map(function(a) { return {url: a.url, label: a.label}; }) : null;
      appendHistory({type: 'bot', tool: tool, message: message, actions: acts});
      scrollToBottom();
    }

    function addErrorMessage(text) {
      renderErrorMsg(text);
      appendHistory({type: 'error', text: text});
      scrollToBottom();
    }

    function addCancelMessage() {
      var el = document.createElement('div');
      el.className = 'maw-msg maw-msg-cancel';
      el.textContent = 'Query cancelled.';
      chatLog.appendChild(el);
      appendHistory({type: 'cancel', text: 'Query cancelled.'});
      scrollToBottom();
    }

    function addThinking() {
      var el = document.createElement('div');
      el.className = 'maw-msg maw-msg-thinking';
      el.innerHTML = '<img src="' + WHEEL_URL + '" alt=""> Thinking\u2026';
      el.id = 'maw-thinking';
      chatLog.appendChild(el);
      scrollToBottom();
      return el;
    }

    function removeThinking() {
      var el = document.getElementById('maw-thinking');
      if (el) el.remove();
    }

    // --- Slash command autocomplete ---
    var cmdData = null;
    var cmdActiveIndex = -1;

    function loadCommands() {
      if (cmdData) return;
      var params = [];
      try {
        var ctx = scrapeContext();
        if (ctx && ctx.dashboard_id) params.push('dashboard_id=' + ctx.dashboard_id);
        if (ctx && ctx.current_tool) params.push('current_tool=' + ctx.current_tool);
      } catch (e) { console.warn('Malcolm AI: context scrape failed:', e); }
      var qs = params.length ? '?' + params.join('&') : '';
      fetch(COMMANDS_URL + qs, {credentials: 'same-origin'})
        .then(function(r) { return r.json(); })
        .then(function(data) { cmdData = data; })
        .catch(function(e) { console.warn('Malcolm AI: commands load failed:', e); });
    }
    // Pre-load commands
    loadCommands();

    function showCmdPopup(filter) {
      if (!cmdData || !cmdData.commands) { cmdPopup.style.display = 'none'; return; }
      var cmds = cmdData.commands;
      if (filter) {
        cmds = cmds.filter(function(c) { return c.name.indexOf(filter) === 0; });
      }
      if (cmds.length === 0) { cmdPopup.style.display = 'none'; return; }

      cmdPopup.innerHTML = '';
      cmds.forEach(function(c, i) {
        var el = document.createElement('div');
        el.className = 'maw-cmd-item' + (i === cmdActiveIndex ? ' active' : '');
        var nameSpan = document.createElement('span');
        nameSpan.className = 'maw-cmd-name';
        nameSpan.textContent = '/' + c.name;
        el.appendChild(nameSpan);
        var descSpan = document.createElement('span');
        descSpan.className = 'maw-cmd-desc';
        descSpan.textContent = c.description;
        el.appendChild(descSpan);
        var hintSpan = document.createElement('span');
        hintSpan.className = 'maw-cmd-hint';
        hintSpan.textContent = c.hint || '';
        el.appendChild(hintSpan);
        el.addEventListener('click', function() {
          input.value = '/' + c.name + ' ';
          cmdPopup.style.display = 'none';
          input.focus();
        });
        cmdPopup.appendChild(el);
      });
      cmdPopup.style.display = '';
    }

    function hideCmdPopup() {
      cmdPopup.style.display = 'none';
      cmdActiveIndex = -1;
    }

    input.addEventListener('input', function() {
      var val = input.value;
      if (val === '/') {
        cmdActiveIndex = -1;
        showCmdPopup('');
      } else if (val.startsWith('/') && val.indexOf(' ') === -1) {
        cmdActiveIndex = -1;
        showCmdPopup(val.substring(1).toLowerCase());
      } else {
        hideCmdPopup();
      }
    });

    input.addEventListener('keydown', function(e) {
      if (cmdPopup.style.display === 'none') return;
      var items = cmdPopup.querySelectorAll('.maw-cmd-item');
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        cmdActiveIndex = Math.min(cmdActiveIndex + 1, items.length - 1);
        items.forEach(function(el, i) { el.classList.toggle('active', i === cmdActiveIndex); });
        items[cmdActiveIndex].scrollIntoView({ block: 'nearest' });
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        cmdActiveIndex = Math.max(cmdActiveIndex - 1, 0);
        items.forEach(function(el, i) { el.classList.toggle('active', i === cmdActiveIndex); });
        items[cmdActiveIndex].scrollIntoView({ block: 'nearest' });
      } else if ((e.key === 'Tab' || e.key === 'Enter') && cmdActiveIndex >= 0) {
        e.preventDefault();
        items[cmdActiveIndex].click();
      } else if (e.key === 'Escape') {
        hideCmdPopup();
      }
    });

    // --- Drag handling (distinguish drag from click) ---
    var dragState = null;

    btn.addEventListener('mousedown', function(e) {
      if (e.button !== 0) return;
      dragState = {
        startX: e.clientX, startY: e.clientY,
        origBottom: posBottom, origRight: posRight,
        dragged: false
      };
      e.preventDefault();
    });

    document.addEventListener('mousemove', function(e) {
      if (!dragState) return;
      var dx = e.clientX - dragState.startX;
      var dy = e.clientY - dragState.startY;
      if (!dragState.dragged && Math.abs(dx) < DRAG_THRESHOLD && Math.abs(dy) < DRAG_THRESHOLD) return;
      dragState.dragged = true;
      posRight = Math.max(0, dragState.origRight - dx);
      posBottom = Math.max(0, dragState.origBottom - dy);
      // Clamp to viewport
      posRight = Math.min(posRight, window.innerWidth - 70);
      posBottom = Math.min(posBottom, window.innerHeight - 70);
      host.style.right = posRight + 'px';
      host.style.bottom = posBottom + 'px';
      if (isOpen) repositionPanel();
    });

    document.addEventListener('mouseup', function() {
      if (!dragState) return;
      if (dragState.dragged) {
        savePosition(posBottom, posRight);
        if (isOpen) repositionPanel();
      } else {
        toggle();
      }
      dragState = null;
    });

    // --- Resize handling ---
    var resizeState = null;

    resizeHandle.addEventListener('mousedown', function(e) {
      if (e.button !== 0) return;
      resizeState = {
        startX: e.clientX, startY: e.clientY,
        origW: panelW, origH: panelH
      };
      e.preventDefault();
      e.stopPropagation();
    });

    document.addEventListener('mousemove', function(e) {
      if (!resizeState) return;
      var dx = e.clientX - resizeState.startX;
      var dy = e.clientY - resizeState.startY;
      // Resize direction depends on panel position relative to button
      var btnLeft = window.innerWidth - posRight - 56;
      var btnCenterX = btnLeft + 28;
      // If panel extends left (icon on right), dragging left = wider
      if (btnCenterX >= window.innerWidth * 0.25) dx = -dx;
      // If panel is above, dragging up = taller
      var btnTop = window.innerHeight - posBottom - 56;
      if (btnTop > panelH + 12) dy = -dy;

      panelW = Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, resizeState.origW + dx));
      panelH = Math.max(MIN_HEIGHT, Math.min(MAX_HEIGHT, resizeState.origH + dy));
      panel.style.width = panelW + 'px';
      panel.style.height = panelH + 'px';
    });

    document.addEventListener('mouseup', function() {
      if (!resizeState) return;
      saveSize(panelW, panelH);
      resizeState = null;
    });

    closeBtn.addEventListener('click', toggle);
    collapseBtn.addEventListener('click', toggleCollapse);
    clearBtn.addEventListener('click', function() {
      clearHistory();
      chatLog.innerHTML = '';
    });
    exportBtn.addEventListener('click', function() { exportHistory(); });

    document.addEventListener('keydown', function(e) {
      if (e.key === 'Escape' && isOpen) toggle();
    });

    var activeAbort = null;

    function setSubmitState(isLoading) {
      if (isLoading) {
        submitBtn.textContent = 'Stop';
        submitBtn.classList.add('maw-stop');
        submitBtn.disabled = false;
      } else {
        submitBtn.textContent = 'Ask';
        submitBtn.classList.remove('maw-stop');
        submitBtn.disabled = false;
        activeAbort = null;
      }
    }

    submitBtn.addEventListener('click', function(e) {
      // Stop button bypasses form validation
      if (activeAbort) {
        e.preventDefault();
        activeAbort.abort();
        removeThinking();
        addCancelMessage();
        setSubmitState(false);
        input.focus();
        return;
      }
    });

    form.addEventListener('submit', function(e) {
      e.preventDefault();

      var query = input.value.trim();
      if (!query) return;

      addUserMessage(query);
      input.value = '';
      input.focus();

      var abortController = new AbortController();
      activeAbort = abortController;
      setSubmitState(true);
      var thinking = addThinking();

      var body = {query: query};
      var ctx = scrapeContext();
      if (ctx) body.current_context = ctx;

      // Send last 2 exchanges for pronoun/context resolution
      var hist = loadHistory();
      if (hist.length > 0) {
        var recent = hist.slice(-4);  // last 4 messages = up to 2 exchanges
        body.conversation_history = recent.map(function(m) {
          return {role: m.type === 'user' ? 'user' : 'assistant', content: m.text || ''};
        });
      }
      fetch(INTENT_URL, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body),
        signal: abortController.signal
      })
      .then(function(r) {
        if (!r.ok) {
          if (r.status === 401) throw new Error('Auth required \u2014 reload page.');
          var ct = r.headers.get('content-type') || '';
          if (ct.indexOf('application/json') !== -1) {
            return r.json().then(function(d) { throw new Error(d.error || 'Request failed'); });
          }
          throw new Error('Request failed (HTTP ' + r.status + ')');
        }
        return r.json();
      })
      .then(function(data) {
        removeThinking();
        setSubmitState(false);
        input.focus();

        if (data.status === 'error') {
          var errMsg = data.message;
          if (data.error_code) errMsg += ' [' + data.error_code + ']';
          addErrorMessage(errMsg);
          return;
        }

        var actions = data.actions || [];
        var tool = data.intent || 'unknown';

        addBotMessage(tool, data.message, actions);

        // Auto-navigate to first action (opt-out: server can set auto_navigate=false)
        var autoNav = data.auto_navigate !== false;
        var firstUrl = actions.length >= 1 ? actions[0].url : null;
        if (autoNav && firstUrl && (firstUrl.startsWith('/') || firstUrl.startsWith(window.location.origin))) {
          setTimeout(function() { window.location.href = firstUrl; }, 600);
        }
      })
      .catch(function(err) {
        if (err.name === 'AbortError') return;  // user cancelled, already handled
        removeThinking();
        setSubmitState(false);
        input.focus();
        addErrorMessage(err.message || 'Could not reach the AI service.');
      });
    });
  }

  // --- Template ---
  var WIDGET_HTML = [
    '<div class="maw-panel">',
    '  <div class="maw-header">',
    '    <span class="maw-title"><span class="maw-resize" title="Drag to resize"><svg width="12" height="12" viewBox="0 0 12 12"><path d="M11 11L1 1M1 1H5M1 1V5M11 11H7M11 11V7" stroke="currentColor" stroke-width="1.5" fill="none" stroke-linecap="round"/></svg></span> Ask Malcolm</span>',
    '    <span class="maw-header-btns">',
    '      <button class="maw-export" title="Export chat"><svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M7 1v8M7 9L4 6M7 9l3-3M2 12h10" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg></button>',
    '      <button class="maw-clear" title="Clear chat"><svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M3 4h8M5.5 4V3a1 1 0 011-1h1a1 1 0 011 1v1M6 6.5v3M8 6.5v3M4 4l.5 7a1.5 1.5 0 001.5 1.5h2a1.5 1.5 0 001.5-1.5L10 4" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/></svg></button>',
    '      <button class="maw-collapse" title="Collapse">\u25bc</button>',
    '      <button class="maw-close" title="Close">\u00d7</button>',
    '    </span>',
    '  </div>',
    '  <div class="maw-chat-log"></div>',
    '  <div class="maw-form-wrap">',
    '    <div class="maw-cmd-popup" style="display:none"></div>',
    '    <form class="maw-form" autocomplete="off">',
    '      <input class="maw-input" type="text" placeholder="Type / for commands or ask anything" required>',
    '      <button class="maw-submit" type="submit">Ask</button>',
    '    </form>',
    '  </div>',
    '</div>',
    '<div class="maw-btn" title="Ask Malcolm">',
    '  <img class="maw-btn-img" src="' + WHEEL_URL + '" alt="Ask Malcolm">',
    '</div>',
  ].join('\n');

  // --- Styles ---
  var WIDGET_CSS = [
    '/* Non-Shadow-DOM: no :host needed */',

    '@keyframes maw-spin {',
    '  0%   { transform: rotate(0deg); }',
    '  25%  { transform: rotate(180deg); }',
    '  55%  { transform: rotate(270deg); }',
    '  75%  { transform: rotate(290deg); }',
    '  100% { transform: rotate(360deg); }',
    '}',

    '@keyframes maw-spin-once {',
    '  0%   { transform: rotate(0deg); }',
    '  100% { transform: rotate(360deg); }',
    '}',

    '.maw-root {',
    '  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;',
    '  font-size: 14px; line-height: 1.4; color: #e0e0e0;',
    '  box-sizing: border-box;',
    '}',
    '.maw-root *, .maw-root *::before, .maw-root *::after {',
    '  box-sizing: border-box;',
    '}',

    /* Floating button */
    '.maw-btn {',
    '  width: 56px; height: 56px; border-radius: 50%;',
    '  background: #1a1a2e; border: 2px solid #c5a44e;',
    '  cursor: grab; display: flex; align-items: center; justify-content: center;',
    '  box-shadow: 0 4px 12px rgba(0,0,0,0.3);',
    '  transition: box-shadow 0.2s, transform 0.2s;',
    '  margin-left: auto;',
    '}',
    '.maw-btn:hover {',
    '  box-shadow: 0 6px 20px rgba(197,164,78,0.4);',
    '  transform: scale(1.08);',
    '}',
    '.maw-btn:hover .maw-btn-img {',
    '  animation: maw-spin 1.6s infinite cubic-bezier(0.4, 0, 0.2, 1);',
    '}',
    '.maw-btn-img {',
    '  width: 34px; height: 34px; pointer-events: none;',
    '}',
    '.maw-btn.active .maw-btn-img {',
    '  animation: maw-spin-once 0.4s ease-out;',
    '}',

    /* Chat panel — position and size set dynamically by JS */
    '.maw-panel {',
    '  display: none; position: fixed;',
    '  background: #1a1a2e; border: 1px solid #2a2a4a; border-radius: 12px;',
    '  box-shadow: 0 8px 32px rgba(0,0,0,0.5);',
    '  overflow: hidden; flex-direction: column;',
    '}',
    '.maw-panel.open { display: flex; }',

    /* Header */
    '.maw-header {',
    '  display: flex; align-items: center; justify-content: space-between;',
    '  padding: 12px 16px; background: #12122a; border-bottom: 1px solid #2a2a4a;',
    '  flex-shrink: 0;',
    '}',
    '.maw-title { color: #c5a44e; font-weight: 600; font-size: 15px; margin: 0; }',
    '.maw-header-btns { display: flex; gap: 4px; align-items: center; }',
    '.maw-close, .maw-collapse, .maw-clear, .maw-export {',
    '  background: none; border: none; color: #888; font-size: 16px;',
    '  cursor: pointer; padding: 2px 6px; line-height: 1;',
    '  display: inline-flex; align-items: center;',
    '}',
    '.maw-close:hover, .maw-collapse:hover { color: #ccc; }',
    '.maw-clear:hover { color: #f87171; }',
    '.maw-export:hover { color: #60a5fa; }',

    /* Collapsed state: hide chat log + resize, compact layout */
    '.maw-panel.collapsed .maw-chat-log { display: none; }',
    '.maw-panel.collapsed .maw-resize { display: none; }',
    '.maw-panel.collapsed { height: auto !important; }',

    /* Resize handle (inline in header) */
    '.maw-resize {',
    '  cursor: nwse-resize; color: #555;',
    '  padding: 0 6px 0 0; user-select: none; transition: color 0.2s;',
    '  display: inline-flex; align-items: center; vertical-align: middle;',
    '}',
    '.maw-resize:hover { color: #c5a44e; }',

    /* Chat log */
    '.maw-chat-log {',
    '  flex: 1; overflow-y: auto; padding: 12px 16px;',
    '  display: flex; flex-direction: column; gap: 8px;',
    '  scrollbar-color: #2a2a4a #1a1a2e;',
    '}',
    '.maw-chat-log::-webkit-scrollbar { width: 6px; }',
    '.maw-chat-log::-webkit-scrollbar-track { background: #1a1a2e; }',
    '.maw-chat-log::-webkit-scrollbar-thumb { background: #2a2a4a; border-radius: 3px; }',
    '.maw-chat-log::-webkit-scrollbar-thumb:hover { background: #3a3a5a; }',

    /* Messages */
    '.maw-msg {',
    '  max-width: 85%; padding: 8px 12px; border-radius: 10px;',
    '  font-size: 13px; word-wrap: break-word;',
    '}',
    '.maw-msg-user {',
    '  align-self: flex-end; background: #2563eb; color: #fff;',
    '  border-bottom-right-radius: 3px;',
    '}',
    '.maw-msg-bot {',
    '  align-self: flex-start; background: #1e1e3a; color: #ddd;',
    '  border: 1px solid #2a2a4a; border-bottom-left-radius: 3px;',
    '}',
    '.maw-msg-error {',
    '  align-self: flex-start; background: rgba(220,38,38,0.15); color: #f87171;',
    '  border: 1px solid rgba(220,38,38,0.3); border-bottom-left-radius: 3px;',
    '}',
    '.maw-msg-cancel {',
    '  align-self: center; background: rgba(150,150,150,0.15); color: #999;',
    '  border: 1px solid rgba(150,150,150,0.3); font-style: italic; font-size: 12px;',
    '  padding: 4px 12px; border-radius: 8px;',
    '}',
    '.maw-msg-thinking {',
    '  align-self: flex-start; color: #888; font-size: 13px;',
    '}',
    '.maw-msg-thinking img {',
    '  width: 18px; height: 18px; vertical-align: middle;',
    '  animation: maw-spin 1.6s infinite cubic-bezier(0.4, 0, 0.2, 1);',
    '}',

    /* Tool tag */
    '.maw-tool-tag {',
    '  display: inline-block; background: #2a2a4a; color: #c5a44e;',
    '  padding: 2px 6px; border-radius: 4px; font-size: 11px;',
    '  font-weight: 600; font-family: monospace; margin-right: 4px;',
    '  vertical-align: middle;',
    '}',
    '.maw-msg-text { vertical-align: middle; }',

    /* Navigate link */
    '.maw-msg-link {',
    '  display: block; margin-top: 6px; color: #60a5fa;',
    '  font-size: 12px; text-decoration: none; cursor: pointer;',
    '}',
    '.maw-msg-link:hover { text-decoration: underline; color: #93c5fd; }',

    /* Command popup */
    '.maw-cmd-popup {',
    '  max-height: 200px; overflow-y: auto; padding: 4px 0;',
    '  border-bottom: 1px solid #2a2a4a; margin-bottom: 0;',
    '  scrollbar-color: #2a2a4a #12122a;',
    '}',
    '.maw-cmd-item {',
    '  padding: 8px 16px; cursor: pointer; display: flex; gap: 8px;',
    '  align-items: baseline;',
    '}',
    '.maw-cmd-item:hover, .maw-cmd-item.active {',
    '  background: #1e1e3a;',
    '}',
    '.maw-cmd-name {',
    '  color: #c5a44e; font-family: monospace; font-weight: 600;',
    '  font-size: 13px; white-space: nowrap;',
    '}',
    '.maw-cmd-desc {',
    '  color: #888; font-size: 12px; white-space: nowrap;',
    '  overflow: hidden; text-overflow: ellipsis;',
    '}',
    '.maw-cmd-hint {',
    '  color: #555; font-size: 11px; font-family: monospace;',
    '}',

    /* Form area */
    '.maw-form-wrap {',
    '  padding: 12px 16px; border-top: 1px solid #2a2a4a;',
    '  background: #12122a; flex-shrink: 0;',
    '}',
    '.maw-form { display: flex; gap: 8px; }',
    '.maw-input {',
    '  flex: 1; padding: 10px 12px; border-radius: 8px;',
    '  border: 1px solid #2a2a4a; background: #0d0d1a; color: #e0e0e0;',
    '  font-size: 14px; font-family: inherit; outline: none;',
    '  width: 0; min-width: 0;',
    '}',
    '.maw-input:focus { border-color: #c5a44e; }',
    '.maw-input::placeholder { color: #666; }',
    '.maw-submit {',
    '  padding: 10px 18px; border-radius: 8px; border: none;',
    '  background: #c5a44e; color: #1a1a2e; font-weight: 600; font-size: 14px;',
    '  font-family: inherit; cursor: pointer; white-space: nowrap;',
    '}',
    '.maw-submit:hover { background: #d4b65e; }',
    '.maw-submit:disabled { opacity: 0.5; cursor: not-allowed; }',
    '.maw-submit.maw-stop { background: #c0392b; }',
    '.maw-submit.maw-stop:hover { background: #e74c3c; }',
  ].join('\n');
})();
