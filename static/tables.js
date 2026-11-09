'use strict';
/* Wide tables keep their last column (the buttons) in view: when a table is wider than its box, it gets .is-scrollable and
   the last column sticks to the right edge with a soft shadow (static/polish.css). Works for every page and portal. */
(() => {
  let queued = false;
  const mark = () => {
    queued = false;
    document.querySelectorAll('.table-wrap').forEach(w => w.classList.toggle('is-scrollable', w.scrollWidth > w.clientWidth + 2));
  };
  const soon = () => { if (!queued) { queued = true; requestAnimationFrame(mark); } };
  new MutationObserver(soon).observe(document.body, {childList: true, subtree: true});
  addEventListener('resize', soon);
  soon();
})();
