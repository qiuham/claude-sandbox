"""浏览器里执行的脚本（Playwright evaluate 用）。"""

from __future__ import annotations

WEBRTC_JS = """
new Promise((res) => {
  const out = [];
  const pc = new RTCPeerConnection({iceServers:[{urls:'stun:stun.l.google.com:19302'}]});
  pc.createDataChannel('x');
  pc.onicecandidate = (e) => {
    const c = e.candidate && e.candidate.candidate;
    if (!c) { res(out); return; }
    out.push(c);
  };
  pc.createOffer().then(o => pc.setLocalDescription(o)).catch(() => res(out));
  setTimeout(() => res(out), 8000);
})
"""


HARVEST_JS = """
async () => {
  const get = async (u) => {
    try {
      const r = await fetch(u, { headers: { accept: 'application/json' } });
      if (!r.ok) return null;
      return await r.json();
    } catch (e) { return null; }
  };
  const [orgs, acct] = await Promise.all([get('/api/organizations'), get('/api/account')]);
  return { orgs, acct };
}
"""
