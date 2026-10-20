'use strict';
/* Every file field in Krein (staff, tenant, owner and contractor screens): a file that is too large, or the wrong kind,
   is refused the moment it is chosen, with a clear message, before anything is uploaded. The server checks again.
   Limits: videos KREIN_MAX_VIDEO_MB (data-max-mb on the field, default 50), the company logo 1 MB, everything else 10 MB. */
(() => {
  const nl = () => (document.documentElement.lang || '').startsWith('nl');
  const mb = n => (n / 1024 / 1024).toFixed(n < 10 * 1024 * 1024 ? 1 : 0);
  function rules(input) {
    const accept = (input.getAttribute('accept') || '').toLowerCase();
    if (input.dataset.maxMb) return {max: Number(input.dataset.maxMb) * 1024 * 1024, video: accept.includes('video')};
    if (accept.includes('video')) return {max: 50 * 1024 * 1024, video: true};
    if (input.id === 'logo-file') return {max: 1024 * 1024, video: false};
    if (accept.includes('.csv')) return {max: 5 * 1024 * 1024, video: false, other: true};       // CSV imports
    if (accept.includes('.xml') || accept.includes('.sta')) return {max: 5 * 1024 * 1024, video: false, other: true};   // bank statements
    return {max: 10 * 1024 * 1024, video: false};
  }
  const OK_DOC = /\.(pdf|jpe?g|png|webp)$/i, OK_TYPE = /^(application\/pdf|image\/(jpeg|png|webp))$/;
  const OK_VIDEO = /\.(mp4|mov|webm)$/i, OK_VTYPE = /^video\/(mp4|quicktime|webm)$/;
  document.addEventListener('change', ev => {
    const input = ev.target;
    if (!(input instanceof HTMLInputElement) || input.type !== 'file' || !input.files || !input.files.length) return;
    const r = rules(input), files = [...input.files], most = Number(input.dataset.maxFiles || (input.multiple ? 5 : 1));
    let problem = '';
    if (files.length > most) problem = nl() ? `Kies hoogstens ${most} bestanden.` : `Choose at most ${most} files.`;
    for (const f of files) {
      if (problem) break;
      if (f.size > r.max) problem = nl() ? `${f.name} is ${mb(f.size)} MB. Het maximum is ${mb(r.max)} MB.` : `${f.name} is ${mb(f.size)} MB. The limit is ${mb(r.max)} MB.`;
      else if (r.video && !(OK_VTYPE.test(f.type) || OK_VIDEO.test(f.name))) problem = nl() ? 'Kies een MP4-, MOV- of WebM-video.' : 'Choose an MP4, MOV or WebM video.';
      else if (!r.video && !r.other && input.id !== 'logo-file' && /pdf|image\//.test(input.accept) && !(OK_TYPE.test(f.type) || OK_DOC.test(f.name)))
        problem = /\.hei[cf]$/i.test(f.name) ? (nl() ? 'Dit is een HEIC-foto van een iPhone. Upload hem vanaf de telefoon zelf (dan wordt het JPEG) of kies JPEG.' : 'This is an iPhone HEIC photo. Upload it from the phone itself (it becomes JPEG) or choose a JPEG.')
          : (nl() ? 'Kies een PDF, JPEG, PNG of WebP.' : 'Choose a PDF, JPEG, PNG or WebP file.');
    }
    input.setCustomValidity(problem);
    if (problem) {
      input.reportValidity();
      input.value = '';
      setTimeout(() => input.setCustomValidity(''), 0);
      const form = input.closest('form'), box = form && form.querySelector('.form-error');
      if (box) box.textContent = problem; else if (typeof toast === 'function') toast(problem); else alert(problem);
    } else {
      const form = input.closest('form'), box = form && form.querySelector('.form-error');
      if (box && box.textContent) box.textContent = '';
    }
  });
})();
