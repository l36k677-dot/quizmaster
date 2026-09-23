/* 通用前端工具：统一请求封装、提示、格式化 */

const API = {
  async request(url, options = {}) {
    const config = {
      method: options.method || 'GET',
      headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
      credentials: 'same-origin',
    };
    if (options.body !== undefined) {
      config.body = JSON.stringify(options.body);
    }
    let response;
    try {
      response = await fetch(url, config);
    } catch (err) {
      throw { code: 'NETWORK_ERROR', message: '网络异常，请检查服务是否运行', status: 0 };
    }

    let payload = null;
    try {
      payload = await response.json();
    } catch (err) {
      throw { code: 'BAD_RESPONSE', message: `服务返回异常（HTTP ${response.status}）`, status: response.status };
    }

    if (!response.ok || payload.success === false) {
      const error = (payload && payload.error) || {};
      throw {
        code: error.code || 'UNKNOWN',
        message: error.message || `请求失败（HTTP ${response.status}）`,
        status: response.status,
      };
    }
    return payload.data;
  },
  get(url) { return this.request(url); },
  post(url, body) { return this.request(url, { method: 'POST', body }); },
  put(url, body) { return this.request(url, { method: 'PUT', body }); },
  patch(url, body) { return this.request(url, { method: 'PATCH', body }); },
  del(url) { return this.request(url, { method: 'DELETE' }); },
};

function toast(message, type = 'info', timeout = 3200) {
  let wrap = document.querySelector('.toast-wrap');
  if (!wrap) {
    wrap = document.createElement('div');
    wrap.className = 'toast-wrap';
    document.body.appendChild(wrap);
    wrap.addEventListener('click', (event) => dismissToast(event.target.closest('.toast')));
  }
  const node = document.createElement('div');
  node.className = `toast ${type}`;
  node.textContent = message;
  wrap.appendChild(node);
  setTimeout(() => dismissToast(node), timeout);
}

function dismissToast(node) {
  if (!node || node.classList.contains('is-out')) return;
  node.classList.add('is-out');
  setTimeout(() => node.remove(), 200);
}

