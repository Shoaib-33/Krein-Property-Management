'use strict';
/* Spare keys of a home (app/keys.py): which keys the office keeps, how many, where, and who has one out. Shown on the
   home's page, tab Technical. Plugs into property_file.js. */
(() => {
  if (typeof renderPage !== 'function') return;
  const EDIT = ['admin', 'manager', 'property'];
  const mb = (label, attrs, primary = false) => `<button type="button" class="button small${primary ? ' primary' : ''}" ${attrs}>${e(label)}</button>`;
  const tr = s => window.kreinT ? kreinT(s) : s;

  function panel(f, role) {
    const list = f.keys || [], can = EDIT.includes(role);
    const body = table(['Key', 'Number', 'Kept at', 'Where is it now?', ''], list.map(k => [
      `<strong>${e(k.label)}</strong>${k.note ? `<small>${e(k.note)}</small>` : ''}`, e(k.copies), e(k.storage || '—'),
      k.holder ? `${badge('Out', 'amber')} <span>${e(k.holder)}</span><small>${e(tr('since'))} ${e(dateLabel(k.out_since))}</small>` : badge('In the office', 'green'),
      can ? `<span class="actions">${k.holder ? mb('Back in the office', `data-key="in" data-id="${k.id}"`) : mb('Hand out', `data-key="out" data-id="${k.id}"`)}${mb('Edit', `data-key="edit" data-id="${k.id}"`)}${mb('Delete', `data-key="delete" data-id="${k.id}"`)}</span>` : '']),
      'No spare key registered yet.');
    return panel_('Spare keys', 'The keys of this home that the office keeps, where they are kept, and who has one out.', body + (can ? `<div class="panel-body tf-more">${mb('Add key', 'data-key="add"', true)}</div>` : ''));
  }
  const panel_ = (...a) => window.panel(...a);

  function form(p, k) {
    formModal(k ? 'Edit key' : 'Add a spare key', [
      ['label', 'Which key?', 'text', {value: k ? k.label : '', wide: true, placeholder: 'For example: front door and mailbox'}],
      ['copies', 'How many keys?', 'number', {value: k ? k.copies : 1, step: '1', min: 1}],
      ['storage', 'Kept at', 'text', {value: k ? k.storage : '', required: false, placeholder: 'For example: key cabinet, hook 12'}],
      ['note', 'Note (optional)', 'text', {value: k ? k.note : '', required: false, wide: true}]],
    async v => { await api(k ? `/api/property-keys/${k.id}` : `/api/properties/${p.id}/keys`, {method: 'POST', body: v}); },
    {submit: 'Save', success: 'Key saved.'});
  }
  window.kreinKeys = {panel};

  document.addEventListener('click', async ev => {
    const el = ev.target.closest('[data-key]');
    if (!el) return;
    ev.preventDefault();
    const f = window.kreinPropertyFile && kreinPropertyFile();
    if (!f || !f.property) return;
    const k = (f.keys || []).find(x => x.id === Number(el.dataset.id)), act = el.dataset.key;
    if (act === 'add') form(f.property);
    else if (act === 'edit' && k) form(f.property, k);
    else if (act === 'out' && k) formModal(`Hand out: ${k.label}`, [['holder', 'Who gets the key?', 'text', {wide: true, placeholder: 'For example: the plumber, or a colleague'}]],
      async v => { await api(`/api/property-keys/${k.id}/out`, {method: 'POST', body: v}); }, {submit: 'Hand out', success: 'Key handed out.'});
    else if (act === 'in' && k) { await api(`/api/property-keys/${k.id}/out`, {method: 'POST', body: {holder: ''}}); toast(tr('Key is back in the office.')); await refresh(); }
    else if (act === 'delete' && k) { if (!confirm(tr('Delete this key from the register?'))) return; await api(`/api/property-keys/${k.id}/delete`, {method: 'POST'}); toast(tr('Deleted.')); await refresh(); }
  });
})();
