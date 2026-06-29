/* =========================================================
   WHATSAPP AUTOMATION APP — INTERACTIONS
   Minimal vanilla JS for prototype interactions
   ========================================================= */

(function() {
  'use strict';

  // --- Sidebar active state based on current page ---
  const currentPage = window.location.pathname.split('/').pop() || 'index.html';
  document.querySelectorAll('.nav-item').forEach(link => {
    const href = link.getAttribute('href');
    if (href && href.includes(currentPage)) {
      link.classList.add('active');
    }
  });

  // --- Command Palette (Ctrl+K / Cmd+K) ---
  const palette = document.getElementById('commandPalette');
  const paletteInput = document.getElementById('cpInput');
  const paletteOverlay = document.getElementById('paletteOverlay');

  function openPalette() {
    if (!palette) return;
    palette.classList.add('open');
    if (paletteOverlay) paletteOverlay.classList.add('open');
    if (paletteInput) paletteInput.focus();
  }

  function closePalette() {
    if (!palette) return;
    palette.classList.remove('open');
    if (paletteOverlay) paletteOverlay.classList.remove('open');
  }

  document.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
      e.preventDefault();
      if (palette && palette.classList.contains('open')) {
        closePalette();
      } else {
        openPalette();
      }
    }
    if (e.key === 'Escape') closePalette();
  });

  if (paletteOverlay) {
    paletteOverlay.addEventListener('click', closePalette);
  }

  // --- Modal system ---
  window.openModal = function(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) {
      modal.classList.add('open');
      const input = modal.querySelector('input, textarea, select');
      if (input) setTimeout(() => input.focus(), 100);
    }
  };

  window.closeModal = function(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) modal.classList.remove('open');
  };

  document.querySelectorAll('.modal-overlay').forEach(overlay => {
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) overlay.classList.remove('open');
    });
  });

  // --- Toast system ---
  const toastContainer = document.getElementById('toastContainer') || (() => {
    const el = document.createElement('div');
    el.className = 'toast-container';
    el.id = 'toastContainer';
    document.body.appendChild(el);
    return el;
  })();

  window.showToast = function(message, type, duration) {
    type = type || 'success';
    duration = duration || 4000;
    const toast = document.createElement('div');
    toast.className = 'toast toast-' + type;
    var icon = type === 'success' ? '✓' : type === 'error' ? '✕' : type === 'warn' ? '⚠' : 'ℹ';
    toast.innerHTML = '<span style="font-size:16px">' + icon + '</span><span>' + message + '</span>';
    toastContainer.appendChild(toast);
    setTimeout(function() {
      toast.style.opacity = '0';
      toast.style.transform = 'translateY(10px)';
      setTimeout(function() { toast.remove(); }, 300);
    }, duration);
  };

  // --- Tab switching ---
  document.querySelectorAll('.tabs').forEach(function(tabGroup) {
    const tabs = tabGroup.querySelectorAll('.tab');
    const panels = tabGroup.parentElement.querySelectorAll('.tab-panel');
    tabs.forEach(function(tab, idx) {
      tab.addEventListener('click', function() {
        tabs.forEach(function(t) { t.classList.remove('active'); });
        tab.classList.add('active');
        if (panels.length) {
          panels.forEach(function(p) { p.style.display = 'none'; });
          if (panels[idx]) panels[idx].style.display = 'block';
        }
      });
    });
  });

  // --- Segmented control ---
  document.querySelectorAll('.segmented').forEach(function(seg) {
    const items = seg.querySelectorAll('.segmented-item');
    items.forEach(function(item) {
      item.addEventListener('click', function() {
        items.forEach(function(i) { i.classList.remove('active'); });
        item.classList.add('active');
      });
    });
  });

  // --- Checkbox toggle ---
  document.querySelectorAll('.checkbox').forEach(function(cb) {
    cb.addEventListener('click', function() {
      cb.classList.toggle('checked');
      cb.textContent = cb.classList.contains('checked') ? '✓' : '';
    });
  });

  // --- Table row selection ---
  document.querySelectorAll('.data-table tbody tr').forEach(function(row) {
    row.addEventListener('click', function(e) {
      if (e.target.closest('.cell-actions') || e.target.closest('.checkbox')) return;
      const checkbox = row.querySelector('.checkbox');
      if (checkbox) {
        checkbox.classList.toggle('checked');
        checkbox.textContent = checkbox.classList.contains('checked') ? '✓' : '';
        row.classList.toggle('selected', checkbox.classList.contains('checked'));
      }
    });
  });

  // --- Select-all checkbox ---
  document.querySelectorAll('.select-all').forEach(function(master) {
    master.addEventListener('click', function() {
      const table = master.closest('table');
      const checked = !master.classList.contains('checked');
      master.classList.toggle('checked');
      master.textContent = master.classList.contains('checked') ? '✓' : '';
      table.querySelectorAll('tbody .checkbox').forEach(function(cb) {
        cb.classList.toggle('checked', checked);
        cb.textContent = checked ? '✓' : '';
        if (cb.closest('tr')) cb.closest('tr').classList.toggle('selected', checked);
      });
    });
  });

  // --- Demo: show toast on button clicks with data-toast attribute ---
  document.querySelectorAll('[data-toast]').forEach(function(btn) {
    btn.addEventListener('click', function() {
      const msg = btn.getAttribute('data-toast');
      const type = btn.getAttribute('data-toast-type') || 'success';
      if (msg) showToast(msg, type);
    });
  });

  // --- Demo: open modal on button clicks with data-modal attribute ---
  document.querySelectorAll('[data-modal]').forEach(function(btn) {
    btn.addEventListener('click', function() {
      const modalId = btn.getAttribute('data-modal');
      if (modalId) openModal(modalId);
    });
  });

  // --- File dropzone visual feedback ---
  document.querySelectorAll('.dropzone').forEach(function(dz) {
    dz.addEventListener('dragover', function(e) { e.preventDefault(); dz.classList.add('dragover'); });
    dz.addEventListener('dragleave', function() { dz.classList.remove('dragover'); });
    dz.addEventListener('drop', function(e) {
      e.preventDefault();
      dz.classList.remove('dragover');
      showToast('Files attached (demo)', 'success');
    });
  });

  // --- Recipient item toggle in Send page ---
  document.querySelectorAll('.recipient-item').forEach(function(item) {
    item.addEventListener('click', function() {
      item.classList.toggle('selected');
      const cb = item.querySelector('.checkbox');
      if (cb) {
        cb.classList.toggle('checked', item.classList.contains('selected'));
        cb.textContent = cb.classList.contains('checked') ? '✓' : '';
      }
      updateSendCount();
    });
  });

  function updateSendCount() {
    const count = document.querySelectorAll('.recipient-item.selected').length;
    const el = document.getElementById('selectedCount');
    if (el) el.textContent = count;
    const st = document.getElementById('statusCount');
    if (st) st.textContent = count;
    const sched = document.getElementById('scheduleSummaryCount');
    if (sched) sched.textContent = count;
    const btn = document.getElementById('sendNowBtn');
    if (btn) btn.disabled = count === 0;
  }

  // --- Template variable insert ---
  document.querySelectorAll('.var-chip').forEach(function(chip) {
    chip.addEventListener('click', function() {
      const textarea = document.querySelector('.composer-textarea');
      if (textarea) {
        const val = chip.textContent;
        const start = textarea.selectionStart;
        const end = textarea.selectionEnd;
        textarea.value = textarea.value.substring(0, start) + val + textarea.value.substring(end);
        textarea.focus();
        textarea.setSelectionRange(start + val.length, start + val.length);
      }
    });
  });

  // --- Live preview text from composer ---
  const msgInput = document.getElementById('messageInput');
  const previewText = document.getElementById('livePreviewText');
  if (msgInput && previewText) {
    const syncPreview = function() {
      const val = msgInput.value.trim();
      previewText.textContent = val || 'Start typing to preview your message...';
    };
    msgInput.addEventListener('input', syncPreview);
    syncPreview();
  }

  // --- Search trigger click ---
  const searchTrigger = document.querySelector('.search-trigger');
  if (searchTrigger) {
    searchTrigger.addEventListener('click', openPalette);
  }

  console.log('WhatsApp App UI initialized');
})();
