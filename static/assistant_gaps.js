'use strict';
/* The developer's "Chatbot questions" page: questions the tenant help chat could not answer well from the repair knowledge
   base (app/knowledge/tenant_repairs.md). Use them to extend the knowledge base, then mark them handled.
   Server: app/repair_help.py. Plugs into app.js without editing it. */
(() => {
  if (typeof navGroups === 'undefined' || typeof renderPage !== 'function') return;
  const isDev = () => !!(state.me && state.me.developer);
  const admin = navGroups.find(g => g[1].some(n => n[0] === 'settings'));
  if (admin) admin[1].push(['chat-gaps', 'Chatbot questions', 'message']);
  const baseAllowed = allowedPage;
  allowedPage = page => page === 'chat-gaps' ? isDev() : baseAllowed(page);

  const ui = {d: null, error: '', loading: false};
  async function load() {
    if (ui.loading) return;
    ui.loading = true;
    try { ui.d = await api('/api/developer/assistant-gaps'); ui.error = ''; }
    catch (err) { ui.error = err.message; }
    ui.loading = false;
    if (state.page === 'chat-gaps') render();
  }
  const when = v => v ? e(String(v).replace('T', ' ').slice(0, 16)) : '—';
  const REASON = {ai_unsure: ['AI was not sure', 'amber'], no_match: ['No answer found', 'red']};
  const WHO = {tenant: 'Tenant', staff: 'Staff', owner: 'Owner', contractor: 'Contractor'};

  function page() {
    const H = heading('Chatbot questions', 'Questions the help chat could not answer well. Add the answers to the knowledge base, then mark them handled.', '', 'DEVELOPER');
    if (!ui.d && !ui.error) { setTimeout(load); return H + empty('Loading…', ''); }
    if (ui.error) return H + empty('Could not load the questions', ui.error);
    const open = ui.d.items.filter(x => !x.handled_at), done = ui.d.items.filter(x => x.handled_at);
    const row = x => {
      const [label, tone] = REASON[x.reason] || [x.reason, 'gray'];
      return [when(x.created_at), e(WHO[x.audience] || x.audience), e(x.question), `<span class="badge ${tone}">${e(label)}</span>`,
        `<div class="actions">${x.handled_at ? '' : `<button type="button" class="button small" data-cg="handled" data-id="${x.id}">Handled</button>`}<button type="button" class="button small" data-cg="delete" data-id="${x.id}">Delete</button></div>`];
    };
    const cols = ['When', 'Who', 'Question (contact details masked)', 'Why', ''];
    const info = panel('How this works', 'Kept for 180 days. Names, phone numbers, emails and bank numbers are masked before they are saved.',
      `<div class="panel-body"><p class="muted">The tenant chat answers repair questions from <code>app/knowledge/tenant_repairs.md</code> (${ui.d.sections.length} sections). When a question appears here often, add a section or a few words to the right heading, then mark the questions handled.</p></div>`);
    return H + `<div class="section-stack">${info}`
      + panel(`To look at (${open.length})`, '', table(cols, open.map(row), 'Nothing to look at. The chat could answer every question.'))
      + (done.length ? panel(`Handled (${done.length})`, '', table(cols, done.map(row), '')) : '') + '</div>';
  }
  const baseRender = renderPage;
  renderPage = () => state.page === 'chat-gaps' ? page() : baseRender();

  document.addEventListener('click', async ev => {
    const el = ev.target.closest('[data-cg]');
    if (!el) return;
    ev.preventDefault();
    try {
      if (el.dataset.cg === 'handled') { await api(`/api/developer/assistant-gaps/${el.dataset.id}/handled`, {method: 'POST'}); await load(); toast('Marked as handled.'); }
      if (el.dataset.cg === 'delete') { await api(`/api/developer/assistant-gaps/${el.dataset.id}`, {method: 'DELETE'}); await load(); toast('Question deleted.'); }
    } catch (err) { toast(err.message); }
  });
})();
