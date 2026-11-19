'use strict';
/* Mailbox: the company's email (for example beheer@pandtbeheer.nl) inside the software. Each email is linked to its
   home and tenant when that is clear (otherwise it waits under "Unassigned"); the AI writes a short summary, picks the
   category and suggests a case and the dates it mentions. The team confirms: a case (it then shows on the home, the
   dashboard and in the calendar), a task with a date, the home. Replies go out from the platform in the same
   conversation, and the conversation shows on the home. Server: app/mailbox.py. Plugs into app.js without editing it. */
(() => {
  if (typeof navGroups === 'undefined' || typeof renderPage !== 'function') return;
  const STAFF = ['admin', 'manager', 'property', 'finance'];
  const ws = navGroups.find(g => g[0] === 'WORKSPACE');
  if (ws && !ws[1].some(n => n[0] === 'mailbox')) { const at = ws[1].findIndex(n => n[0] === 'calendar'); ws[1].splice(at >= 0 ? at + 1 : 1, 0, ['mailbox', 'Mailbox', 'message']); }
  const baseAllowed = allowedPage;
  allowedPage = page => page === 'mailbox' ? !!state.company && STAFF.includes(company().role) : baseAllowed(page);

  const CATS = {Maintenance: 'c-maintenance', Financial: 'c-financial', Huurcommissie: 'c-huurcommissie', Contract: 'c-rental', Complaint: 'c-vve', Legal: 'c-legal', General: '', Newsletter: ''};
  const CASE_KINDS = ['Maintenance', 'Huurcommissie', 'Financial', 'Legal', 'Contract', 'Complaint', 'Other'];
  const DOC_KINDS = ['General', 'Contract', 'Lease draft', 'Huurcommissie', 'supplier invoice', 'Settlement letter', 'Valuation', 'Inspection report', 'Maintenance report',
    'Quote', 'Energy label', 'Sustainability report', 'VvE', 'Permit', 'Legal notice', 'Insurance policy', 'Transfer deed', 'Deed of division'];
  const BOXES = [['action', 'Needs action'], ['unassigned', 'Unassigned'], ['urgent', 'Urgent'], ['done', 'Handled'], ['all', 'All']];
  const MATCHED = {tenant: 'Found by the tenant\'s email address', address: 'Found by the address in the email', ai: 'Found by the AI (address in the email)',
    conversation: 'Same conversation as an earlier email', team: 'Linked by the team'};
  const nl = () => (window.kreinLang || document.documentElement.lang) === 'nl';
  const ini = s => String(s || '?').replace(/[<>"]/g, '').split(/[\s@.]+/).filter(Boolean).slice(0, 2).map(x => x[0].toUpperCase()).join('') || '?';
  const ui = {box: 'action', q: '', cat: '', list: null, info: null, key: '', loading: false, error: '', sel: null, d: null, reply: '', sending: false, drafting: false, checking: false, homeEdit: false, homes: null, homesLoading: false};
  try { const saved = JSON.parse(sessionStorage.getItem('krein_mail') || '{}'); if (saved.box) ui.box = saved.box; } catch { /* storage unavailable */ }
  if (!BOXES.some(b => b[0] === ui.box)) ui.box = 'action';
  const remember = () => { try { sessionStorage.setItem('krein_mail', JSON.stringify({box: ui.box})); } catch { /* storage unavailable */ } };

  const when = s => {
    if (!s) return '';
    const d = new Date(s), t = new Date(), loc = nl() ? 'nl-NL' : 'en-GB';
    if (isNaN(d)) return String(s).slice(0, 10);
    const same = d.toDateString() === t.toDateString();
    return new Intl.DateTimeFormat(loc, same ? {hour: '2-digit', minute: '2-digit'} : d.getFullYear() === t.getFullYear() ? {day: 'numeric', month: 'short'} : {day: 'numeric', month: 'short', year: 'numeric'}).format(d);
  };
  const full = s => { const d = new Date(s); return isNaN(d) ? String(s || '') : new Intl.DateTimeFormat(nl() ? 'nl-NL' : 'en-GB', {weekday: 'short', day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit'}).format(d); };
  const day = s => new Intl.DateTimeFormat(nl() ? 'nl-NL' : 'en-GB', {weekday: 'short', day: 'numeric', month: 'short'}).format(new Date(s + 'T12:00:00'));
  const size = n => n > 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} kB`;
  const cat = c => `<span class="doc-cat ${CATS[c] || ''}">${e(c)}</span>`;
  const summary = m => (nl() ? m.summary_nl : m.summary_en) || m.summary_en || '';

  async function load(force = false) {
    if (!state.company || ui.loading) return;
    const key = `${state.company}|${ui.box}|${ui.q}`;
    if (!force && key === ui.key && ui.list) return;
    ui.loading = true; ui.key = key;
    try {
      const r = await api(`/api/mail?company_id=${state.company}&box=${ui.box}&q=${encodeURIComponent(ui.q)}`);
      ui.list = r.messages; ui.info = r; ui.error = '';
    } catch (err) { ui.error = err.message; }
    ui.loading = false;
    if (state.page === 'mailbox') render();
  }
  async function open(id, keepReply = false) {
    if (ui.sel !== id) { ui.d = null; ui.homeEdit = false; if (!keepReply) ui.reply = ''; }
    ui.sel = id;
    if (state.page !== 'mailbox') { state.page = 'mailbox'; history.replaceState(null, '', '#mailbox'); }
    render();
    try { ui.d = await api(`/api/mail/${id}`); } catch (err) { toast(err.message); ui.sel = null; ui.d = null; }
    if (state.page === 'mailbox') { render(); if (innerWidth < 900) document.querySelector('.mail-read-col')?.scrollIntoView({block: 'start'}); }
  }

  // ---- the list ----
  function listItem(m) {
    const who = m.from_name || m.from_addr;
    return `<button type="button" class="mail-item${m.id === ui.sel ? ' active' : ''}${m.status === 'new' ? ' is-new' : ''}${m.urgent && m.status === 'new' ? ' is-urgent' : ''}" data-mail-open="${m.id}">
      <span class="avatar">${e(ini(who))}</span>
      <span class="mail-item-main"><span class="mail-item-top"><strong>${e(who)}</strong><time>${e(when(m.sent_at))}</time></span>
        <span class="mail-item-subj">${m.urgent && m.status === 'new' ? '<span class="badge red">Urgent</span> ' : ''}<span>${e(m.subject)}</span></span>
        <small class="mail-item-sum">${e(summary(m) || m.snippet)}</small>
        <span class="mail-item-tags">${m.property_id ? `<span class="mail-home">${icon('home')}<span>${e(m.property_address || '')}</span></span>` : '<span class="badge amber">Unassigned</span>'}${cat(m.category)}${m.case_id ? '<span class="badge blue">Case</span>' : ''}${m.attachments ? `<span class="mail-clip">${icon('file')}${m.attachments}</span>` : ''}</span>
      </span></button>`;
  }

  function statusLine() {
    const i = ui.info;
    if (!i || !i.connected) {
      return `<div class="notice info mail-off">${icon('info')}<div><strong>The mailbox is not connected yet.</strong>
        <p>When the login of the company mailbox is added to the server settings, new emails show here by themselves, linked to the right home.</p></div></div>`;
    }
    const last = i.checked_at ? `<span>Last check: ${e(when(i.checked_at))}</span>` : '<span>Not checked yet</span>';
    const elsewhere = i.unassigned_at ? `<span>${e(nl() ? `Niet gekoppelde e-mails staan bij ${i.unassigned_at}` : `Emails not linked yet are under ${i.unassigned_at}`)}</span>` : '';
    return `<p class="mail-status">${icon('check')}<span>Connected to <strong translate="no">${e(i.address)}</strong></span><span>Checked every ${i.minutes} minutes</span>${last}${i.ai ? '<span>AI summaries on</span>' : ''}${elsewhere}</p>
      ${i.error ? `<div class="notice warning">${icon('info')}<span>${e(i.error)}</span></div>` : ''}`;
  }

  function listCol() {
    const c = (ui.info && ui.info.counts) || {};
    const tabs = `<div class="mail-tabs" role="tablist">${BOXES.map(([k, label]) => `<button type="button" role="tab" class="doc-tab${ui.box === k ? ' active' : ''}" aria-selected="${ui.box === k}" data-mail-box="${k}"><span>${e(label)}</span>${c[k] ? `<span class="count">${c[k]}</span>` : ''}</button>`).join('')}</div>`;
    const cats = [...new Set((ui.list || []).map(m => m.category))];
    const tools = `<div class="mail-tools"><label class="doc-search">${icon('search')}<input type="search" data-mail-search value="${e(ui.q)}" placeholder="Search emails" aria-label="Search emails"></label>
      ${cats.length > 1 ? `<select data-mail-cat aria-label="Category"><option value="">All categories</option>${cats.map(x => `<option value="${e(x)}" ${ui.cat === x ? 'selected' : ''}>${e(x)}</option>`).join('')}</select>` : ''}</div>`;
    const list = (ui.list || []).filter(m => !ui.cat || m.category === ui.cat);
    const emptyText = {new: 'No new emails. Everything is handled.', action: 'No emails waiting for an answer.', unassigned: 'Every email is linked to a home.', urgent: 'No urgent emails.', done: 'Nothing handled yet.', all: 'No emails yet.'};
    const body = list.length ? `<div class="mail-list">${list.map(listItem).join('')}</div>` : `<p class="mail-none">${e(ui.q ? 'No emails found. Try another word.' : emptyText[ui.box] || '')}</p>`;
    return tabs + tools + body;
  }

  // ---- reading one email ----
  function bodyHtml(text) {
    const lines = String(text || '').split('\n');
    const cut = lines.findIndex((l, i) => i > 0 && /^(>|On .{4,80} wrote:|Op .{4,80} schreef|-{2,} ?(Original Message|Oorspronkelijk bericht)|Van: .+|From: .+@)/i.test(l.trim()));
    const link = s => e(s).replace(/(https?:\/\/[^\s<]+)/g, '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>');
    const main = (cut > 0 ? lines.slice(0, cut) : lines).join('\n').trim();
    const rest = cut > 0 ? lines.slice(cut).join('\n') : '';
    return `<div class="mail-body">${link(main)}</div>${rest.trim() ? `<details class="mail-quote"><summary>Show earlier text</summary><div class="mail-body">${link(rest)}</div></details>` : ''}`;
  }

  function linkCard(d) {
    const {message: m, property: p, tenant, case: k} = d;
    if (p && !ui.homeEdit) {
      return `<div class="mail-link"><div class="mail-link-item"><span class="mail-label">Home</span><button type="button" class="link-button" data-mail="home-open">${icon('home')} ${e(p.address)}${p.city ? ', ' + e(p.city) : ''}</button>
          ${MATCHED[m.matched_by] ? `<small>${e(MATCHED[m.matched_by])}</small>` : ''}</div>
        <div class="mail-link-item"><span class="mail-label">Tenant</span>${tenant ? `<span>${e(tenant.name)}</span><small translate="no">${e(tenant.email)}</small>` : '<span class="muted">Not a tenant</span>'}</div>
        <div class="mail-link-item"><span class="mail-label">Case</span>${k ? `<button type="button" class="link-button" data-mail="case-open">${icon('clipboard')} ${e(k.subject)}</button><small>${window.kreinCases ? window.kreinCases.status(k.status) : e(k.status)}</small>` : '<span class="muted">No case yet</span>'}</div>
        <button type="button" class="button small" data-mail="home-edit">Change</button></div>`;
    }
    if (ui.homes === null) loadHomes();
    const homes = ui.homes || rows('properties').map(x => ({...x, company_id: state.company}));
    const byName = (a, b) => String(a.address || a.name).localeCompare(String(b.address || b.name));
    const option = x => `<option value="${x.id}" ${p && p.id === x.id ? 'selected' : ''}>${e(x.address || x.name)}${x.city ? ', ' + e(x.city) : ''}</option>`;
    const groups = [...new Set(homes.map(x => x.company_id))].sort((a, b) => (b === state.company) - (a === state.company));
    const opts = groups.length > 1
      ? groups.map(g => { const list = homes.filter(x => x.company_id === g).sort(byName); return `<optgroup label="${e(list[0].company || '')}">${list.map(option).join('')}</optgroup>`; }).join('')
      : homes.slice().sort(byName).map(option).join('');
    const where = c => c.company_id && c.company_id !== state.company && c.company ? ` <small>· ${e(c.company)}</small>` : '';
    return `<div class="mail-link is-missing"><div><strong>${p ? 'Link this email to another home' : 'Which home is this email about?'}</strong>
        <p>${p ? 'The whole conversation moves to the home you choose.' : 'The software could not tell for sure. Choose the home; the rest of the conversation follows.'}</p>
        ${d.candidates.length ? `<div class="mail-cands"><span class="mail-label">Possibly</span>${d.candidates.map(c => `<button type="button" class="button small" data-mail-home="${c.id}">${icon('home')} ${e(c.address)}${where(c)}</button>`).join('')}</div>` : ''}
        <div class="mail-pick"><select data-mail-home-select aria-label="Home"><option value="">Choose a home…</option>${opts}</select>
          <button type="button" class="button primary small" data-mail="home-save">Link</button>
          ${p ? '<button type="button" class="button small" data-mail="home-cancel">Cancel</button><button type="button" class="button link small" data-mail="home-remove">Unlink</button>' : ''}</div></div></div>`;
  }

  function aiCard(d) {
    const {message: m, property: p, case: k, cases, suggestion: s} = d;
    const sum = summary(m);
    const dates = (s.dates || []).map((x, i) => `<div class="mail-date"><span class="att-date">${e(day(x.date))}</span><span class="mail-date-what"><strong>${e(x.what || (x.kind === 'appointment' ? 'Appointment' : 'Deadline'))}</strong>
        <small><span>${x.kind === 'appointment' ? 'Appointment' : 'Deadline'}</span>${x.time ? ` · ${e(x.time)}` : ''}</small></span><button type="button" class="button small" data-mail-task="${i}">${icon('plus')}Add as task</button></div>`).join('');
    const prio = x => window.kreinCases ? window.kreinCases.priority(x) : e(x);
    let next = '';
    if (!p) next = '<p class="muted mail-hint">Link the email to a home to create a case or task.</p>';
    else if (!k) next = `<div class="mail-suggest"><div><span class="mail-label">${s.case ? 'Suggested case' : 'Case'}</span>${s.case ? `<strong>${e(s.case.title)}</strong> ${prio(s.case.priority)}` : '<span class="muted">No case suggested.</span>'}</div>
        <div class="actions"><button type="button" class="button ${s.case ? 'primary' : ''} small" data-mail="case-new">${icon('plus')}${s.case ? 'Create this case' : 'Create a case'}</button>
        ${cases.length ? `<select data-mail-case-link aria-label="Add to an existing case"><option value="">Or add to a case…</option>${cases.map(c => `<option value="${c.id}">${e(c.subject)}</option>`).join('')}</select>` : ''}</div></div>`;
    else next = `<div class="mail-suggest is-done"><div><span class="mail-label">Case</span><strong>${e(k.subject)}</strong> ${prio(k.priority)}${k.next_deadline ? ` <small>· deadline ${e(day(k.next_deadline))}</small>` : ''}</div>
        <div class="actions"><button type="button" class="button small" data-mail="case-open">Open case</button><button type="button" class="button link small" data-mail="case-unlink">Unlink</button></div></div>`;
    return `<section class="mail-ai"><div class="mail-ai-head">${icon('info')}<strong>${s.by === 'ai' ? 'AI summary' : 'Summary'}</strong>${s.by !== 'ai' ? '<small>(the AI was not used for this email)</small>' : ''}</div>
      ${sum ? `<p>${e(sum)}</p>` : ''}
      ${(s.actions || []).length ? `<ul class="mail-actions">${s.actions.map(a => `<li>${e(a)}</li>`).join('')}</ul>` : ''}
      ${dates ? `<div class="mail-dates"><span class="mail-label">Dates in this email</span>${dates}</div>` : ''}
      ${next}<p class="mail-check">${icon('info')}<span>Check the AI's suggestions: nothing is created until you confirm.</span></p></section>`;
  }

  function thread(d) {
    return d.thread.map(t => {
      const out = t.direction === 'out', who = out ? (t.sent_by_name || 'You') : (t.from_name || t.from_addr);
      const files = (t.attachments || []).map(f => `<span class="mail-file">${icon('file')}<span>${e(f.name)} <small>${e(size(f.size || 0))}</small></span>
        ${f.document_id ? `<a class="button small" href="/api/documents/${f.document_id}/file" target="_blank" rel="noopener">View</a>` : !out ? `<a class="button small" href="/api/mail/${t.id}/attachments/${f.n}" target="_blank" rel="noopener">View</a><button type="button" class="button small" data-mail-file="${t.id}:${f.n}">Save to documents</button>` : ''}</span>`).join('');
      return `<article class="mail-msg ${out ? 'is-out' : 'is-in'}${t.id === d.message.id ? ' is-current' : ''}"><header><span class="avatar">${e(ini(who))}</span>
        <div><strong>${e(who)}</strong>${out ? ' <span class="badge green">Sent</span>' : ''}<small><span>${out ? 'To' : 'From'}</span> <span translate="no">${e(out ? t.to_addrs : t.from_addr)}</span> · ${e(full(t.sent_at))}</small></div></header>
        ${bodyHtml(t.body)}${files ? `<div class="mail-files">${files}</div>` : ''}</article>`;
    }).join('');
  }

  function reader() {
    if (!ui.sel) return `<div class="mail-empty">${icon('message')}<p>Choose an email to read it.</p></div>`;
    if (!ui.d) return `<div class="mail-empty"><p>Loading…</p></div>`;
    const d = ui.d, m = d.message, to = m.reply_to || m.from_addr;
    const ai = ui.info && ui.info.ai;
    return `<button type="button" class="button link small mail-back" data-mail="back">&lsaquo; Back to the list</button>
      <header class="mail-head"><div><h2>${e(m.subject)}</h2><p class="mail-meta"><span>${e(m.from_name || m.from_addr)}</span> <span translate="no">&lt;${e(m.from_addr)}&gt;</span> · ${e(full(m.sent_at))}</p></div>
        <div class="actions"><select data-mail-category aria-label="Category">${Object.keys(CATS).map(c => `<option value="${c}" ${m.category === c ? 'selected' : ''}>${e(c)}</option>`).join('')}</select>
        ${m.status === 'new' ? `<button type="button" class="button" data-mail="done">${icon('check')}Mark as handled</button>` : `<button type="button" class="button" data-mail="undo">Back to "needs action"</button>`}</div></header>
      ${linkCard(d)}${aiCard(d)}
      <div class="mail-thread"><h3>Conversation <span class="count">${d.thread.length}</span></h3>${thread(d)}</div>
      <section class="mail-reply"><label for="mail-reply-text"><span>Reply to</span> <strong translate="no">${e(to)}</strong></label>
        <textarea id="mail-reply-text" data-mail-reply rows="7" placeholder="${nl() ? 'Schrijf uw antwoord…' : 'Write your reply…'}">${e(ui.reply)}</textarea>
        <div class="actions">${ai ? `<button type="button" class="button" data-mail="draft" ${ui.drafting ? 'disabled' : ''}>${icon('edit')}${ui.drafting ? 'Writing…' : 'Draft with AI'}</button>` : ''}
          <button type="button" class="button primary" data-mail="send" ${ui.sending ? 'disabled' : ''}>${ui.sending ? 'Sending…' : 'Send reply'}</button></div>
        <p class="muted"><span>The reply goes out from</span> <span translate="no">${e((ui.info && ui.info.address) || '')}</span><span>, in the same conversation, and stays with the home.</span></p></section>`;
  }

  function page() {
    const i = ui.info;
    const check = i && i.connected ? `<button type="button" class="button" data-mail="check" ${ui.checking ? 'disabled' : ''}>${icon('down')}${ui.checking ? 'Checking…' : 'Check for new email'}</button>` : '';
    const H = heading('Mailbox', 'Emails to the company, linked to the home and tenant. The AI summarises each email and suggests what to do.', check, 'MAILBOX');
    if (!ui.list && !ui.error) { setTimeout(load); return H + empty('Loading…', ''); }
    if (ui.error) return H + empty('Could not load the mailbox', ui.error);
    return H + statusLine() + `<section class="panel mail-panel${ui.sel ? ' has-sel' : ''}" data-no-filter><div class="mail-cols"><div class="mail-list-col">${listCol()}</div><div class="mail-read-col">${reader()}</div></div></section>`;
  }

  // ---- the emails of one home (on the home's page, below the cases) ----
  const homeMail = {};
  function panel(pid) {
    const h = homeMail[pid];
    if (!h || Date.now() - h.at > 60000) loadHome(pid);
    const list = h ? h.list : null;
    const body = !list ? '<p class="pf-cases-none">Loading…</p>' : !list.length ? '<p class="pf-cases-none">No emails about this home yet.</p>'
      : `<div class="mail-list mail-list-home">${list.slice(0, h.all ? 100 : 5).map(listItem).join('')}</div>${list.length > 5 && !h.all ? `<div class="panel-body tf-more"><button type="button" class="button link" data-mail-home-all="${pid}">Show all ${list.length} emails</button></div>` : ''}`;
    const waiting = list ? list.filter(m => m.status === 'new' && m.direction === 'in').length : 0;
    return `<section class="panel pf-mail-panel" id="pf-mail" data-pid="${pid}"><div class="panel-header"><div><h2>${icon('message')} Emails</h2>
      <p>${waiting ? `<strong class="late-text">${waiting === 1 ? '1 email waiting for an answer' : `${waiting} emails waiting for an answer`}</strong>` : 'The email conversations about this home, with the replies sent from the platform.'}</p></div>
      ${allowedPage('mailbox') ? '<div class="actions"><button type="button" class="button small" data-action="navigate" data-page="mailbox">Open mailbox</button></div>' : ''}</div>${body}</section>`;
  }
  async function loadHome(pid) {
    if (homeMail[pid] && homeMail[pid].busy) return;
    homeMail[pid] = {...(homeMail[pid] || {list: null}), busy: true, at: Date.now()};
    try {
      const r = await api(`/api/mail?company_id=${state.company}&property_id=${pid}`);
      const before = JSON.stringify(homeMail[pid].list);
      homeMail[pid] = {...homeMail[pid], list: r.messages.filter((m, i, all) => all.findIndex(x => x.thread_key === m.thread_key) === i), busy: false, at: Date.now()};
      const el = document.getElementById('pf-mail');
      if (el && Number(el.dataset.pid) === pid && before !== JSON.stringify(homeMail[pid].list)) el.outerHTML = panel(pid);
    } catch { homeMail[pid] = {...homeMail[pid], busy: false}; }
  }

  // ---- the dashboard: new emails in "Messages requiring action" ----
  const dash = {list: null, at: 0, busy: false};
  async function loadDash() {
    if (dash.busy || !state.company || !STAFF.includes(company().role)) return;
    dash.busy = true;
    try {
      const r = await api(`/api/mail?company_id=${state.company}&box=new`);
      const changed = JSON.stringify(r.messages.map(m => m.id)) !== JSON.stringify((dash.list || []).map(m => m.id));
      dash.list = r.connected || r.messages.length ? r.messages : []; dash.at = Date.now();
      if (changed && state.page === 'overview') render();
    } catch { dash.list = dash.list || []; }
    dash.busy = false;
  }
  function waiting() {
    if (!state.company || !STAFF.includes(company().role)) return [];
    if (!dash.list || Date.now() - dash.at > 120000) setTimeout(loadDash);
    return (dash.list || []).map(m => ({date: String(m.sent_at || '').slice(0, 10), text: m.subject, sub: [m.property_address || 'Unassigned', m.from_name || m.from_addr].filter(Boolean).join(' · '),
      attrs: `data-mail-open="${m.id}"`, urgent: !!m.urgent}));
  }

  // ---- forms ----
  function caseForm() {
    const d = ui.d, m = d.message, s = d.suggestion, first = (s.dates || []).find(x => x.kind === 'deadline') || (s.dates || [])[0];
    const kind = CASE_KINDS.includes(m.category) ? m.category : 'Other';
    formModal('Create a case from this email', [
      ['subject', 'Case title', 'text', {wide: true, value: (s.case && s.case.title) || m.subject}],
      selectField('kind', 'Category', CASE_KINDS, {value: kind}),
      selectField('priority', 'Priority', [['low', 'Low'], ['medium', 'Medium'], ['high', 'High'], ['urgent', 'Urgent']], {value: (s.case && s.case.priority) || (m.urgent ? 'urgent' : 'medium')}),
      selectField('assignee_id', 'Assigned to', d.team.map(u => [u.id, u.name]), {optional: true, required: false, value: state.me.id}),
      selectField('status', 'Status', [['open', 'Open⁠'], ['in progress', 'In progress'], ['waiting', 'Waiting for response']], {value: 'open'}),
      ['next_deadline', 'Deadline', 'date', {required: false, value: first ? first.date : ''}],
      ['deadline_note', 'What by then', 'text', {required: false, value: first ? first.what : ''}],
      ['note', 'Short description', 'textarea', {wide: true, required: false, value: summary(m)}],
    ], async data => { await api(`/api/mail/${m.id}/case`, {method: 'POST', body: data}); },
    {intro: 'The case appears on the home, on the dashboard and, with its deadline, in the calendar. Every email of this conversation stays linked to it.', submit: 'Create case', success: 'Case created.'});
  }
  function taskForm(i) {
    const m = ui.d.message, x = (ui.d.suggestion.dates || [])[i];
    if (!x) return;
    formModal('Add a task', [['title', 'Task', 'text', {wide: true, value: x.what || m.subject}], ['due_date', 'Date', 'date', {value: x.date}], ['owner', 'Assigned to', 'text', {value: state.me.name}]],
      async data => { await api(`/api/mail/${m.id}/task`, {method: 'POST', body: data}); },
      {intro: 'The task shows in the task list and in the calendar.', submit: 'Add task', success: 'Task added.'});
  }
  function fileForm(mailId, n) {
    const label = k => window.kreinDocs && window.kreinDocs.label ? window.kreinDocs.label(k) : k;
    const money = ['admin', 'manager', 'finance'].includes(company().role);
    formModal('Save to the documents of this home', [selectField('kind', 'Type', DOC_KINDS.filter(k => money || k !== 'supplier invoice').map(k => [k, label(k)]), {value: 'General', wide: true})],
      async data => { await api(`/api/mail/${mailId}/attachments/${n}`, {method: 'POST', body: data}); },
      {intro: 'The file is saved with the home (and the case of this email). Only PDF, JPEG, PNG and WebP files can be saved.', submit: 'Save', success: 'Saved to the documents.'});
  }

  // ---- hooks ----
  const baseRender = renderPage;
  renderPage = () => state.page === 'mailbox' ? page() : baseRender();
  const baseRefresh = refresh;
  refresh = async (...a) => {
    const r = await baseRefresh(...a);
    ui.homes = null;
    if (state.page === 'mailbox') { load(true); if (ui.sel) open(ui.sel, true); }
    else if (state.page === 'overview') loadDash();
    Object.keys(homeMail).forEach(k => { homeMail[k].at = 0; });
    return r;
  };

  async function loadHomes() {
    if (ui.homesLoading) return;
    ui.homesLoading = true;
    try { ui.homes = (await api('/api/mail/homes')).homes; } catch { ui.homes = false; }
    ui.homesLoading = false;
    if (state.page === 'mailbox' && ui.homes) render();
  }
  async function linkHome(id, pid) {
    const r = await post(`/api/mail/${id}/home`, {property_id: pid});
    if (!r) return;
    toast(r.moved ? (nl() ? `Gekoppeld aan de woning. De e-mail staat nu bij ${r.company}.` : `Linked to the home. The email is now under ${r.company}.`) : 'Linked to the home.');
    ui.homeEdit = false; open(id, true); load(true);
    if (typeof window.kreinMenuRefresh === 'function') window.kreinMenuRefresh();
  }

  async function post(url, body, okMsg) {
    try { const r = await api(url, {method: 'POST', body}); if (okMsg) toast(okMsg); return r; }
    catch (err) { toast(err.message); return null; }
  }

  document.addEventListener('click', async ev => {
    const o = ev.target.closest('[data-mail-open]');
    if (o) { ev.preventDefault(); open(Number(o.dataset.mailOpen)); return; }
    const bx = ev.target.closest('[data-mail-box]');
    if (bx) { ui.box = bx.dataset.mailBox; ui.cat = ''; remember(); ui.list = null; render(); load(true); return; }
    const hm = ev.target.closest('[data-mail-home]');
    if (hm && ui.d) { linkHome(ui.d.message.id, Number(hm.dataset.mailHome)); return; }
    const all = ev.target.closest('[data-mail-home-all]');
    if (all) { const pid = Number(all.dataset.mailHomeAll); homeMail[pid].all = true; document.getElementById('pf-mail').outerHTML = panel(pid); return; }
    const tk = ev.target.closest('[data-mail-task]');
    if (tk && ui.d) { taskForm(Number(tk.dataset.mailTask)); return; }
    const fl = ev.target.closest('[data-mail-file]');
    if (fl) { const [id, n] = fl.dataset.mailFile.split(':').map(Number); fileForm(id, n); return; }
    const b = ev.target.closest('[data-mail]');
    if (!b) return;
    const act = b.dataset.mail, d = ui.d, id = d && d.message.id;
    if (act === 'check') {
      ui.checking = true; render();
      const r = await post('/api/mailbox/check', {company_id: state.company});
      ui.checking = false;
      if (r) toast(r.busy ? 'The mailbox is being checked already.' : r.new ? `${r.new} new ${r.new === 1 ? 'email' : 'emails'}.` : 'No new emails.');
      load(true); if (typeof window.kreinMenuRefresh === 'function') window.kreinMenuRefresh();
      render();
    } else if (act === 'back') { ui.sel = null; ui.d = null; render(); }
    else if (act === 'done' || act === 'undo') {
      if (await post(`/api/mail/${id}`, {status: act === 'done' ? 'done' : 'new'}, act === 'done' ? 'Marked as handled.' : 'Back under "needs action".')) {
        const list = ui.list || [], at = list.findIndex(m => m.id === id);
        const next = act === 'done' && ['new', 'action', 'unassigned', 'urgent'].includes(ui.box) ? (list[at + 1] || list[at - 1]) : null;
        await load(true);
        if (next && (ui.list || []).some(m => m.id === next.id)) open(next.id); else open(id, true);
        refresh();
      }
    } else if (act === 'home-open' && d && d.property) showProperty(d.property.id);
    else if (act === 'case-open' && d && d.property) {
      showProperty(d.property.id);
      let n = 0; const t = setInterval(() => { const el = document.getElementById('pf-cases'); if (el || ++n > 20) { clearInterval(t); el && el.scrollIntoView({behavior: 'smooth', block: 'start'}); } }, 150);
    } else if (act === 'home-edit') { ui.homeEdit = true; render(); }
    else if (act === 'home-cancel') { ui.homeEdit = false; render(); }
    else if (act === 'home-save') {
      const v = Number(document.querySelector('[data-mail-home-select]')?.value || 0);
      if (!v) { toast('Choose a home.'); return; }
      linkHome(id, v);
    } else if (act === 'home-remove') {
      if (!confirm('Unlink this conversation from the home? It goes back to Unassigned.')) return;
      if (await post(`/api/mail/${id}/home`, {property_id: null}, 'Unlinked.')) { ui.homeEdit = false; open(id, true); load(true); }
    } else if (act === 'case-new') caseForm();
    else if (act === 'case-unlink') { if (await post(`/api/mail/${id}/case`, {case_id: 0}, 'Unlinked from the case.')) open(id, true); }
    else if (act === 'draft') {
      if (ui.reply.trim() && !confirm('Replace what you wrote with a draft from the AI?')) return;
      ui.drafting = true; render();
      const r = await post(`/api/mail/${id}/draft`, {lang: 'auto'});
      ui.drafting = false;
      if (r) ui.reply = r.body;
      render();
      document.getElementById('mail-reply-text')?.focus();
    } else if (act === 'send') {
      const text = (document.getElementById('mail-reply-text')?.value || '').trim();
      if (!text) { toast('Write your reply.'); return; }
      if (/\[\.\.\]/.test(text) && !confirm('Your reply still has [..] to fill in. Send it anyway?')) return;
      ui.sending = true; ui.reply = text; render();
      const r = await post(`/api/mail/${id}/reply`, {body: text, lang: nl() ? 'nl' : 'en'}, 'Reply sent.');
      ui.sending = false;
      if (r) { ui.reply = ''; await open(id, false); load(true); }
      else render();
    }
  });
  document.addEventListener('input', ev => {
    if (ev.target.matches('[data-mail-reply]')) ui.reply = ev.target.value;
    if (ev.target.matches('[data-mail-search]')) {
      clearTimeout(ui.t);
      ui.t = setTimeout(async () => {
        ui.q = ev.target.value.trim(); await load(true);
        const el = document.querySelector('[data-mail-search]');
        if (el) { el.focus(); el.setSelectionRange(el.value.length, el.value.length); }
      }, 350);
    }
  });
  document.addEventListener('change', async ev => {
    if (ev.target.matches('[data-mail-cat]')) { ui.cat = ev.target.value; render(); return; }
    if (ev.target.matches('[data-mail-category]') && ui.d) {
      if (await post(`/api/mail/${ui.d.message.id}`, {category: ev.target.value}, 'Category changed.')) { ui.d.message.category = ev.target.value; load(true); }
      return;
    }
    if (ev.target.matches('[data-mail-case-link]') && ui.d && ev.target.value) {
      if (await post(`/api/mail/${ui.d.message.id}/case`, {case_id: Number(ev.target.value)}, 'Added to the case.')) { open(ui.sel, true); load(true); }
    }
  });
  window.kreinMail = {panel, waiting, open, load: loadDash};
})();
