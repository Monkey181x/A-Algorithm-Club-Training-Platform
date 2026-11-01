/* Decoded, page-local image reuse. Private image responses remain no-store. */
(() => {
  const cache = new Map();
  const limit = 64;

  function decode(image) {
    return new Promise((resolve, reject) => {
      const loaded = async () => {
        try {
          if (!image.naturalWidth) throw Error('Empty image');
          if (image.decode) await image.decode();
          resolve();
        } catch (error) { reject(error); }
      };
      image.onload = loaded;
      image.onerror = () => reject(Error('Image unavailable'));
      if (image.complete && image.naturalWidth) loaded();
    }).finally(() => { image.onload = null; image.onerror = null; });
  }

  function trim() {
    for (const [source, entry] of cache) {
      if (cache.size <= limit) break;
      if (entry.nodes.some(node => node.image.isConnected)) continue;
      entry.controller.abort();
      if (entry.url) URL.revokeObjectURL(entry.url);
      cache.delete(source);
    }
  }

  function acquire(source) {
    let entry = cache.get(source);
    if (!entry) {
      entry = {nodes: [], controller: new AbortController()};
      cache.set(source, entry);
      const timeout = setTimeout(() => entry.controller.abort(), 60000);
      // Fetch once per URL, including duplicate products in the backpack.
      entry.promise = fetch(source, {credentials: 'same-origin', signal: entry.controller.signal})
        .then(response => {
          if (!response.ok) throw Error('Image unavailable');
          return response.blob();
        }).then(blob => {
          entry.url = URL.createObjectURL(blob);
          return entry.url;
        }).catch(error => {
          // A subsequent visit can retry a temporary failure.
          if (cache.get(source) === entry) cache.delete(source);
          throw error;
        }).finally(() => clearTimeout(timeout));
    } else {
      cache.delete(source);
      cache.set(source, entry);
    }
    let node = entry.nodes.find(node => !node.image.isConnected);
    if (!node) {
      const image = new Image();
      image.className = 'shop-product-image';
      image.decoding = 'async';
      node = {image, ready: false};
      entry.nodes.push(node);
      node.promise = entry.promise.then(async url => {
        const decoded = decode(image);
        image.src = url;
        await decoded;
        node.ready = true;
      }).catch(error => {
        if (cache.get(source) === entry) cache.delete(source);
        if (entry.url) { URL.revokeObjectURL(entry.url); entry.url = null; }
        throw error;
      });
    }
    return node;
  }

  function mount(container) {
    container.querySelectorAll('[data-shop-image]').forEach(slot => {
      if (slot.querySelector('.shop-product-image')) return;
      const node = acquire(slot.dataset.shopImage);
      const image = node.image;
      image.alt = slot.dataset.imageAlt || '';
      slot.append(image);
      const reveal = () => {
        if (!slot.isConnected || image.parentElement !== slot) return;
        slot.classList.add('is-image-ready');
        slot.classList.remove('is-image-loading');
        slot.removeAttribute('aria-busy');
      };
      if (node.ready) {
        slot.classList.add('is-image-cached');
        reveal();
      } else {
        node.promise.then(reveal, () => {
          if (!slot.isConnected || image.parentElement !== slot) return;
          image.remove();
          slot.classList.remove('is-image-loading');
          slot.classList.add('is-image-error');
          slot.removeAttribute('aria-busy');
          const fallback = document.createElement('span');
          fallback.className = 'shop-image-error';
          fallback.textContent = '图片暂不可用';
          fallback.setAttribute('role', 'img');
          fallback.setAttribute('aria-label', `${slot.dataset.imageAlt || '商品'}：图片暂不可用`);
          slot.append(fallback);
        });
      }
    });
    trim();
  }

  window.ShopImages = {mount};
})();