function escapeHtml(value) {
  if (value === null || value === undefined) return '';
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function formatDuration(seconds) {
  if (seconds === null || seconds === undefined) return '--:--';
  const total = Math.max(0, Math.floor(seconds));
  const m = String(Math.floor(total / 60)).padStart(2, '0');
  const s = String(total % 60).padStart(2, '0');
  return `${m}:${s}`;
}

async function logout() {
  try {
    await API.post('/api/auth/logout', {});
    window.location.href = '/login';
  } catch (err) {
    toast(err.message, 'err');
  }
}

document.addEventListener('click', (event) => {
  const target = event.target.closest('[data-action="logout"]');
  if (target) {
    event.preventDefault();
    logout();
  }
});

/* ============================================================
   动效与交互层
   ------------------------------------------------------------
   设计原则：
   1. 全部为「渐进增强」—— 脚本失效时页面内容依然完整可读，
      动效只负责让状态变化更顺滑、让重点更醒目。
   2. 只动 transform / opacity，避免触发重排。
   3. 用户在系统里开启「减少动态效果」时全部退化为即时状态
      （由 html.anim 类控制，见 base.html 的头部脚本）。
   ============================================================ */

const MOTION_OK = document.documentElement.classList.contains('anim');
const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

/* 放映模式：知识库加 ?reveal=1 时 <body> 会带上 is-present。
   它是给教师现场投屏用的 —— 要求「不滚动、一屏读完」，
   所以和系统「减少动态效果」共用同一条退化路径：
   内容直接可见、数字直接写终值、卡片直接翻开。 */
const PRESENT_MODE = document.body.classList.contains('is-present');
const NO_MOTION = !MOTION_OK || prefersReducedMotion || PRESENT_MODE;

/* --- 视口观察器：元素进入视口时回调一次 -----------------------
   这里刻意不用 IntersectionObserver —— 它在 headless 截图、
   部分嵌入式浏览器里不会触发，会让内容停在「未揭示」的透明状态。
   改成 scroll + 时间戳节流 + 同步 getBoundingClientRect，
   行为完全可预期；页面上几十个元素的量级，性能负担可以忽略。 */
function createViewportWatcher(onEnter, ratio = 0.94) {
  const pending = new Set();
  let lastRun = 0;

  const check = () => {
    const height = window.innerHeight || document.documentElement.clientHeight;
    pending.forEach((node) => {
      const rect = node.getBoundingClientRect();
      if (rect.bottom > 0 && rect.top < height * ratio) {
        pending.delete(node);
        onEnter(node);
      }
    });
  };

  const schedule = () => {
    const now = performance.now();
    if (now - lastRun < 60) return;
    lastRun = now;
    check();
  };

  window.addEventListener('scroll', schedule, { passive: true });
  window.addEventListener('resize', schedule, { passive: true });

  /* 浏览器在 load 之后才把视口定位到 URL 锚点（例如 /knowledge#people），
     恢复上次滚动位置也是同理：这两次定位都不会派发 scroll 事件。
     若只在 start() 里查一次，锚点所在区块会因为「从未被检查」而一直停在透明态。
     所以在 load / hashchange 时补检，并在启动后几个时间点各查一次兜底。 */
  window.addEventListener('load', check);
  window.addEventListener('hashchange', () => {
    check();
    setTimeout(check, 80);
  });

  return {
    observe(node) { pending.add(node); },
    start() {
      check();
      requestAnimationFrame(check);
      [0, 80, 240, 600].forEach((delay) => setTimeout(check, delay));
    },
  };
}

/* --- 滚动揭示 ------------------------------------------------- */
function initReveal() {
  const nodes = Array.from(document.querySelectorAll('.reveal'));
  if (!nodes.length) return;

  // 用户要求减少动效时（此时 html.anim 也不会被注入），直接全部显示
  if (NO_MOTION) {
    nodes.forEach((node) => node.classList.add('is-visible'));
    return;
  }

  const watcher = createViewportWatcher((node) => node.classList.add('is-visible'));
  nodes.forEach((node) => watcher.observe(node));
  watcher.start();
}

/* --- 数字滚动计数 --------------------------------------------- */
function countUp(node) {
  const target = Number(node.dataset.count);
  if (!Number.isFinite(target)) return;
  if (NO_MOTION) {
    node.textContent = String(target);
    return;
  }
  const duration = 900;
  const started = performance.now();
  const tick = (now) => {
    const progress = Math.min(1, (now - started) / duration);
    const eased = 1 - Math.pow(1 - progress, 4); // easeOutQuart
    node.textContent = String(Math.round(target * eased));
    if (progress < 1) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
  // 兜底：rAF 被节流或未执行时，也保证最终落到真实值
  window.setTimeout(() => { node.textContent = String(target); }, duration + 150);
}

function initCounters() {
  const nodes = Array.from(document.querySelectorAll('[data-count]'));
  if (!nodes.length) return;

  if (NO_MOTION) {
    nodes.forEach((node) => { node.textContent = String(Number(node.dataset.count)); });
    return;
  }

  const watcher = createViewportWatcher(countUp, 0.98);
  nodes.forEach((node) => watcher.observe(node));
  watcher.start();
}

/* --- 得分环：从 0 度转到目标角度 ------------------------------- */
function initScoreRings() {
  document.querySelectorAll('.score-ring').forEach((ring) => {
    const target = ring.style.getPropertyValue('--ring').trim();
    if (!target) return;
    if (NO_MOTION) return;
    ring.style.setProperty('--ring', '0deg');
    void ring.offsetWidth; // 强制回流，确保过渡从 0 开始
    requestAnimationFrame(() => ring.style.setProperty('--ring', target));
    window.setTimeout(() => ring.style.setProperty('--ring', target), 1500);
  });
}

/* --- 进度条：从 0 涨到目标宽度 -------------------------------- */
function initProgressBars() {
  if (NO_MOTION) return;
  document.querySelectorAll('[data-progress]').forEach((node) => {
    const target = node.style.width || node.dataset.progress;
    if (!target) return;
    node.style.width = '0%';
    void node.offsetWidth;
    requestAnimationFrame(() => { node.style.width = target; });
    window.setTimeout(() => { node.style.width = target; }, 1200);
  });
}

/* --- 时间轴：轴线随滚动进度生长 ------------------------------- */
function initTimelineProgress() {
  const timeline = document.querySelector('.timeline');
  if (!timeline || NO_MOTION) return;

  let lastRun = 0;
  const update = () => {
    const now = performance.now();
    if (now - lastRun < 60) return;
    lastRun = now;
    const rect = timeline.getBoundingClientRect();
    const total = rect.height || 1;
    const scrolled = window.innerHeight * 0.72 - rect.top;
    const progress = Math.max(0, Math.min(1, scrolled / total));
    timeline.style.setProperty('--tl-progress', progress.toFixed(3));
  };
  window.addEventListener('scroll', update, { passive: true });
  window.addEventListener('resize', update, { passive: true });
  update();
}

/* --- 按钮点击波纹 --------------------------------------------- */
function initRipple() {
  if (NO_MOTION) return;
  document.addEventListener('pointerdown', (event) => {
    const button = event.target.closest('.btn');
    if (!button || button.disabled || event.button !== 0) return;
    const rect = button.getBoundingClientRect();
    const size = Math.max(rect.width, rect.height);
    const bit = document.createElement('span');
    bit.className = 'ripple';
    bit.style.width = `${size}px`;
    bit.style.height = `${size}px`;
    bit.style.left = `${event.clientX - rect.left - size / 2}px`;
    bit.style.top = `${event.clientY - rect.top - size / 2}px`;
    button.appendChild(bit);
    setTimeout(() => bit.remove(), 640);
  });
}

/* --- 庆祝粒子：得分达标或解锁成就时触发 ----------------------- */
function celebrate() {
  if (NO_MOTION) return;
  const layer = document.createElement('div');
  layer.className = 'confetti-layer';
  const palette = ['#c9a961', '#4a7fb5', '#3f9c7a', '#e9eef7', '#8fd0b4'];
  for (let i = 0; i < 46; i += 1) {
    const bit = document.createElement('span');
    bit.className = 'confetti-bit';
    bit.style.left = `${Math.random() * 100}%`;
    bit.style.background = palette[i % palette.length];
    bit.style.setProperty('--dx', `${(Math.random() - 0.5) * 180}px`);
    bit.style.setProperty('--rot', `${360 + Math.random() * 720}deg`);
    bit.style.setProperty('--fall', `${1800 + Math.random() * 1400}ms`);
    bit.style.animationDelay = `${Math.random() * 320}ms`;
    layer.appendChild(bit);
  }
  document.body.appendChild(layer);
  setTimeout(() => layer.remove(), 4400);
}

/* --- 知识库：主题切换 / 翻卡 / 洗牌 --------------------------- */
function initFlipCards() {
  // 事件委托：卡片任意位置或专用按钮都能翻面
  document.addEventListener('click', (event) => {
    const card = event.target.closest('.flip');
    if (!card) return;
    const interactive = event.target.closest('a, button');
    if (interactive && !interactive.hasAttribute('data-flip-toggle')) return;
    card.classList.toggle('is-flipped');
    updateKnowledgeProgress();
  });

  // 键盘可达：卡片获得焦点后按 Enter / 空格翻面
  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    const card = event.target.closest('.flip');
    if (!card || event.target.closest('button, a')) return;
    event.preventDefault();
    card.classList.toggle('is-flipped');
    updateKnowledgeProgress();
  });
}

