/* SPDX-License-Identifier: Apache-2.0 */
(() => {
  'use strict';
  const title = document.getElementById('hero-title');
  const tagline = document.querySelector('.hero-tagline');
  const hero = document.querySelector('.hero');
  if (!title || !tagline || !hero) return;

  const phrases = {
    en:['Your models. ', 'Your silicon. ', 'Your infrastructure.'],
    'zh-Hant':['模型、', '晶片、', '基礎設施，由你掌握。'],
    'zh-Hans':['模型、', '芯片、', '基础设施，由你掌握。'],
    ja:['モデルも、', 'シリコンも、', 'インフラも、あなたの手に。']
  };
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
  const finePointer = window.matchMedia('(hover: hover) and (pointer: fine)');
  const narrow = window.matchMedia('(max-width: 600px)');
  const entranceMotions = new Set();
  const phraseMotions = new Set();
  let entranceStarted = false;
  let disabledByControl = false;
  const motionAllowed = () => !disabledByControl && !reduced.matches && !document.hidden;
  const pointerAllowed = () => motionAllowed() && finePointer.matches && !narrow.matches;
  const controlMotions = new Map();
  const pendingCloses = new Map();
  let themeTimer = 0;
  function cancelControl(element) {
    const animation = controlMotions.get(element);
    if (!animation) return;
    controlMotions.delete(element);
    element.style.removeProperty('will-change');
    animation.cancel();
  }
  function animateControl(element, frames, duration) {
    if (!element) return;
    cancelControl(element);
    if (!motionAllowed() || typeof element.animate !== 'function') return;
    element.style.willChange = 'opacity,transform';
    const animation = element.animate(frames, {duration, easing:'cubic-bezier(.22,1,.36,1)'});
    controlMotions.set(element, animation);
    const clean = () => {
      if (controlMotions.get(element) === animation) {
        controlMotions.delete(element);
        element.style.removeProperty('will-change');
      }
    };
    animation.addEventListener('cancel', clean, {once:true});
    animation.addEventListener('finish', () => { clean(); animation.cancel(); }, {once:true});
  }
  function animateLanguageMenu(menu) {
    if (!menu || menu.hidden) return;
    const from = menuFrame(menu, {opacity:0, transform:'translateY(-4px)'});
    cancelLanguageMenu(menu);
    animateControl(menu, [from, {opacity:1, transform:'translateY(0)'}], 170);
  }
  function menuFrame(menu, fallback) {
    if (!controlMotions.has(menu) || typeof window.getComputedStyle !== 'function') return fallback;
    const style = window.getComputedStyle(menu);
    return {opacity:style.opacity, transform:style.transform};
  }
  function cancelLanguageMenu(menu) {
    pendingCloses.delete(menu);
    cancelControl(menu);
  }
  function closeLanguageMenu(menu, finish) {
    const from = menuFrame(menu, {opacity:1, transform:'translateY(0)'});
    cancelLanguageMenu(menu);
    if (!motionAllowed() || typeof menu.animate !== 'function' || menu.hidden) { finish(); return; }
    pendingCloses.set(menu, finish);
    animateControl(menu, [from, {opacity:0, transform:'translateY(-3px)'}], 120);
    controlMotions.get(menu).addEventListener('finish', () => {
      if (pendingCloses.get(menu) !== finish) return;
      pendingCloses.delete(menu);
      finish();
    }, {once:true});
  }
  function stopThemeTransition() {
    clearTimeout(themeTimer);
    themeTimer = 0;
    document.documentElement.classList.remove('theme-transition');
  }
  function transitionTheme(change) {
    clearTimeout(themeTimer);
    document.querySelectorAll('#theme-toggle .theme-icon').forEach(cancelControl);
    if (!motionAllowed()) { stopThemeTransition(); change(); return; }
    document.documentElement.classList.add('theme-transition');
    change();
    const icon = document.querySelector(document.documentElement.getAttribute('data-theme') === 'dark' ? '.theme-icon-sun' : '.theme-icon-moon');
    animateControl(icon, [{opacity:.35, transform:'rotate(-12deg)'}, {opacity:1, transform:'rotate(0deg)'}], 180);
    themeTimer = setTimeout(stopThemeTransition, 260);
  }

  function entrance(element, frames, duration, delay, phrase = false) {
    if (typeof element.animate !== 'function') return;
    element.style.willChange = phrase ? 'opacity,top' : 'opacity,filter,transform';
    const animation = element.animate(frames, {
      duration, delay, easing:'cubic-bezier(.22,1,.36,1)', fill:'backwards'
    });
    entranceMotions.add(animation);
    if (phrase) phraseMotions.add(animation);
    const clean = () => {
      entranceMotions.delete(animation);
      phraseMotions.delete(animation);
      element.style.removeProperty('will-change');
    };
    animation.addEventListener('cancel', clean, {once:true});
    animation.addEventListener('finish', () => { clean(); animation.cancel(); }, {once:true});
  }
  function setTagline(locale, text) {
    const parts = phrases[locale];
    if (!parts || parts.join('') !== text) return;
    Array.from(phraseMotions).forEach(animation => animation.cancel());
    const layers = parts.map(part => {
      const phrase = document.createElement('span');
      phrase.className = 'hero-tagline-phrase';
      const inner = document.createElement('span');
      inner.className = 'hero-tagline-phrase-inner';
      inner.textContent = part;
      phrase.appendChild(inner);
      return phrase;
    });
    tagline.replaceChildren(...layers);
    if (entranceStarted) return;
    entranceStarted = true;
    if (!motionAllowed()) return;
    entrance(title, [
      {opacity:0, filter:'blur(4px)', transform:'translateY(4px)'},
      {opacity:1, filter:'blur(0)', transform:'translateY(0)'}
    ], 500, 0);
    layers.forEach((phrase, index) => entrance(phrase.firstElementChild, [
      {opacity:0, top:'6px'}, {opacity:1, top:'0px'}
    ], 460, 220 + index * 120, true));
  }

  const surfaces = [];
  const pending = new Set();
  let frame = 0;
  function render() {
    frame = 0;
    if (!pointerAllowed()) { resetPointers(true); return; }
    pending.forEach(surface => {
      const {element, rect, x, y, limit} = surface;
      if (!rect || !surface.inside) return;
      const nx = Math.max(-1, Math.min(1, (x - rect.left) / rect.width * 2 - 1));
      const ny = Math.max(-1, Math.min(1, (y - rect.top) / rect.height * 2 - 1));
      element.style.setProperty('--polish-pointer-x', `${x - rect.left}px`);
      element.style.setProperty('--polish-pointer-y', `${y - rect.top}px`);
      const angle = Math.min(1, Math.hypot(nx, ny)) * limit;
      element.style.rotate = angle ? `${-ny} ${nx} 0 ${angle}deg` : '0deg';
    });
    pending.clear();
  }
  function schedule(surface, event) {
    if (!pointerAllowed() || !surface.inside || event.pointerType === 'touch') return;
    surface.x = event.clientX;
    surface.y = event.clientY;
    pending.add(surface);
    if (!frame) frame = window.requestAnimationFrame(render);
  }
  function reset(surface, immediate = false) {
    if (!surface.inside && !immediate) return;
    pending.delete(surface);
    surface.inside = false;
    surface.rect = null;
    clearTimeout(surface.resetTimer);
    surface.resetTimer = 0;
    const element = surface.element;
    element.classList.remove('polish-pointer-active');
    element.classList.toggle('polish-pointer-resting', !immediate);
    if (immediate) {
      element.style.removeProperty('rotate');
      element.style.removeProperty('will-change');
      element.style.removeProperty('--polish-pointer-x');
      element.style.removeProperty('--polish-pointer-y');
    } else {
      element.style.rotate = '0deg';
      surface.resetTimer = setTimeout(() => {
        element.classList.remove('polish-pointer-resting');
        element.style.removeProperty('rotate');
        element.style.removeProperty('will-change');
        element.style.removeProperty('--polish-pointer-x');
        element.style.removeProperty('--polish-pointer-y');
        surface.resetTimer = 0;
      }, 300);
    }
  }
  function resetPointers(immediate = false) {
    surfaces.forEach(surface => reset(surface, immediate));
    if (frame) window.cancelAnimationFrame(frame);
    frame = 0;
    pending.clear();
  }
  function pointerSurface(target, element, limit) {
    const surface = {target, element, limit, rect:null, inside:false, resetTimer:0};
    surfaces.push(surface);
    if (limit) element.classList.add('polish-tilt');
    target.addEventListener('pointerenter', event => {
      if (!pointerAllowed() || event.pointerType === 'touch') return;
      clearTimeout(surface.resetTimer);
      surface.rect = target.getBoundingClientRect();
      surface.inside = true;
      element.classList.remove('polish-pointer-resting');
      element.classList.add('polish-pointer-active');
      element.style.willChange = 'rotate';
      schedule(surface, event);
    });
    target.addEventListener('pointermove', event => schedule(surface, event), {passive:true});
    target.addEventListener('pointerleave', () => {
      reset(surface);
      if (!pending.size && frame) { window.cancelAnimationFrame(frame); frame = 0; }
    });
  }
  document.querySelectorAll('.feature').forEach(element => pointerSurface(element, element, 1.25));
  document.querySelectorAll('.hero-actions .button,.nav-cta').forEach(element => pointerSurface(element, element, .75));
  window.addEventListener('resize', () => {
    if (!pointerAllowed()) { resetPointers(true); return; }
    surfaces.forEach(surface => { if (surface.inside) surface.rect = surface.target.getBoundingClientRect(); });
  }, {passive:true});
  window.addEventListener('scroll', () => resetPointers(), {passive:true});
  window.addEventListener('blur', () => resetPointers(true));
  function alignReturnPath() {
    const svg = document.querySelector('.topology-wires');
    const client = document.querySelector('.client-node');
    const metal = document.querySelector('.metal-node');
    if (!svg || !client || !metal || typeof svg.getScreenCTM !== 'function') return;
    const wire = svg.querySelector('.return-wire');
    const packet = svg.querySelector('.return-packet');
    if (!wire || !packet) return;
    let geometryFrame = 0;
    function update() {
      geometryFrame = 0;
      if (narrow.matches || document.hidden) return;
      const matrix = svg.getScreenCTM();
      const clientRect = client.getBoundingClientRect();
      const metalRect = metal.getBoundingClientRect();
      if (!matrix || !clientRect.width || !clientRect.height || !metalRect.width || !metalRect.height) return;
      let inverse;
      try { inverse = matrix.inverse(); } catch { return; }
      const toSVG = (x, y) => ({x:inverse.a*x + inverse.c*y + inverse.e, y:inverse.b*x + inverse.d*y + inverse.f});
      const clientX = clientRect.left + clientRect.width / 2;
      const metalX = metalRect.left + metalRect.width / 2;
      const end = toSVG(clientX, clientRect.bottom);
      const start = toSVG(metalX, metalRect.bottom);
      const baselineLane = matrix.b*550 + matrix.d*268 + matrix.f;
      const laneScreen = Math.max(baselineLane, clientRect.bottom + 12, metalRect.bottom + 12);
      const lane = toSVG((clientX + metalX) / 2, laneScreen).y;
      if (![start.x, start.y, end.x, end.y, lane].every(Number.isFinite)) return;
      const number = value => Number(value.toFixed(3));
      const path = `M${number(start.x)} ${number(start.y)}V${number(lane)}H${number(end.x)}V${number(end.y)}`;
      if (wire.getAttribute('d') !== path) wire.setAttribute('d', path);
      if (packet.getAttribute('d') !== path) packet.setAttribute('d', path);
    }
    function scheduleGeometry() {
      if (!narrow.matches && !document.hidden && !geometryFrame) geometryFrame = window.requestAnimationFrame(update);
    }
    if (typeof ResizeObserver === 'function') {
      const observer = new ResizeObserver(scheduleGeometry);
      [svg, svg.parentElement, client, metal].forEach(element => { if (element) observer.observe(element); });
    }
    window.addEventListener('resize', scheduleGeometry, {passive:true});
    window.addEventListener('load', scheduleGeometry, {once:true});
    document.addEventListener('visibilitychange', scheduleGeometry);
    if (narrow.addEventListener) narrow.addEventListener('change', scheduleGeometry);
    else if (narrow.addListener) narrow.addListener(scheduleGeometry);
    if (document.fonts) {
      if (document.fonts.ready) document.fonts.ready.then(scheduleGeometry);
      if (document.fonts.addEventListener) document.fonts.addEventListener('loadingdone', scheduleGeometry);
    }
    scheduleGeometry();
  }
  alignReturnPath();
  const Backdrop = (() => {
    const layer = document.createElement('div');
    layer.className = 'welcome-backdrop';
    layer.setAttribute('aria-hidden', 'true');
    ['backdrop-fold-a', 'backdrop-fold-b'].forEach(name => {
      const fold = document.createElement('span');
      fold.className = name;
      layer.appendChild(fold);
    });
    hero.prepend(layer);
    hero.classList.add('backdrop-enabled');
    let visible = true;
    function refresh() {
      layer.classList.toggle('backdrop-paused', !visible || !motionAllowed());
    }
    if (typeof IntersectionObserver === 'function') {
      const observer = new IntersectionObserver(entries => {
        entries.forEach(entry => { if (entry.target === hero) visible = entry.isIntersecting; });
        refresh();
      }, {threshold:0});
      observer.observe(hero);
    }
    refresh();
    return {refresh};
  })();
  // Presentation-only header state; no polling or scroll-frame work.
  function initHeaderGlass() {
    const nav = document.querySelector('.nav');
    if (!nav) return;
    let scrolled = null;
    function syncScroll() {
      const next = window.scrollY > 12;
      if (next !== scrolled) {
        scrolled = next;
        nav.classList.toggle('nav-scrolled', next);
      }
    }
    function syncWidth() {
      const width = document.documentElement.clientWidth;
      if (Number.isFinite(width) && width > 0) nav.style.setProperty('--nav-glass-width', `${width}px`);
    }
    window.addEventListener('scroll', syncScroll, {passive:true});
    window.addEventListener('resize', syncWidth, {passive:true});
    syncWidth();
    syncScroll();
  }
  initHeaderGlass();
  function updateAvailability() {
    Backdrop.refresh();
    if (!motionAllowed()) {
      Array.from(entranceMotions).forEach(animation => animation.cancel());
      Array.from(pendingCloses.entries()).forEach(([menu, finish]) => {
        pendingCloses.delete(menu);
        cancelControl(menu);
        finish();
      });
      Array.from(controlMotions.keys()).forEach(cancelControl);
      stopThemeTransition();
    }
    if (!pointerAllowed()) resetPointers(true);
  }
  [reduced, finePointer, narrow].forEach(media => {
    if (media.addEventListener) media.addEventListener('change', updateAvailability);
    else if (media.addListener) media.addListener(updateAvailability);
  });
  document.addEventListener('visibilitychange', updateAvailability);
  function setMotionDisabled(disabled) {
    disabledByControl = disabled;
    updateAvailability();
  }
  window.WelcomePolish = Object.freeze({setTagline, setMotionDisabled, animateLanguageMenu, cancelLanguageMenu, closeLanguageMenu, transitionTheme});
})();
