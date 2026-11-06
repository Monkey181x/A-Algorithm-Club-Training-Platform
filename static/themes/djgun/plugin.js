/* Optional decoration only. No API calls, settings writes or native node edits. */
(() => {
  'use strict';
  const root = document.documentElement;
  const base = new URL('assets/', document.currentScript.src);
  const urls = Array.from({length: 6}, (_, i) => new URL(`expression-${i + 1}.png`, base).href);
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  let layers = [], timer, loading, generation = 0, current = 0, front = 0, lastState;
  const enabled = () => root.dataset.specialTheme === 'djgun';
  const preload = () => loading ||= Promise.all(urls.map(src => new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = async () => { try { await image.decode?.(); resolve(image); } catch (error) { reject(error); } };
    image.onerror = reject;
    image.src = src;
  }))).catch(error => { loading = null; throw error; });
  function stop() {
    generation++;
    clearInterval(timer);
    timer = null;
    layers.forEach(image => image.remove());
    layers = [];
  }
  async function refresh() {
    // The native appearance engine rewrites these attributes on every paint.
    // Ignore equal values, otherwise adding our images could cause a repaint loop.
    const state = [root.dataset.specialTheme, root.dataset.siteMotion, reduced.matches].join('|');
    if (state === lastState) return;
    lastState = state;
    stop();
    if (!enabled()) return;
    const stage = document.querySelector('.atlas-stage');
    if (!stage) return;
    const token = generation;
    try { await preload(); } catch { if (generation === token) lastState = undefined; return; }
    if (generation !== token || !enabled()) return;
    current = 0; front = 0;
    layers = [0, 1].map(i => {
      const image = document.createElement('img');
      image.className = 'djgun-expression';
      image.width = image.height = 140;
      image.alt = '';
      image.setAttribute('aria-hidden', 'true');
      image.draggable = false;
      image.src = urls[i];
      image.dataset.visible = String(i === 0);
      stage.append(image);
      return image;
    });
    if (reduced.matches || root.dataset.siteMotion === 'off') return;
    timer = setInterval(async () => {
      if (document.hidden) return;
      const back = 1 - front;
      const next = (current + 1) % urls.length;
      layers[back].src = urls[next];
      try { await layers[back].decode(); } catch { return; }
      if (generation !== token) return;
      layers[back].dataset.visible = 'true';
      layers[front].dataset.visible = 'false';
      current = next; front = back;
    }, 1800);
  }
  window.addEventListener('site-special-theme-change', refresh);
  reduced.addEventListener('change', refresh);
  new MutationObserver(records => {
    if (records.some(record => ['data-special-theme', 'data-site-motion'].includes(record.attributeName))) refresh();
  }).observe(root, {attributes: true, attributeFilter: ['data-special-theme', 'data-site-motion']});
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', refresh, {once: true});
  else refresh();
})();