/* 人物卡背面：「档案比卡片长」的那几张要能滚动。
   卡片高度已统一（.person-card 的 --person-h），这里只在真的溢出时挂
   is-overflowing，让底部渐隐与「余下内容可滚动查看」出现，滚到底再挂
   is-at-end 收掉渐隐。没溢出就什么都不做 —— 不谎报「可滚动」。 */
function initFlipOverflow() {
  const boxes = Array.from(document.querySelectorAll('.person-card .flip-back-scroll'));
  if (!boxes.length) return;

  const measure = () => {
    boxes.forEach((box) => {
      const rest = box.scrollHeight - box.clientHeight - box.scrollTop;
      const over = box.scrollHeight - box.clientHeight > 2;
      box.classList.toggle('is-overflowing', over);
      box.classList.toggle('is-at-end', over && rest <= 4);
    });
  };

  boxes.forEach((box) => {
    box.addEventListener('scroll', () => {
      const rest = box.scrollHeight - box.clientHeight - box.scrollTop;
      box.classList.toggle('is-at-end', rest <= 4);
    }, { passive: true });
  });

  measure();
  // 字体加载完换行会变，重量一次；窗口尺寸变化同理
  let raf = 0;
  window.addEventListener('resize', () => {
    cancelAnimationFrame(raf);
    raf = requestAnimationFrame(measure);
  });
  if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(measure).catch(() => {});
  }
}

function activeKnowledgePanel() {
  return document.querySelector('.kb-panel.is-active');
}

function updateKnowledgeProgress() {
  const hint = document.querySelector('[data-kb-progress]');
  if (!hint) return;
  const panel = activeKnowledgePanel();
  if (!panel) { hint.textContent = ''; return; }
  const total = panel.querySelectorAll('.flip').length;
  const flipped = panel.querySelectorAll('.flip.is-flipped').length;
  hint.textContent = total ? `本主题 ${total} 张 · 已翻面 ${flipped} 张` : '';
}

