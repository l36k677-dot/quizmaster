/* ============================================================
   新手指引（荷宝导览）
   ------------------------------------------------------------
   职责边界：**只做几何与交互**。
   步骤文案、目标锚点、姿势全部来自服务端渲染的 #tour-data
   （见 app/services/tour.py 与 templates/_tour.html），这里不写任何
   业务文案 —— 免得同一句话在 Python 与 JS 里各存一份、改一处漏一处。

   四条硬约束：
     1. 全部可跳过：跳过按钮、Esc、点遮罩、最后一步的「完成」等价；
        而「已看过」在**开场那一刻**就记下（localStorage + 服务端账号标记）——
        记的是「展示过」而不是「学完了」。否则用户中途关掉页面，
        下次进首页又被同一份指引拦一次，那才是真正烦人的地方；
        想再看时，账户菜单里随时能重开。
     2. 渐进增强：脚本没跑起来时覆盖层与入口按钮都是 hidden 的，
        页面上不会留下一个点了没反应的控件；
     3. 「本页没有这一步就跳过」：按锚点是否真实渲染（有布局盒）过滤，
        所以同一份步骤定义在首页是 8 步、在知识库页是 5 步；
     4. 减少动效 / 放映模式下不做平滑滚动与过渡，直接落位。
   ============================================================ */
