'use strict';
/* Contractors page: send a contractor the service agreement (rates, dates, company signature), follow its status, check the
   contractor's details and signature, and approve, return or withdraw it. Server side: app/contractor_agreements.py */
(() => {
  if (typeof renderPage !== 'function') return;
  const STATE = {sent: ['Waiting for the contractor', 'amber'], returned: ['Returned to the contractor', 'amber'], signed: ['Signed: needs your approval', 'blue'],
                 approved: ['Approved', 'green'], declined: ['Declined by the contractor', 'red']};
  const btn = (label, act, id = '', cls = 'small') => `<button type="button" class="button ${cls}" data-ca="${act}" data-id="${id}">${e(label)}</button>`;
  let companySigned = null;

  function panelHtml() {
    const offers = rows('contractor_offers'), waiting = offers.filter(o => o.status === 'signed').length;
    const name = id => (byId('contractors', id) || {}).name || '';
    const body = offers.length ? table(['Contractor', 'Sent', 'Rates', 'Status', ''], offers.map(o => [
      `<strong>${e(name(o.contractor_id))}</strong>${o.status === 'declined' && o.response_note ? `<small>${e(o.response_note)}</small>` : ''}`, e(dateLabel(o.sent_at.slice(0, 10))),
      `${money(o.hourly_cents)}/h<small>call-out ${money(o.callout_cents)} · +${o.emergency_percent}% emergency</small>`,
      badge(STATE[o.status][0], STATE[o.status][1]),
      `<div class="actions"><a class="button small" href="/api/contractor-agreements/${o.id}/file" data-pv-title="Service agreement">View PDF</a>${o.status === 'signed' ? btn('Review and approve', 'review', o.id, 'small primary') : ''}${['sent', 'returned', 'signed'].includes(o.status) ? btn('Withdraw', 'withdraw', o.id) : ''}</div>`]))
      : `<div class="empty"><h3>No agreements sent yet</h3><p>Press <strong>Send agreement</strong>. The contractor fills in the company details and signs in the portal; you then approve it.</p></div>`;
    const banner = waiting ? `<div class="notice lease-alert">${icon('check')}<span><strong>${waiting} signed agreement${waiting === 1 ? '' : 's'} waiting for your approval.</strong></span></div>` : '';
    return banner + panel('Service agreements', 'A Dutch framework agreement (overeenkomst van opdracht) with your rates, signed electronically by both parties.', body, btn('Send agreement', 'send', '', 'small primary'));
  }
  const baseRender = renderPage;
  renderPage = () => {
    const html = baseRender();
    if (state.page !== 'contractors' || !['admin', 'manager', 'property'].includes(company().role)) return html;
    return html.replace('<div class="section-stack">', () => '<div class="section-stack">' + panelHtml());
  };

  async function sendForm() {
    const withLogin = rows('contractors').filter(c => rows('contractor_accounts').some(a => a.contractor_id === c.id && a.enabled));
    if (!withLogin.length) { toast('Invite the contractor to the portal first (Contractor directory > Invite).'); return; }
    if (companySigned === null) { try { companySigned = !!(await api(`/api/companies/${state.company}/signature`)).set; } catch { companySigned = false; } }
    const intro = (companySigned ? 'Your stored signature is put on the agreement when it is sent.' : '<strong>Your signature is not set up yet</strong> (Settings > Your signature on contracts). The agreement goes out without it and you sign when you approve.')
      + ' The contractor then fills in KvK, btw, IBAN and insurance details and signs.';
    formModal('Send service agreement', [
      selectField('contractor_id', 'Contractor', withLogin.map(c => [c.id, `${c.name} · ${c.trade}`]), {wide: true}),
      ['start_date', 'Starts on', 'date', {value: today()}], ['end_date', 'Ends on (leave empty if open-ended)', 'date', {required: false}],
      ['hourly_rate', 'Hourly rate (€, excl. VAT)', 'number', {value: '60', step: '0.01'}], ['callout_fee', 'Call-out fee (€, excl. VAT)', 'number', {value: '35', step: '0.01', required: false}],
      ['emergency_percent', 'Emergency surcharge outside office hours (%)', 'number', {value: '50'}], ['quote_threshold', 'Quote required above (€, excl. VAT)', 'number', {value: '750'}],
      ['payment_days', 'Payment term (days)', 'number', {value: '30'}], ['warranty_months', 'Warranty on work (months)', 'number', {value: '6'}]],
      d => api('/api/contractor-agreements', {method: 'POST', body: d}), {intro, submit: 'Send to contractor', success: 'Agreement sent. The contractor signs it in the portal.'});
  }

  async function review(id) {
    const r = await api(`/api/contractor-agreements/${id}/review`);
    const rowsHtml = Object.entries(r.labels).filter(([k]) => r.values[k]).map(([k, l]) => `<tr><th>${e(l.replace(' | ', ' / '))}</th><td>${e(r.values[k])}</td></tr>`).join('');
    modal('Check and approve the agreement', `<p class="modal-note">${e((byId('contractors', r.contractor_id) || {}).name || '')} signed on ${e(dateLabel(r.signed_at.slice(0, 10)))}. Check the company details, then approve. The final PDF with both signatures becomes the contractor's agreement.</p>
      <div class="table-wrap"><table class="ca-values">${rowsHtml}</table></div>
      ${r.signature ? `<p class="modal-note">Signature</p><img class="ca-sig" src="${e(r.signature)}" alt="Contractor signature">` : ''}
      <form id="ca-approve">${r.client_signed ? '' : `<p class="modal-note"><strong>Sign for your company</strong> (your stored signature was not on it when it was sent)</p>${HavenSignature.html(state.me.name)}`}
        <label class="ca-confirm"><input type="checkbox" id="ca-ok"> I checked the details and the signature.</label>
        <p class="form-error" role="alert"></p>
        <div class="modal-footer"><a class="button" href="/api/contractor-agreements/${id}/file" data-pv-title="Service agreement">View PDF</a><button type="button" class="button" data-ca="return" data-id="${id}">Return with a note</button><button type="submit" class="button primary">Approve</button></div></form>`);
    const f = document.getElementById('ca-approve'), err = f.querySelector('.form-error');
    const pad = r.client_signed ? null : HavenSignature.bind(f, state.me.name, () => {});
    if (pad && pad.refit) requestAnimationFrame(() => pad.refit());
    f.addEventListener('submit', async ev => {
      ev.preventDefault();
      if (!f.querySelector('#ca-ok').checked) { err.textContent = 'Tick that you checked the details and the signature.'; return; }
      if (pad && !pad.ready()) { err.textContent = 'Sign for your company first.'; return; }
      try {
        const sig = pad ? await pad.data() : null;
        await api(`/api/contractor-agreements/${id}/approve`, {method: 'POST', body: {confirmed: true, ...(sig ? {signature: sig.dataUrl, signature_mode: sig.mode} : {})}});
        closeModal(); await refresh(); toast('Agreement approved. It is now the contractor\'s signed agreement.');
      } catch (e2) { err.textContent = e2.message; }
    });
  }

  document.addEventListener('click', async ev => {
    const el = ev.target.closest('[data-ca]');
    if (!el) return;
    ev.preventDefault();
    const id = el.dataset.id;
    try {
      if (el.dataset.ca === 'send') return await sendForm();
      if (el.dataset.ca === 'review') return await review(id);
      if (el.dataset.ca === 'return') {
        const note = prompt('What should the contractor correct?');
        if (!note) return;
        await api(`/api/contractor-agreements/${id}/return`, {method: 'POST', body: {note}}); closeModal(); await refresh(); toast('Returned to the contractor.');
      }
      if (el.dataset.ca === 'withdraw') {
        if (!confirm('Withdraw this agreement? The contractor can no longer sign it.')) return;
        await api(`/api/contractor-agreements/${id}/withdraw`, {method: 'POST', body: {}}); await refresh(); toast('Agreement withdrawn.');
      }
    } catch (err) { toast(err.message); }
  });
})();