function initKnowledgeTabs() {
  const tabs = Array.from(document.querySelectorAll('[data-kb-tab]'));
  const panels = Array.from(document.querySelectorAll('[data-kb-panel]'));
  if (!tabs.length || !panels.length) return;

  const activate = (key) => {
    tabs.forEach((tab) => {
      const on = tab.dataset.kbTab === key;
      tab.classList.toggle('is-active', on);
      tab.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    panels.forEach((panel) => panel.classList.toggle('is-active', panel.dataset.kbPanel === key));
    updateKnowledgeProgress();
  };

  tabs.forEach((tab) => tab.addEventListener('click', () => activate(tab.dataset.kbTab)));

  const initial = tabs.find((tab) => tab.classList.contains('is-active')) || tabs[0];
  activate(initial.dataset.kbTab);
}

function initKnowledgeTools() {
  const panel = () => activeKnowledgePanel();

  const shuffle = document.querySelector('[data-kb-shuffle]');
  if (shuffle) {
    shuffle.addEventListener('click', () => {
      const grid = panel()?.querySelector('.card-grid');
      if (!grid) return;
      const cards = Array.from(grid.children);
      for (let i = cards.length - 1; i > 0; i -= 1) {
        const j = Math.floor(Math.random() * (i + 1));
        [cards[i], cards[j]] = [cards[j], cards[i]];
      }
      cards.forEach((card, index) => {
        card.style.setProperty('--i', index);
        card.classList.remove('is-flipped');
        grid.appendChild(card);
      });
      if (MOTION_OK && !prefersReducedMotion) {
        cards.forEach((card) => card.classList.remove('is-visible'));
        requestAnimationFrame(() => {
          cards.forEach((card) => card.classList.add('is-visible'));
        });
      }
      toast('已重新洗牌', 'info', 1800);
      updateKnowledgeProgress();
    });
  }

  const revealAll = document.querySelector('[data-kb-reveal-all]');
  if (revealAll) {
    revealAll.addEventListener('click', () => {
      panel()?.querySelectorAll('.flip').forEach((card) => card.classList.add('is-flipped'));
      updateKnowledgeProgress();
    });
  }

  const hideAll = document.querySelector('[data-kb-hide-all]');
  if (hideAll) {
    hideAll.addEventListener('click', () => {
      panel()?.querySelectorAll('.flip').forEach((card) => card.classList.remove('is-flipped'));
      updateKnowledgeProgress();
    });
  }
}

/* --- 知识库章节目录（侧栏二级导航）-----------------------------
   这组锚点由服务端按 active_nav 决定是否渲染，只在知识库页面存在。
   脚本负责两件原生 <a href="#id"> 做不到的事：
     1) 跳转时避开吸顶的顶栏（原生锚点会把标题压到顶栏底下）；
     2) 滚动时把「当前读到的章节」标出来。
   注意：这里刻意不做滚动劫持 —— 跳过去之后用户想怎么滚就怎么滚。 */
function initSectionNav() {
  const links = Array.from(document.querySelectorAll('.nav-sub-item'));
  if (!links.length) return;

  const pairs = links
    .map((link) => {
      const id = (link.getAttribute('href') || '').replace('#', '');
      const section = id ? document.getElementById(id) : null;
      return section ? { link, section } : null;
    })
    .filter(Boolean);
  if (!pairs.length) return;

  const TOPBAR_OFFSET = 78; // 吸顶顶栏高度 + 一点余量

  const setCurrent = (activeLink) => {
    links.forEach((link) => link.classList.toggle('is-current', link === activeLink));
  };

  pairs.forEach(({ link, section }) => {
    link.addEventListener('click', (event) => {
      event.preventDefault();
      const top = section.getBoundingClientRect().top + window.pageYOffset - TOPBAR_OFFSET;
      window.scrollTo({ top, behavior: NO_MOTION ? 'auto' : 'smooth' });
      // 用 replaceState 记住位置，但不额外写一条历史记录
      if (window.history && history.replaceState) {
        history.replaceState(null, '', link.getAttribute('href'));
      }
      // 立即把锚点标为当前，避免平滑滚动途中高亮还停在上一章
      setCurrent(link);
    });
  });

  // 滚动高亮：取最后一个已经越过参考线的区块
  let lastRun = 0;
  const sync = () => {
    const now = performance.now();
    if (now - lastRun < 80) return;
    lastRun = now;
    const line = window.innerHeight * 0.3;
    let current = pairs[0];
    pairs.forEach((pair) => {
      if (pair.section.getBoundingClientRect().top <= line) current = pair;
    });
    setCurrent(current.link);
  };
  window.addEventListener('scroll', sync, { passive: true });
  window.addEventListener('resize', sync, { passive: true });
  sync();
}

/* --- 顶栏账户菜单 ---------------------------------------------
   展开/收起由原生 <details> 负责，因此脚本失效时菜单依然可用。
   这里只补两件原生做不到的事：点外部关闭、按 Esc 关闭并交还焦点。 */
function initUserMenu() {
  const menu = document.getElementById('user-menu');
  if (!menu) return;

  document.addEventListener('click', (event) => {
    if (menu.open && !menu.contains(event.target)) menu.open = false;
  });

  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape' || !menu.open) return;
    menu.open = false;
    const summary = menu.querySelector('summary');
    if (summary) summary.focus();   // 关闭后焦点不能丢在文档流里
  });

  // 面板内点击链接会跳转，菜单不该留在展开态
  menu.querySelectorAll('.user-panel-links a').forEach((link) => {
    link.addEventListener('click', () => { menu.open = false; });
  });
}

