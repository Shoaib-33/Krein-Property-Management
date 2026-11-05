'use strict';
/* Workspace > Owner enquiries: property owners who asked for an introduction through the company website's form
   (/kennismaking, app/website.py). Staff call them back, mark them contacted or closed with a short note on the outcome,
   or delete them. Plugs into app.js (navigation, page rendering, helpers) without editing it. */
(() => {
  if (typeof navGroups === 'undefined' || typeof renderPage !== 'function') return;
  const after = navGroups[0][1].findIndex(n => n[0] === 'registrations');
  navGroups[0][1].splice(after >= 0 ? after + 1 : navGroups[0][1].length, 0, ['enquiries', 'Owner enquiries', 'building']);

  const ROLES = ['admin', 'manager', 'property'];
  const baseAllowed = allowedPage;
  allowedPage = page => page === 'enquiries' ? ROLES.includes(company().role) : baseAllowed(page);
  const ui = {list: null, company: null, filter: 'new', error: ''};
  const baseRender = renderPage;
  renderPage = () => state.page === 'enquiries' ? page() : baseRender();

  const STATUS = {new: ['New', 'blue'], contacted: ['Contacted', 'amber'], closed: ['Closed', 'gray']};
  const COUNT = {'1': '1 home', '2-5': '2 to 5 homes', '6-20': '6 to 20 homes', '20+': 'More than 20 homes'};
  const TYPE = {apartment: 'Apartment', house: 'Single-family house', studio: 'Studio or room', other: 'Other'};
  const SITUATION = {'': 'Not given', vacant: 'Vacant', let: 'Let', buying: 'Still to be bought'};
  const SERVICE = {letting: 'Letting', management: 'Property management', maintenance: 'Maintenance', full: 'Full management', unsure: 'Not sure yet'};
  const services = v => String(v || '').split(',').filter(Boolean).map(s => SERVICE[s] || s).join(', ');
  const btn = (label, act, id, cls = 'small') => `<button type="button" class="button ${cls}" data-enq="${act}" data-id="${id}">${e(label)}</button>`;

  async function load() {
    ui.company = state.company;
    try {
      ui.list = (await api(`/api/enquiries?company_id=${state.company}`)).enquiries;
      ui.error = '';
    } catch (err) {
      ui.list = [];
      ui.error = err.message;
    }
    if (state.page === 'enquiries') render();
  }

  function page() {
    if (!ui.list || ui.company !== state.company) {
      if (!ui.loading || ui.company !== state.company) {
        ui.list = null; ui.company = state.company; ui.loading = true;
        setTimeout(() => load().finally(() => { ui.loading = false; }));
      }
      return heading('Owner enquiries', 'Loading…', '', 'OWNERS');
    }
    const counts = {all: ui.list.length};
    ui.list.forEach(r => { counts[r.status] = (counts[r.status] || 0) + 1; });
    const shown = ui.filter === 'all' ? ui.list : ui.list.filter(r => r.status === ui.filter);
    const tabs = [['new', 'New'], ['contacted', 'Contacted'], ['closed', 'Closed'], ['all', 'All']]
      .map(([k, l]) => `<button type="button" class="${ui.filter === k ? 'active' : ''}" data-enq-filter="${k}">${e(l)} (${counts[k] || 0})</button>`).join('');
    const body = shown.map(r => {
      const [label, tone] = STATUS[r.status] || STATUS.new;
      return [
        `<strong>${e(r.name)}</strong><br><small>${e(r.email)} · ${e(r.phone)}</small>`,
        `${e(r.location)}<br><small>${e(COUNT[r.property_count] || r.property_count)} · ${e(TYPE[r.property_type] || r.property_type)}</small>`,
        e(services(r.services)),
        `${e(dateLabel(r.created_at))}<br>${badge(label, tone)}`,
        btn('Details', 'view', r.id) + (r.status === 'new' ? ' ' + btn('Mark contacted', 'contacted', r.id, 'small primary') : ''),
      ];
    });
    return heading('Owner enquiries', 'Property owners who asked for an introduction on the company website. Call them back, then mark them contacted or closed with a short note. Enquiries are deleted after 12 months.', '', 'OWNERS')
      + (ui.error ? notice(ui.error, true) : '')
      + `<div class="tabs">${tabs}</div>`
      + table(['Owner', 'Property', 'Services', 'Received', ''], body, ui.filter === 'new' ? 'No new enquiries. Requests from the website form appear here.' : 'Nothing here.');
  }

  const find = id => ui.list.find(r => r.id === Number(id));

  function view(r) {
    detail(r.name, [
      ['Email', r.email], ['Phone', r.phone], ['Property', r.location],
      ['Number of homes', COUNT[r.property_count] || r.property_count], ['Type of home', TYPE[r.property_type] || r.property_type],
      ['Current situation', SITUATION[r.situation] ?? r.situation], ['Services', services(r.services)],
      ['Message', r.message || '—'], ['Came from page', r.source_page || '—'], ['Received on', dateLabel(r.created_at)],
      ['Status', (STATUS[r.status] || STATUS.new)[0] + (r.handled_by_name ? ` · ${r.handled_by_name}` : '')], ['Follow-up note', r.note || '—'],
    ], `<div class="modal-footer">${btn('Delete enquiry', 'delete', r.id, 'danger')}${btn('Update status', 'status', r.id, 'primary')}</div>`);
  }

  function status(r) {
    formModal('Update enquiry', [
      selectField('status', 'Status', [['new', 'New'], ['contacted', 'Contacted'], ['closed', 'Closed']], {value: r.status === 'new' ? 'contacted' : r.status}),
      ['note', 'Note on the follow-up', 'textarea', {value: r.note, required: false, wide: true, hint: 'For example: called on Monday, sending a proposal.'}],
    ], async data => {
      await api(`/api/enquiries/${r.id}/status`, {method: 'POST', body: data});
      ui.list = null;
    }, {submit: 'Save', success: 'Enquiry updated.'});
  }

  document.addEventListener('click', async ev => {
    const tab = ev.target.closest('[data-enq-filter]');
    if (tab) { ui.filter = tab.dataset.enqFilter; render(); return; }
    const el = ev.target.closest('[data-enq]');
    if (!el) return;
    const r = find(el.dataset.id);
    if (!r) return;
    try {
      if (el.dataset.enq === 'view') view(r);
      else if (el.dataset.enq === 'status') { closeModal(); status(r); }
      else if (el.dataset.enq === 'contacted') {
        await api(`/api/enquiries/${r.id}/status`, {method: 'POST', body: {status: 'contacted'}});
        await load();
        toast('Marked as contacted.');
      } else if (el.dataset.enq === 'delete') {
        if (!confirm(`Delete the enquiry of ${r.name}? This cannot be undone.`)) return;
        await api(`/api/enquiries/${r.id}`, {method: 'DELETE'});
        closeModal();
        await load();
        toast('Enquiry deleted.');
      }
    } catch (err) {
      toast(err.message);
    }
  });
})();
