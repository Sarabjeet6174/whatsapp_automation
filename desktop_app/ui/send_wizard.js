/* Send Messages — 5-step wizard (vanilla JS) */
(function () {
  'use strict';

  var TOTAL_STEPS = 5;
  var currentStep = 1;
  var previewIndex = 0;

  var SAMPLE_CONTACTS = [
    { id: 1, name: 'Alice Johnson', phone: '+1 555-0101', tags: ['VIP', 'Active'], company: 'Acme Corp', avatar: 'AJ' },
    { id: 2, name: 'Bob Smith', phone: '+1 555-0102', tags: ['Lead'], company: 'Globex', avatar: 'BS' },
    { id: 3, name: 'Carol White', phone: '+1 555-0103', tags: ['Q3', 'Eligible'], company: 'Initech', avatar: 'CW' },
    { id: 4, name: 'David Lee', phone: '+1 555-0104', tags: ['Renewal'], company: 'Umbrella Co', avatar: 'DL' },
    { id: 5, name: 'Eva Martinez', phone: '+1 555-0105', tags: ['VIP'], company: 'Stark Industries', avatar: 'EM' },
    { id: 6, name: 'Frank Nguyen', phone: '+1 555-0106', tags: ['Warm'], company: 'Wayne Enterprises', avatar: 'FN' },
    { id: 7, name: 'Grace Kim', phone: '+1 555-0107', tags: ['Active'], company: 'Oscorp', avatar: 'GK' },
    { id: 8, name: 'Henry Brown', phone: '+1 555-0108', tags: ['Lead', 'Imported'], company: 'Hooli', avatar: 'HB' }
  ];

  var WA_CONTACTS = [
    { id: 101, name: 'Priya Sharma', phone: '+91 98765 43210', tags: ['WA'], company: 'TechFlow', avatar: 'PS' },
    { id: 102, name: 'Rahul Verma', phone: '+91 91234 56789', tags: ['WA', 'VIP'], company: 'Nova Labs', avatar: 'RV' },
    { id: 103, name: 'Sneha Patel', phone: '+91 99887 76655', tags: ['WA'], company: 'Bright Co', avatar: 'SP' }
  ];

  var GROUPS = [
    { id: 201, name: 'Sales Team', phone: '—', tags: ['Group', '24 members'], company: '', avatar: 'ST', isGroup: true },
    { id: 202, name: 'VIP Customers', phone: '—', tags: ['Group', '56 members'], company: '', avatar: 'VC', isGroup: true },
    { id: 203, name: 'Product Launch', phone: '—', tags: ['Group', '12 members'], company: '', avatar: 'PL', isGroup: true }
  ];

  var LISTS = [
    { id: 301, name: 'Q1 Outreach', phone: '245 contacts', tags: ['List'], company: '', avatar: 'Q1', isList: true },
    { id: 302, name: 'Webinar Attendees', phone: '89 contacts', tags: ['List'], company: '', avatar: 'WA', isList: true }
  ];

  var state = {
    activeTab: 'contacts',
    selectedIds: new Set(),
    selectedGroups: 0,
    draftSaved: false
  };

  var els = {};

  function $(id) { return document.getElementById(id); }

  function init() {
    if (!$('wizardContinueBtn')) return;

    els.continueBtn = $('wizardContinueBtn');
    els.backBtn = $('wizardBackBtn');
    els.nav = $('wizardNav');
    els.fill = $('stepperFill');
    els.tableBody = $('recipientTableBody');
    els.search = $('recipientSearch');
    els.selectAll = $('selectAllRecipients');

    bindNav();
    bindRecipients();
    bindCompose();
    bindSchedule();
    bindPreview();
    bindReview();

    setDefaultScheduleDate();
    renderRecipientTable();
    showStep(1);
  }

  function bindNav() {
    els.continueBtn.addEventListener('click', nextStep);
    els.backBtn.addEventListener('click', previousStep);
    $('reviewBackBtn').addEventListener('click', previousStep);
    $('sendCampaignBtn').addEventListener('click', function () {
      if (typeof showToast === 'function') {
        showToast('Campaign queued successfully!', 'success');
      }
      state.draftSaved = true;
      updateSummary();
    });
  }

  function bindRecipients() {
    document.querySelectorAll('#recipientTabs .tab').forEach(function (tab) {
      tab.addEventListener('click', function () {
        document.querySelectorAll('#recipientTabs .tab').forEach(function (t) { t.classList.remove('active'); });
        tab.classList.add('active');
        state.activeTab = tab.getAttribute('data-tab');
        if (els.selectAll) {
          els.selectAll.classList.remove('checked');
          els.selectAll.textContent = '';
        }
        renderRecipientTable();
      });
    });

    if (els.search) {
      els.search.addEventListener('input', renderRecipientTable);
    }

    if (els.selectAll) {
      els.selectAll.addEventListener('click', function () {
        var rows = getVisibleRows();
        var allSelected = rows.length && rows.every(function (r) { return state.selectedIds.has(r.id); });
        rows.forEach(function (r) {
          if (allSelected) state.selectedIds.delete(r.id);
          else state.selectedIds.add(r.id);
        });
        renderRecipientTable();
        updateCounts();
      });
    }
  }

  function bindCompose() {
    var campaignName = $('campaignName');
    var templateSelect = $('templateSelect');
    var messageInput = $('messageInput');

    [campaignName, templateSelect, messageInput].forEach(function (el) {
      if (!el) return;
      el.addEventListener('input', onComposeChange);
      el.addEventListener('change', onComposeChange);
    });

    document.querySelectorAll('.ai-tool').forEach(function (btn) {
      btn.addEventListener('click', function () {
        if (typeof showToast === 'function') {
          showToast('AI ' + btn.getAttribute('data-action') + ' (demo)', 'success');
        }
      });
    });

    document.querySelectorAll('#attachmentList .remove').forEach(function (rm) {
      rm.addEventListener('click', function (e) {
        e.stopPropagation();
        rm.closest('.attachment-chip').remove();
        updateAttachmentSummary();
        validateStep();
        updateSummary();
      });
    });
  }

  function bindSchedule() {
    document.querySelectorAll('input[name="sendMode"]').forEach(function (radio) {
      radio.addEventListener('change', function () {
        var fields = $('scheduleFields');
        if (fields) fields.hidden = radio.value !== 'schedule' || !radio.checked;
        if (radio.checked) updateSummary();
        validateStep();
      });
    });

    ['scheduleDate', 'scheduleTime', 'scheduleTz', 'messageDelay', 'randomDelay'].forEach(function (id) {
      var el = $(id);
      if (el) el.addEventListener('change', function () { updateSummary(); validateStep(); });
    });

    var randomUi = $('randomDelayUi');
    var randomCb = $('randomDelay');
    if (randomCb && randomUi) {
      randomCb.addEventListener('change', function () {
        randomUi.classList.toggle('checked', randomCb.checked);
        randomUi.textContent = randomCb.checked ? '✓' : '';
      });
    }
  }

  function bindPreview() {
    $('previewPrevRecipient').addEventListener('click', function () {
      var selected = getSelectedContacts();
      if (!selected.length) return;
      previewIndex = (previewIndex - 1 + selected.length) % selected.length;
      updatePreview();
    });
    $('previewNextRecipient').addEventListener('click', function () {
      var selected = getSelectedContacts();
      if (!selected.length) return;
      previewIndex = (previewIndex + 1) % selected.length;
      updatePreview();
    });
  }

  function bindReview() {}

  function getSourceData() {
    switch (state.activeTab) {
      case 'wa': return WA_CONTACTS;
      case 'groups': return GROUPS;
      case 'lists': return LISTS;
      default: return SAMPLE_CONTACTS;
    }
  }

  function getVisibleRows() {
    var q = (els.search && els.search.value || '').trim().toLowerCase();
    return getSourceData().filter(function (row) {
      if (!q) return true;
      return (
        row.name.toLowerCase().includes(q) ||
        row.phone.toLowerCase().includes(q) ||
        row.tags.some(function (t) { return t.toLowerCase().includes(q); })
      );
    });
  }

  function renderRecipientTable() {
    if (!els.tableBody) return;
    var rows = getVisibleRows();
    els.tableBody.innerHTML = rows.map(function (row) {
      var checked = state.selectedIds.has(row.id);
      var tagHtml = row.tags.map(function (t) {
        var cls = t === 'VIP' ? 'chip-green' : t === 'Group' || t === 'List' ? 'chip-blue' : 'chip-gray';
        return '<span class="chip ' + cls + '">' + t + '</span>';
      }).join(' ');
      return (
        '<tr class="' + (checked ? 'selected' : '') + '" data-id="' + row.id + '">' +
        '<td><div class="checkbox' + (checked ? ' checked' : '') + '">' + (checked ? '✓' : '') + '</div></td>' +
        '<td><div class="recipient-avatar">' + row.avatar + '</div></td>' +
        '<td><span class="recipient-name">' + row.name + '</span></td>' +
        '<td class="font-mono">' + row.phone + '</td>' +
        '<td>' + tagHtml + '</td>' +
        '</tr>'
      );
    }).join('');

    els.tableBody.querySelectorAll('tr').forEach(function (tr) {
      tr.addEventListener('click', function (e) {
        if (e.target.closest('.checkbox')) return;
        toggleRow(parseInt(tr.getAttribute('data-id'), 10));
      });
      var cb = tr.querySelector('.checkbox');
      if (cb) {
        cb.addEventListener('click', function (e) {
          e.stopPropagation();
          toggleRow(parseInt(tr.getAttribute('data-id'), 10));
        });
      }
    });

    syncSelectAll();
    updateCounts();
  }

  function toggleRow(id) {
    if (state.selectedIds.has(id)) state.selectedIds.delete(id);
    else state.selectedIds.add(id);
    renderRecipientTable();
  }

  function syncSelectAll() {
    if (!els.selectAll) return;
    var rows = getVisibleRows();
    var selectedVisible = rows.filter(function (r) { return state.selectedIds.has(r.id); }).length;
    var all = rows.length > 0 && selectedVisible === rows.length;
    var some = selectedVisible > 0 && !all;
    els.selectAll.classList.toggle('checked', all);
    els.selectAll.textContent = all ? '✓' : '';
    els.selectAll.style.opacity = some ? '0.6' : '1';
  }

  function getSelectedContacts() {
    var all = SAMPLE_CONTACTS.concat(WA_CONTACTS);
    return all.filter(function (c) { return state.selectedIds.has(c.id); });
  }

  function countRecipients() {
    var contacts = getSelectedContacts().length;
    var groups = 0;
    var lists = 0;
    GROUPS.forEach(function (g) { if (state.selectedIds.has(g.id)) groups++; });
    LISTS.forEach(function (l) { if (state.selectedIds.has(l.id)) lists++; });
    var groupMembers = groups * 24;
    var listMembers = lists * 245;
    return contacts + groupMembers + listMembers;
  }

  function countGroups() {
    var n = 0;
    GROUPS.forEach(function (g) { if (state.selectedIds.has(g.id)) n++; });
    LISTS.forEach(function (l) { if (state.selectedIds.has(l.id)) n++; });
    return n;
  }

  function estimateMinutes(recipients) {
    if (!recipients) return '—';
    var delay = parseInt(($('messageDelay') && $('messageDelay').value) || '10', 10);
    var secs = recipients * delay;
    var mins = Math.max(1, Math.ceil(secs / 60));
    return mins + ' min';
  }

  function updateCounts() {
    var recipients = countRecipients();
    var groups = countGroups();
    var eta = estimateMinutes(recipients);

    $('step1RecipientCount').textContent = recipients;
    $('step1GroupCount').textContent = groups;
    $('step1Eta').textContent = recipients ? eta : '—';

    updateSummary();
    validateStep();
  }

  function updateAttachmentSummary() {
    var n = document.querySelectorAll('#attachmentList .attachment-chip').length;
    var label = n === 1 ? '1 File' : n + ' Files';
    $('summaryMedia').textContent = label;
    $('reviewAttachments').textContent = label;
    var hint = $('previewAttachmentHint');
    if (hint) hint.textContent = n ? '📎 ' + label : '';
    $('checkMedia').classList.toggle('is-ok', n > 0);
  }

  function onComposeChange() {
    state.draftSaved = false;
    updateSummary();
    validateStep();
    if (currentStep === 3) updatePreview();
  }

  function getMessageTemplate() {
    return ($('messageInput') && $('messageInput').value) || '';
  }

  function renderMessageFor(contact) {
    var msg = getMessageTemplate();
    var today = new Date().toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
    return msg
      .replace(/\{\{name\}\}/gi, contact.name.split(' ')[0])
      .replace(/\{\{company\}\}/gi, contact.company || 'your company')
      .replace(/\{\{phone\}\}/gi, contact.phone)
      .replace(/\{\{date\}\}/gi, today);
  }

  function updatePreview() {
    var selected = getSelectedContacts();
    if (!selected.length) {
      selected = [SAMPLE_CONTACTS[0]];
    }
    if (previewIndex >= selected.length) previewIndex = 0;
    var contact = selected[previewIndex];
    var rendered = renderMessageFor(contact);

    $('previewRecipientName').textContent = contact.name;
    $('previewPhoneHeader').textContent = contact.name;
    $('previewBubble').textContent = rendered || 'Your message preview will appear here.';

    var hasVars = /\{\{/.test(getMessageTemplate());
    $('checkVariables').classList.toggle('is-ok', !hasVars);
    $('checkLooksGood').classList.toggle('is-ok', rendered.trim().length > 0);
    updateAttachmentSummary();
  }

  function getScheduleLabel() {
    var mode = document.querySelector('input[name="sendMode"]:checked');
    if (!mode || mode.value === 'now') return 'Send now';
    var date = $('scheduleDate').value;
    var time = $('scheduleTime').value;
    if (!date) return 'Scheduled';
    var d = new Date(date + 'T' + (time || '09:00'));
    return d.toLocaleString(undefined, { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
  }

  function updateSummary() {
    var recipients = countRecipients();
    var template = ($('templateSelect') && $('templateSelect').value) || 'Welcome';
    var shortTemplate = template.split(' ')[0];

    $('summaryRecipients').textContent = recipients;
    $('summaryTemplate').textContent = shortTemplate;
    $('summarySchedule').textContent = getScheduleLabel().replace('Send now', 'Now').split(',')[0];

    var draftEl = $('summaryDraft');
    if (draftEl) {
      draftEl.classList.toggle('status-success', state.draftSaved);
      draftEl.classList.toggle('status-idle', !state.draftSaved);
      draftEl.querySelector('span').textContent = state.draftSaved ? 'Draft Saved ✓' : 'Draft not saved';
    }

    if ($('reviewRecipients')) $('reviewRecipients').textContent = recipients;
    if ($('reviewTemplate')) $('reviewTemplate').textContent = template;
    if ($('reviewSchedule')) $('reviewSchedule').textContent = getScheduleLabel();
    if ($('reviewDuration')) $('reviewDuration').textContent = estimateMinutes(recipients);

    var completion = '—';
    if (recipients) {
      var mode = document.querySelector('input[name="sendMode"]:checked');
      var start = new Date();
      if (mode && mode.value === 'schedule' && $('scheduleDate').value) {
        start = new Date($('scheduleDate').value + 'T' + ($('scheduleTime').value || '09:00'));
      }
      var mins = parseInt(estimateMinutes(recipients), 10) || 1;
      start.setMinutes(start.getMinutes() + mins);
      completion = start.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
    }
    if ($('reviewCompletion')) $('reviewCompletion').textContent = completion;

    updateAttachmentSummary();
  }

  function setDefaultScheduleDate() {
    var d = new Date();
    d.setDate(d.getDate() + 1);
    var iso = d.toISOString().slice(0, 10);
    if ($('scheduleDate')) $('scheduleDate').value = iso;
  }

  function canAdvance(step) {
    if (step === 1) return countRecipients() > 0;
    if (step === 2) {
      var msg = getMessageTemplate().trim();
      var files = document.querySelectorAll('#attachmentList .attachment-chip').length;
      return msg.length > 0 || files > 0;
    }
    if (step === 3) return true;
    if (step === 4) {
      var mode = document.querySelector('input[name="sendMode"]:checked');
      if (mode && mode.value === 'schedule') {
        return !!($('scheduleDate') && $('scheduleDate').value);
      }
      return true;
    }
    return false;
  }

  function validateStep() {
    if (els.continueBtn && currentStep < 5) {
      els.continueBtn.disabled = !canAdvance(currentStep);
    }
    if (els.backBtn) {
      els.backBtn.disabled = currentStep <= 1;
    }
  }

  function updateStepper(step) {
    document.querySelectorAll('.wizard-step').forEach(function (el) {
      var n = parseInt(el.getAttribute('data-step'), 10);
      el.classList.remove('is-active', 'is-done', 'is-future');
      if (n < step) el.classList.add('is-done');
      else if (n === step) el.classList.add('is-active');
      else el.classList.add('is-future');
    });
    if (els.fill) {
      var pct = ((step - 1) / (TOTAL_STEPS - 1)) * 100;
      els.fill.style.width = pct + '%';
    }
  }

  function showStep(step) {
    step = Math.max(1, Math.min(TOTAL_STEPS, step));
    currentStep = step;

    document.querySelectorAll('.wizard-step-panel').forEach(function (panel) {
      var n = parseInt(panel.getAttribute('data-step'), 10);
      var visible = n === step;
      panel.classList.toggle('is-visible', visible);
      panel.classList.toggle('is-entering', visible);
      if (visible) {
        requestAnimationFrame(function () {
          panel.classList.remove('is-entering');
        });
      }
    });

    updateStepper(step);

    if (els.nav) els.nav.hidden = step >= 5;
    if (step === 3) {
      previewIndex = 0;
      updatePreview();
    }
    if (step === 5) updateSummary();

    validateStep();
  }

  function nextStep() {
    if (!canAdvance(currentStep)) return;
    if (currentStep === 2) state.draftSaved = true;
    if (currentStep < TOTAL_STEPS) showStep(currentStep + 1);
    updateSummary();
  }

  function previousStep() {
    if (currentStep > 1) showStep(currentStep - 1);
  }

  window.SendWizard = {
    currentStep: function () { return currentStep; },
    nextStep: nextStep,
    previousStep: previousStep,
    showStep: showStep
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