/* --- 首页横幅轮播 ---------------------------------------------
   图与图注一起换。四条约束：
   1. 脚本失效时首图仍然可见 —— CSS 里只有容器带上 .is-slideshow 才会
      把图片改成绝对定位叠加，所以这里先加类、再接管切换；
   2. prefers-reduced-motion / 放映模式下不自动播放，只保留手动切图；
   3. 切到后台或鼠标悬停时暂停，避免切回来连着翻好几张；
   4. 轮播只动 opacity 与 transform，不触发布局。 */
const HERO_INTERVAL = 6200;

function initHeroSlides() {
  const box = document.querySelector('[data-hero-slides]');
  if (!box) return;
  const slides = Array.from(box.querySelectorAll('[data-hero-slide]'));
  if (slides.length < 2) return;

  const caption = box.querySelector('[data-hero-caption]');
  const dotsBox = box.querySelector('[data-hero-dots]');
  const dots = [];
  let current = -1;
  let timer = null;

  function show(index) {
    current = index;
    slides.forEach((slide, i) => slide.classList.toggle('is-active', i === index));
    dots.forEach((dot, i) => dot.classList.toggle('is-active', i === index));
    if (caption) {
      caption.textContent = slides[index].dataset.caption || '';
      caption.classList.remove('is-in');
      void caption.offsetWidth;          // 强制回流，让淡入每次都能重放
      caption.classList.add('is-in');
    }
  }

  function stop() {
    if (timer) { clearInterval(timer); timer = null; }
  }

  function restart() {
    stop();
    if (NO_MOTION) return;               // 减少动效：不自动播，但点圆点仍可切
    timer = setInterval(() => show((current + 1) % slides.length), HERO_INTERVAL);
  }

  // 图注圆点：原生 <button>，无脚本时不存在，因此也不会留下不可用的控件
  if (dotsBox) {
    slides.forEach((slide, index) => {
      const dot = document.createElement('button');
      dot.type = 'button';
      dot.className = 'hero-dot';
      dot.setAttribute('aria-label', `切换到第 ${index + 1} 张：${slide.dataset.caption || ''}`);
      dot.addEventListener('click', () => { show(index); restart(); });
      dotsBox.appendChild(dot);
      dots.push(dot);
    });
  }

  box.classList.add('is-slideshow');
  show(0);
  restart();

  box.addEventListener('pointerenter', stop);
  box.addEventListener('pointerleave', restart);
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) stop();
    else if (!timer) restart();
  });
}

/* --- 初始化 --------------------------------------------------- */
function initMotion() {
  initReveal();
  initHeroSlides();
  initUserMenu();
  initCounters();
  initScoreRings();
  initProgressBars();
  initTimelineProgress();
  initRipple();
  initFlipCards();
  initFlipOverflow();
  initKnowledgeTabs();
  initKnowledgeTools();
  initSectionNav();
  updateKnowledgeProgress();

  // 告诉 _head.html 里的看门狗：脚本已经跑起来了，可以保留 html.anim
  document.documentElement.classList.add('anim-ready');
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initMotion);
} else {
  initMotion();
}

// 供页面内联脚本调用（结果页庆祝效果等）
window.QM = { celebrate, toast, countUp };