(function () {
  'use strict';

  const dataNode = document.getElementById('tour-data');
  const layer = document.getElementById('tour-layer');
  if (!dataNode || !layer) return;          // 未登录或该页不带指引

  let config;
  try {
    config = JSON.parse(dataNode.textContent || '{}');
  } catch (err) {
    return;                                  // 数据坏了就整块不启用，不影响页面
  }
  const sourceSteps = Array.isArray(config.steps) ? config.steps : [];
  if (!sourceSteps.length) return;

  const findByAttr = (name) => layer.querySelector(`[data-tour-${name}]`);
  const card = findByAttr('card');
  const spot = findByAttr('spot');
  const mascotBox = findByAttr('mascot');
  const titleNode = findByAttr('title');
  const bodyNode = findByAttr('body');
  const countNode = findByAttr('count');
  const dotsNode = findByAttr('dots');
  const prevBtn = findByAttr('prev');
  const nextBtn = findByAttr('next');
  const skipBtn = findByAttr('skip');
  const backdrop = findByAttr('backdrop');

  const STORAGE_KEY = 'qm.tour.' + (config.version || 'v1');
  const SHEET_MAX = 620;                     // 与 app.css 的窄屏断点一致
  const GAP = 14;                            // 卡片与高亮框之间的间距
  const MARGIN = 14;                         // 卡片与视口边缘的最小留白

  let steps = [];
  let index = 0;
  let running = false;
  let lastFocus = null;
  let rafId = 0;

  const reduced = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const fastLayout = () => reduced() || !document.documentElement.classList.contains('anim');

  /* ---------- 已读标记：本地做镜像，服务端为准 ---------- */
  function readLocal() {
    try { return window.localStorage.getItem(STORAGE_KEY) === 'done'; } catch (err) { return false; }
  }
  function writeLocal() {
    try { window.localStorage.setItem(STORAGE_KEY, 'done'); } catch (err) { /* 无痕模式忽略 */ }
  }
  function post(url, body) {
    if (typeof API !== 'undefined' && API && API.post) return API.post(url, body || {});
    return fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
      credentials: 'same-origin',
    });
  }
  function markSeen() {
    writeLocal();
    if (config.seen) return;                 // 服务端已经记过，不用再发
    config.seen = true;
    try { Promise.resolve(post('/api/tour/seen', {})).catch(() => {}); } catch (err) { /* 忽略 */ }
  }

  /* ---------- 步骤筛选：本页没有对应锚点就不出现在这一步序列里 ---------- */
  function resolveTarget(step) {
    if (!step || !step.target) return null;
    let node = null;
    try { node = document.querySelector(step.target); } catch (err) { return null; }
    if (!node) return null;
    // display:none 的元素没有布局盒 —— 与「不存在」同等对待
    return node.getClientRects().length ? node : null;
  }
  function collectSteps() {
    steps = sourceSteps.filter((step) => !step.target || resolveTarget(step));
  }

  /* ---------- 渲染 ---------- */
  function renderDots() {
    if (!dotsNode) return;
    dotsNode.textContent = '';
    steps.forEach((_, i) => {
      const dot = document.createElement('span');
      dot.className = 'tour-dot' + (i === index ? ' is-active' : '');
      dotsNode.appendChild(dot);
    });
  }

  function render() {
    const step = steps[index] || {};
    if (mascotBox) mascotBox.setAttribute('data-pose', step.pose || 'wave');
    if (titleNode) titleNode.textContent = step.title || '';
    if (bodyNode) bodyNode.textContent = step.body || '';
    if (countNode) countNode.textContent = `第 ${index + 1} / ${steps.length} 步`;
    if (prevBtn) prevBtn.disabled = index === 0;
    if (nextBtn) nextBtn.textContent = index === steps.length - 1 ? '完成' : '下一步';
    renderDots();
    layer.classList.toggle('is-first', index === 0);
    layer.classList.toggle('is-last', index === steps.length - 1);
  }

  /* ---------- 定位：高亮框跟着目标，卡片躲开高亮框 ---------- */
  function targetRect(step) {
    const node = resolveTarget(step);
    return node ? node.getBoundingClientRect() : null;
  }

  function layoutSpot(rect) {
    if (!spot) return;
    if (!rect) {
      layer.classList.add('is-centered');
      return;
    }
    layer.classList.remove('is-centered');
    const pad = 8;
    spot.style.top = `${Math.round(rect.top - pad)}px`;
    spot.style.left = `${Math.round(rect.left - pad)}px`;
    spot.style.width = `${Math.round(rect.width + pad * 2)}px`;
    spot.style.height = `${Math.round(rect.height + pad * 2)}px`;
  }

  function layoutCard(rect, placement) {
    if (!card) return;
    // 窄屏：卡片贴底做成抽屉，位置交给 CSS，避免在手机上算错位
    if (window.matchMedia(`(max-width: ${SHEET_MAX}px)`).matches) {
      card.classList.add('is-sheet');
      card.style.top = '';
      card.style.left = '';
      return;
    }
    card.classList.remove('is-sheet');

    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const cw = card.offsetWidth;
    const ch = card.offsetHeight;
    const clamp = (value, low, high) => Math.min(Math.max(value, low), Math.max(low, high));

    if (!rect || placement === 'center') {
      card.style.left = `${Math.round((vw - cw) / 2)}px`;
      card.style.top = `${Math.round((vh - ch) / 2)}px`;
      return;
    }

    let x = null;
    let y = null;

    // 左右优先（侧栏、看板这类竖长目标，卡片放在侧边最不挡视线）
    if (placement === 'left' && rect.left - GAP - cw >= MARGIN) {
      x = rect.left - GAP - cw;
      y = rect.top + rect.height / 2 - ch / 2;
    } else if (placement === 'right' && rect.right + GAP + cw <= vw - MARGIN) {
      x = rect.right + GAP;
      y = rect.top + rect.height / 2 - ch / 2;
    }

    if (x === null) {
      // 上下择其一：优先放在目标下方（顺着阅读方向）
      if (rect.bottom + GAP + ch <= vh - MARGIN) y = rect.bottom + GAP;
      else if (rect.top - GAP - ch >= MARGIN) y = rect.top - GAP - ch;
      else y = rect.top + rect.height / 2 - ch / 2;   // 上下都放不下：贴着目标居中
      x = rect.left + rect.width / 2 - cw / 2;
    }

    card.style.left = `${Math.round(clamp(x, MARGIN, vw - cw - MARGIN))}px`;
    card.style.top = `${Math.round(clamp(y, MARGIN, vh - ch - MARGIN))}px`;
  }

  function layout() {
    if (!running) return;
    const step = steps[index] || {};
    const rect = targetRect(step);
    layoutSpot(rect);
    layoutCard(rect, step.placement);
  }

  /* 目标不在舒适的视线范围内时先滚过去；平滑滚动期间靠 scroll 监听持续跟位 */
  function ensureVisible(step) {
    const rect = targetRect(step);
    if (!rect) return;
    const safeTop = 88;
    const safeBottom = window.innerHeight - 48;
    if (rect.top >= safeTop && rect.bottom <= safeBottom) return;
    const top = rect.top + window.pageYOffset - Math.max(safeTop, (window.innerHeight - rect.height) / 2);
    window.scrollTo({ top: Math.max(0, top), behavior: fastLayout() ? 'auto' : 'smooth' });
  }

  function schedule() {
    if (!running) return;
    if (rafId) cancelAnimationFrame(rafId);
    rafId = requestAnimationFrame(() => { rafId = 0; layout(); });
  }

  /* ---------- 步骤切换 ---------- */
  function show(next) {
    index = Math.min(Math.max(next, 0), steps.length - 1);
    render();
    ensureVisible(steps[index]);
    layout();
    schedule();
    // 平滑滚动大约 400ms，途中的位置变化由 scroll 监听补上，这里再兜两次
    window.setTimeout(schedule, 220);
    window.setTimeout(schedule, 480);
    // 键盘用户不必手动找按钮：焦点始终留在指引卡片内
    if (nextBtn && !fastLayout()) nextBtn.focus({ preventScroll: true });
  }

  function start() {
    collectSteps();
    if (!steps.length) return;
    if (running) { show(0); return; }
    running = true;
    lastFocus = document.activeElement;
    layer.hidden = false;
    layer.classList.add('is-open');
    document.documentElement.classList.add('tour-on');
    // 「开场即记录」：见文件头约束 1。放到开场之后、定位之前，
    // 免得用户看到卡片的第一帧还没落库就关掉页面。
    markSeen();
    index = 0;
    render();
    ensureVisible(steps[0]);
    layout();
    schedule();
    window.setTimeout(schedule, 240);
    if (nextBtn) nextBtn.focus({ preventScroll: true });
  }

  /* 跳过 = 关闭。不做二次确认：指引随时能被重新打开，拦一次就够烦了。 */
  function finish(completed) {
    if (!running) return;
    running = false;
    layer.hidden = true;
    layer.classList.remove('is-open');
    document.documentElement.classList.remove('tour-on');
    card.classList.remove('is-sheet');
    markSeen();
    if (lastFocus && typeof lastFocus.focus === 'function') {
      lastFocus.focus({ preventScroll: true });
    }
    if (completed && typeof toast === 'function') {
      toast('指引结束。想再看一遍：右上角账户菜单 → 新手指引。', 'ok', 4200);
    }
  }

  /* ---------- 事件 ---------- */
  if (prevBtn) prevBtn.addEventListener('click', () => show(index - 1));
  if (nextBtn) {
    nextBtn.addEventListener('click', () => {
      if (index >= steps.length - 1) finish(true);
      else show(index + 1);
    });
  }
  if (skipBtn) skipBtn.addEventListener('click', () => finish(false));
  if (backdrop) backdrop.addEventListener('click', () => finish(false));

  // Esc 关闭 + Tab 锁在卡片内（原生 <dialog> 在旧内核上行为不一致，手写更可控）
  layer.addEventListener('keydown', (event) => {
    if (!running) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      finish(false);
      return;
    }
    if (event.key !== 'Tab') return;
    const focusables = [skipBtn, prevBtn, nextBtn].filter(
      (node) => node && !node.disabled && node.getClientRects().length
    );
    if (!focusables.length) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    const current = document.activeElement;
    if (event.shiftKey && (current === first || !layer.contains(current))) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && current === last) {
      event.preventDefault();
      first.focus();
    }
  });

  window.addEventListener('scroll', schedule, { passive: true });
  window.addEventListener('resize', schedule, { passive: true });

  // 账户菜单里的入口：脚本跑起来后才解除 hidden（见文件头约束 2）
  const triggers = document.querySelectorAll('[data-action="tour-start"]');
  Array.prototype.forEach.call(triggers, (node) => { node.hidden = false; });
  document.addEventListener('click', (event) => {
    const trigger = event.target.closest('[data-action="tour-start"]');
    if (!trigger) return;
    event.preventDefault();
    const menu = document.getElementById('user-menu');
    if (menu) menu.open = false;             // 指引盖住页面时菜单不该还开着
    start();
  });

  /* ---------- 启动判定 ---------- */
  function boot() {
    const params = new URLSearchParams(window.location.search);
    const force = params.get('tour');
    if (force === '1') {                     // 演示 / 截图：强制开场
      window.setTimeout(start, 400);
      return;
    }
    if (force === '0') return;               // 明确不要
    if (config.autoStart && !config.seen && !readLocal()) {
      window.setTimeout(start, 1100);        // 等首页统计与动画先落位
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }

  // 供探针与演示脚本调用
  window.QMTour = {
    start,
    stop: finish,
    isRunning: () => running,
    steps: () => steps.map((step) => step.key),
  };
})();
