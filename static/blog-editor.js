(() => {
  let submissionEditor;

  function bindEditor(root) {
    const input = root.querySelector('.blog-markdown-input');
    const preview = root.querySelector('.blog-preview');
    const tabs = [...root.querySelectorAll('.blog-editor-tab')];
    const panes = [...root.querySelectorAll('.blog-editor-pane')];
    let previewTimeout;

    function renderPreview() {
      const markdown = input.value.trim();
      if (!markdown) {
        preview.innerHTML = '<p class="preview-placeholder">预览将显示在这里...</p>';
        return;
      }
      if (typeof renderSubmissionBlog === 'function') renderSubmissionBlog(preview, markdown);
      else preview.textContent = markdown;
    }

    function selectTab(targetTab) {
      tabs.forEach(tab => tab.classList.toggle('active', tab.dataset.tab === targetTab));
      panes.forEach(pane => pane.classList.toggle('active', pane.dataset.pane === targetTab));
      if (targetTab === 'preview') renderPreview();
    }

    function insertMarkdown(action) {
      const textarea = input;
      const start = textarea.selectionStart;
      const end = textarea.selectionEnd;
      const selectedText = textarea.value.substring(start, end);
      let replacement = '';
      let cursorOffset = 0;

      switch (action) {
        case 'bold':
          replacement = `**${selectedText || '粗体文本'}**`;
          cursorOffset = selectedText ? replacement.length : 2;
          break;
        case 'italic':
          replacement = `*${selectedText || '斜体文本'}*`;
          cursorOffset = selectedText ? replacement.length : 1;
          break;
        case 'heading':
          replacement = `\n## ${selectedText || '标题'}\n`;
          cursorOffset = selectedText ? replacement.length : 3;
          break;
        case 'code':
          if (selectedText.includes('\n')) {
            replacement = `\`\`\`\n${selectedText || '代码块'}\n\`\`\``;
            cursorOffset = selectedText ? replacement.length : 4;
          } else {
            replacement = `\`${selectedText || '代码'}\``;
            cursorOffset = selectedText ? replacement.length : 1;
          }
          break;
        case 'link':
          replacement = `[${selectedText || '链接文本'}](https://)`;
          cursorOffset = selectedText ? replacement.length - 1 : replacement.indexOf('https://') + 8;
          break;
        case 'image':
          replacement = `![${selectedText || '图片描述'}](https://)`;
          cursorOffset = selectedText ? replacement.length - 1 : replacement.indexOf('https://') + 8;
          break;
        case 'list':
          replacement = `\n- ${selectedText || '列表项'}\n`;
          cursorOffset = selectedText ? replacement.length : 2;
          break;
        case 'quote':
          replacement = `\n> ${selectedText || '引用内容'}\n`;
          cursorOffset = selectedText ? replacement.length : 2;
          break;
        case 'math':
          replacement = `$$\n${selectedText || '\\sum_{i=1}^{n} i'}\n$$`;
          cursorOffset = selectedText ? replacement.length : 3;
          break;
        case 'table':
          replacement = `\n| 列1 | 列2 | 列3 |\n|-----|-----|-----|\n| 内容 | 内容 | 内容 |\n`;
          cursorOffset = 2;
          break;
      }

      textarea.value = textarea.value.substring(0, start) + replacement + textarea.value.substring(end);
      textarea.dispatchEvent(new Event('input', {bubbles: true}));
      textarea.focus();
      textarea.setSelectionRange(start + cursorOffset, start + cursorOffset);
    }

    tabs.forEach(tab => tab.addEventListener('click', () => selectTab(tab.dataset.tab)));
    root.querySelectorAll('.toolbar-btn').forEach(button => {
      button.addEventListener('click', () => insertMarkdown(button.dataset.action));
    });
    input.addEventListener('input', () => {
      clearTimeout(previewTimeout);
      if (root.querySelector('[data-tab="preview"]').classList.contains('active')) {
        previewTimeout = setTimeout(renderPreview, 500);
      }
    });

    return {input, selectTab};
  }

  function initSubmissionEditor() {
    const openButton = document.getElementById('blogOpenButton');
    const overlay = document.getElementById('blogEditorOverlay');
    if (!openButton || !overlay) return;
    document.body.append(overlay);

    if (typeof marked !== 'undefined') {
      marked.setOptions({breaks: true, gfm: true, headerIds: false, mangle: false});
    }

    submissionEditor = bindEditor(overlay);
    openButton.addEventListener('click', () => {
      overlay.setAttribute('aria-hidden', 'false');
      document.body.style.overflow = 'hidden';
      setTimeout(() => submissionEditor.input.focus(), 350);
    });

    function closeEditor() {
      overlay.setAttribute('aria-hidden', 'true');
      document.body.style.overflow = '';
    }

    ['blogEditorClose', 'blogEditorCancel', 'blogEditorSave'].forEach(id => {
      overlay.querySelector('#' + id).addEventListener('click', closeEditor);
    });
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && overlay.getAttribute('aria-hidden') === 'false') closeEditor();
    });
  }

  window.openMarkdownEditor = ({content, onSave}) => {
    const source = document.querySelector('#blogEditorOverlay .blog-editor-modal');
    const dialog = document.createElement('dialog');
    dialog.id = 'dataStructureBlogEditor';
    dialog.className = source.className;
    dialog.setAttribute('aria-label', 'Markdown 编辑器');
    dialog.append(...source.cloneNode(true).children);
    const close = dialog.querySelector('#blogEditorClose');
    const cancel = dialog.querySelector('#blogEditorCancel');
    const save = dialog.querySelector('#blogEditorSave');
    dialog.querySelectorAll('[id]').forEach(element => element.removeAttribute('id'));

    const {input, selectTab} = bindEditor(dialog);
    input.value = content;
    input.maxLength = 100000;
    selectTab('write');
    const error = document.createElement('p');
    error.className = 'data-structure-error';
    error.setAttribute('role', 'alert');
    dialog.querySelector('.blog-editor-footer').before(error);
    close.onclick = cancel.onclick = () => dialog.close();
    save.onclick = () => {
      const markdown = input.value.trim();
      if (markdown.length > input.maxLength) {
        error.textContent = 'Blog 最多 100000 字';
        return;
      }
      onSave(markdown);
      dialog.close();
    };
    dialog.addEventListener('close', () => dialog.remove(), {once: true});
    document.body.append(dialog);
    dialog.showModal();
    input.focus();
  };

  window.getBlogEditorContent = () => document.getElementById('blogMarkdownInput')?.value.trim() || '';

  window.setBlogEditorContent = content => {
    const input = document.getElementById('blogMarkdownInput');
    if (input) input.value = content || '';
  };

  window.clearBlogEditor = () => {
    window.setBlogEditorContent('');
    submissionEditor?.selectTab('write');
    const preview = document.getElementById('blogPreview');
    if (preview) preview.innerHTML = '<p class="preview-placeholder">预览将显示在这里...</p>';
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initSubmissionEditor);
  } else {
    initSubmissionEditor();
  }
})();
