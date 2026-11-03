/* Signs a person out after a while without activity (KREIN_IDLE_MINUTES on the server, default 30 minutes).
   Mouse, keys, touch and scrolling count as activity and are reported to the server at most once a minute; the page's
   own background refresh does not count. A minute before the end a warning appears; at the end the page reloads to the
   sign-in screen, which then says why. Another open tab that is still in use keeps the session going. */
(function () {
  const PORTALS = ['tenant', 'contractor', 'owner', 'landlord', 'developer'];
  const portal = PORTALS.find(p => location.pathname === '/' + p) || '';
  const FLAG = 'krein.idle-signout';
  let limit = 0, lastActivity = Date.now(), lastPing = 0, pinging = false, warning = null;

  async function status(touch) {
    const headers = portal ? {'X-Krein-Portal': portal} : {};
    const r = await fetch('/api/session/active?touch=' + (touch ? 1 : 0), {headers, credentials: 'same-origin'});
    if (!r.ok) return null;
    return r.json();
  }

  async function ping() {
    if (pinging) return;
    pinging = true; lastPing = Date.now();
    try {
      const s = await status(true);
      limit = s && s.idle_minutes ? s.idle_minutes * 60 : 0;   // 0: not signed in, or no limit
    } catch (e) { /* offline: try again on the next activity */ }
    finally { pinging = false; }
  }

  function active() {
    lastActivity = Date.now();
    hideWarning();
    if (Date.now() - lastPing > 60000) ping();
  }

  function banner(text, id) {
    const el = document.createElement('div');
    el.id = id;
    el.setAttribute('role', 'alert');
    el.style.cssText = 'position:fixed;top:12px;left:50%;transform:translateX(-50%);z-index:1000;max-width:calc(100vw - 32px);' +
      'background:#fcf6e9;border:1px solid #eee2c4;color:#7a5d1e;padding:12px 16px;border-radius:8px;font-size:13px;' +
      'line-height:1.5;box-shadow:0 5px 20px #18352522;cursor:pointer';
    const span = document.createElement('span');
    span.textContent = text;
    el.appendChild(span);
    el.addEventListener('click', () => el.remove());
    document.body.appendChild(el);
    return el;
  }

  function hideWarning() {
    if (warning) { warning.remove(); warning = null; }
  }

  async function tick() {
    if (!limit) return;
    const idle = (Date.now() - lastActivity) / 1000;
    if (idle < limit - 60) return;
    if (idle < limit) {
      if (!warning) warning = banner('You will be signed out in 1 minute because there was no activity. Move the mouse or press a key to stay signed in.', 'idle-warning');
      return;
    }
    let s = null;
    try { s = await status(false); } catch (e) { return; }   // offline: decide when the connection is back
    if (s && s.seconds_left > 5) {   // still in use in another tab
      lastActivity = Date.now() - (limit - s.seconds_left) * 1000;
      hideWarning();
      return;
    }
    limit = 0;
    try { sessionStorage.setItem(FLAG, '1'); } catch (e) { /* private window: no message, still signed out */ }
    location.reload();
  }

  function init() {
    let signedOut = false;
    try { signedOut = sessionStorage.getItem(FLAG) === '1'; sessionStorage.removeItem(FLAG); } catch (e) { /* ignore */ }
    if (signedOut) {
      const el = banner('You were signed out because there was no activity for a while. Please sign in again.', 'idle-signed-out');
      // the staff app redraws the whole page for its sign-in screen: put the message back until it is clicked or 15 seconds pass
      let closed = false;
      el.addEventListener('click', () => { closed = true; });
      const keep = setInterval(() => { if (!closed && !el.isConnected) document.body.appendChild(el); }, 300);
      setTimeout(() => { clearInterval(keep); el.remove(); }, 15000);
    }
    ['mousemove', 'mousedown', 'keydown', 'touchstart', 'scroll', 'wheel'].forEach(ev => document.addEventListener(ev, active, {passive: true, capture: true}));
    document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') tick(); });
    setInterval(tick, 15000);
    ping();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
