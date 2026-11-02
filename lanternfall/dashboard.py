"""Live dashboard: a standard-library HTTP server that renders the world.

Run a season with --serve and open http://localhost:8765/ in a browser.
The original viewer remains read-only. Opt-in shared-room controls submit bounded
JSON actions with actor-scoped local session capabilities; only the tick owner
changes world state. Loopback-only, not a public multiplayer security boundary.
"""

from __future__ import annotations

import json
import threading
import secrets
from urllib.parse import urlsplit, parse_qs
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Lanternfall — live</title>
<style>
body{font-family:system-ui,sans-serif;max-width:900px;margin:2em auto;padding:0 1em}
table{border-collapse:collapse} td,th{border:1px solid #ccc;padding:4px 8px;text-align:left}
pre{background:#111;color:#ddd;padding:1em;overflow:auto;max-height:300px}
</style></head>
<body><h1>Lanternfall — an MMO for agents (live)</h1>
<p id="meta">loading…</p>
<h2>Players</h2><div id="players"></div>
<h2>Commons — shared-stock meter</h2><div id="commons"></div>
<h2>Nodes</h2><div id="nodes"></div>
<h2>Market</h2><div id="market"></div>
<h2>Price history</h2><div id="prices"></div>
<h2>Recent events</h2><pre id="events"></pre>
<script>
const esc = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function tick(){
  const s = await (await fetch('/state.json')).json();
  document.getElementById('meta').textContent =
    'tick ' + s.tick + ' · town vault (tolls): ' + s.town_vault + ' sparks';
  const tbl = (rows) => '<table>' + rows.map(r =>
    '<tr>' + r.map(c => '<td>' + esc(c) + '</td>').join('') + '</tr>').join('') + '</table>';
  document.getElementById('players').innerHTML = tbl(
    [['name','zone','sparks','levels','quests','trades','inventory']].concat(
      Object.entries(s.players).map(([n,p]) => [n, p.zone, p.sparks,
        p.total_level, p.quests_done, p.trades_done,
        Object.entries(p.inventory).map(([k,v]) => k + '×' + v).join(', ') || '—'])));
  // Commons meter: stock/cap bar + stock-vs-tick chart, so the tragedy
  // of the commons is visible live instead of only in the final numbers.
  const c = s.commons || {};
  const ch = s.commons_history || [];
  const pct = c.cap ? Math.round(100 * c.stock / c.cap) : 0;
  const ccolor = c.stock === 0 ? '#d62728' : pct < 25 ? '#ff7f0e' : '#2ca02c';
  const cstatus = c.stock === 0 ? 'DEPLETED' : pct < 25 ? 'low' : 'healthy';
  const depleted = ch.find(h => h.stock === 0);
  let commonsHtml = '<p>' + esc(c.name || c.zone || 'commons') + ' (' + esc(c.item || '') +
    '): ' + c.stock + '/' + c.cap + ' (' + pct + '%) — ' + cstatus +
    (depleted ? ', first depleted at tick ' + depleted.tick : ', never depleted') + '</p>' +
    '<div style="background:#eee;width:860px;height:22px;border:1px solid #ccc">' +
    '<div style="background:' + ccolor + ';width:' + pct + '%;height:100%"></div></div>';
  if (ch.length > 1) {
    const W = 860, H = 160, PAD = 30;
    const maxTick = Math.max(...ch.map(h => h.tick), 1);
    const maxCap = Math.max(c.cap || 1, 1);
    const pts = ch.map(h =>
      (PAD + (h.tick / maxTick) * (W - PAD - 10)).toFixed(1) + ',' +
      (H - PAD - (h.stock / maxCap) * (H - PAD - 14)).toFixed(1)).join(' ');
    commonsHtml += '<svg width="' + W + '" height="' + H +
      '" style="background:#fafafa;border:1px solid #ccc;margin-top:8px">' +
      '<line x1="' + PAD + '" y1="' + (H - PAD) + '" x2="' + W + '" y2="' +
      (H - PAD) + '" stroke="#999"/>' +
      '<line x1="' + PAD + '" y1="0" x2="' + PAD + '" y2="' + (H - PAD) +
      '" stroke="#999"/>' +
      '<polyline points="' + pts + '" fill="none" stroke="' + ccolor +
      '" stroke-width="2"/>' +
      '<text x="4" y="12" font-size="11" fill="#666">cap ' + maxCap + '</text>' +
      '<text x="' + (W - 4) + '" y="' + (H - 8) +
      '" text-anchor="end" font-size="11" fill="#666">tick ' + maxTick + '</text>' +
      '</svg>';
  }
  document.getElementById('commons').innerHTML = commonsHtml;
  document.getElementById('nodes').innerHTML = tbl(
    [['zone','item','stock']].concat(
      Object.entries(s.nodes).flatMap(([z,n]) => {
        const rows = [[(s.zones[z] || {}).name || z,
          n.item, n.stock + '/' + n.cap]];
        if (n.secondary) rows.push([(s.zones[z] || {}).name || z,
          n.secondary.item + ' (2nd)', n.secondary.stock + '/' + n.secondary.cap]);
        return rows;
      })));
  document.getElementById('market').innerHTML =
    '<p>bids: ' + (s.bids.map(o => esc(o.owner) + ' buys ' + o.qty + '×' + esc(o.item) +
      ' @ ' + o.price).join('; ') || '—') + '</p>' +
    '<p>asks: ' + (s.asks.map(o => esc(o.owner) + ' sells ' + o.qty + '×' + esc(o.item) +
      ' @ ' + o.price).join('; ') || '—') + '</p>';
  document.getElementById('events').textContent =
    s.recent_events.map(e => 't' + e.tick + ' ' + e.kind + ' ' +
      JSON.stringify(e)).join('\\n');
  // Price history: one SVG line per traded item, price vs tick, with the
  // last trade price in the legend. No trades yet -> placeholder text.
  const hist = s.market_history || [];
  if (!hist.length) {
    document.getElementById('prices').innerHTML = '<p>no trades yet</p>';
  } else {
    const byItem = {};
    hist.forEach(t => (byItem[t.item] = byItem[t.item] || []).push(t));
    const W = 860, H = 220, PAD = 30;
    const maxTick = Math.max(...hist.map(t => t.tick), 1);
    const maxPrice = Math.max(...hist.map(t => t.price), 1);
    const colors = ['#1f77b4','#d62728','#2ca02c','#9467bd','#ff7f0e','#17becf'];
    let svg = '<svg width="' + W + '" height="' + H +
      '" style="background:#fafafa;border:1px solid #ccc">';
    svg += '<line x1="' + PAD + '" y1="' + (H - PAD) + '" x2="' + W + '" y2="' +
      (H - PAD) + '" stroke="#999"/><line x1="' + PAD + '" y1="0" x2="' + PAD +
      '" y2="' + (H - PAD) + '" stroke="#999"/>';
    svg += '<text x="' + (W - 4) + '" y="' + (H - 8) +
      '" text-anchor="end" font-size="11" fill="#666">tick ' + maxTick + '</text>';
    svg += '<text x="4" y="12" font-size="11" fill="#666">' + maxPrice +
      ' sparks</text>';
    let legend = '';
    Object.keys(byItem).forEach((item, i) => {
      const pts = byItem[item].map(t =>
        (PAD + (t.tick / maxTick) * (W - PAD - 10)).toFixed(1) + ',' +
        (H - PAD - (t.price / maxPrice) * (H - PAD - 14)).toFixed(1)).join(' ');
      const c = colors[i % colors.length];
      svg += '<polyline points="' + pts + '" fill="none" stroke="' + c +
        '" stroke-width="2"/>';
      const last = byItem[item][byItem[item].length - 1];
      legend += '<span style="color:' + c + '">■</span> ' + esc(item) +
        ' (last ' + last.price + ' @ t' + last.tick + ', ' +
        byItem[item].length + ' trades) &nbsp; ';
    });
    svg += '</svg>';
    document.getElementById('prices').innerHTML = svg + '<p>' + legend + '</p>';
  }
}
tick(); setInterval(tick, 2000);
</script></body></html>
"""


CONTROLS = """
<section><h2>Shared room controls</h2>
<p>Local session slots, not verified personal identities. One legal action per turn.
Library and Forge are workstations inside Haven. Requests never execute shell commands.</p>
<label>Actor <select id="actor">
<option>chris</option><option>jon</option><option>steward</option><option>engineer</option>
<option>steve</option><option>tom</option><option>thales</option></select></label>
<label>Session capability <input id="token" type="password" autocomplete="off"></label>
<p><label>Destination <select id="destination"></select></label>
<button onclick="sendAction(['move', document.getElementById('destination').value])">Move</button>
<button onclick="sendAction(['gather'])">Harvest</button>
<button onclick="sendAction(['idle'])">Idle</button></p>
<p><label>Item / recipe <input id="item" size="14"></label>
<button onclick="sendAction(['craft', itemValue()])">Craft</button>
<button onclick="sendAction(['merchant_sell', itemValue()])">Floor sell</button>
<label>Price <input id="price" type="number" min="0" value="5" size="5"></label>
<label>Quantity <input id="qty" type="number" min="1" value="1" size="5"></label>
<button onclick="marketAction('bid')">Bid</button><button onclick="marketAction('ask')">Ask</button></p>
<p><label>Legal action JSON <input id="action" size="65" value='["idle"]'></label>
<button onclick="sendRaw()">Queue action</button></p>
<p><label>Chat / work directive <input id="chat" maxlength="500" size="65"
placeholder="@steward audit commons depletion"></label>
<button onclick="sendChat()">Say</button></p>
<p id="feedback" role="status"></p>
<h3>Work orders (inert outbox; verification requires a trusted worker)</h3><pre id="orders"></pre>
<details><summary>My view / proposals / recipes</summary><pre id="view"></pre></details>
</section>
"""
CONTROL_SCRIPT = """
const actorEl = document.getElementById('actor');
const tokenEl = document.getElementById('token');
const params = new URLSearchParams(location.search);
if (params.has('actor')) actorEl.value = params.get('actor');
const fragment = new URLSearchParams(location.hash.slice(1));
if (fragment.has('token')) {
  sessionStorage.setItem('lanternfall-' + actorEl.value, fragment.get('token'));
  history.replaceState(null, '', location.pathname + location.search);
}
function loadToken(){tokenEl.value=sessionStorage.getItem('lanternfall-'+actorEl.value)||'';}
loadToken(); actorEl.onchange=loadToken;
function itemValue(){return document.getElementById('item').value;}
async function api(path, body){
  sessionStorage.setItem('lanternfall-'+actorEl.value, tokenEl.value);
  const response=await fetch(path, {method:body?'POST':'GET',
    headers:{'Content-Type':'application/json','Authorization':'Bearer '+tokenEl.value},
    ...(body?{body:JSON.stringify({...body,actor:actorEl.value})}:{})});
  const result=await response.json();
  if(!response.ok) throw new Error(result.error||'request failed');
  return result;
}
async function sendAction(action){
  try {document.getElementById('feedback').textContent=JSON.stringify(await api('/api/action',{action}));}
  catch(e){document.getElementById('feedback').textContent=e.message;}
}
function sendRaw(){try{sendAction(JSON.parse(document.getElementById('action').value));}
  catch(e){document.getElementById('feedback').textContent='Invalid action JSON';}}
function marketAction(side){sendAction([side,itemValue(),Number(document.getElementById('price').value),Number(document.getElementById('qty').value)]);}
async function sendChat(){try{
  document.getElementById('feedback').textContent=JSON.stringify(await api('/api/chat',{text:document.getElementById('chat').value}));
  document.getElementById('chat').value='';
}catch(e){document.getElementById('feedback').textContent=e.message;}}
async function controlsTick(){try{
  const state=await(await fetch('/state.json')).json();
  const select=document.getElementById('destination');
  if(!select.options.length) Object.entries(state.zones).forEach(([id,z])=>{
    const opt=document.createElement('option');opt.value=id;opt.textContent=z.name+' ('+id+')';select.appendChild(opt);});
  document.getElementById('orders').textContent=JSON.stringify(state.work_orders||[],null,2);
  if(state.room_error) document.getElementById('feedback').textContent=state.room_error;
  if(tokenEl.value) document.getElementById('view').textContent=JSON.stringify(await api('/api/view?actor='+encodeURIComponent(actorEl.value)),null,2);
}catch(e){document.getElementById('feedback').textContent=e.message;}}
controlsTick();setInterval(controlsTick,1000);
"""
INTERACTIVE_PAGE = PAGE.replace('<h2>Players</h2>', CONTROLS + '<h2>Players</h2>').replace(
    '</script>', CONTROL_SCRIPT + '</script>')


def make_handler(engine, room=None, tokens=None):
    tokens = tokens or {}

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def respond(self, status, value, content_type="application/json"):
            body = value.encode() if isinstance(value, str) else json.dumps(value, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(body)

        def local_request(self):
            port = self.server.server_port
            allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
            return (self.headers.get("Host") in allowed and
                    self.headers.get("Origin") in (None, *("http://" + host for host in allowed)))

        def authorized(self, actor):
            expected = tokens.get(actor)
            supplied = self.headers.get("Authorization", "")
            return expected and supplied.isascii() and secrets.compare_digest(supplied, "Bearer " + expected)

        def do_GET(self):
            if not self.local_request():
                self.respond(403, {"error": "local origin required"})
                return
            route = urlsplit(self.path)
            if route.path == "/":
                self.respond(200, INTERACTIVE_PAGE if room else PAGE, "text/html")
            elif route.path == "/state.json":
                self.respond(200, room.snapshot() if room else engine.world.snapshot())
            elif route.path == "/api/view" and room:
                try:
                    query = parse_qs(route.query, max_num_fields=1)
                    actor = query["actor"][0]
                    if not self.authorized(actor):
                        self.respond(403, {"error": "actor session required"})
                        return
                    self.respond(200, room.view(actor))
                except (ValueError, KeyError):
                    self.respond(400, {"error": "invalid view request"})
            else:
                self.respond(404, {"error": "not found"})

        def do_POST(self):
            if not self.local_request() or not room:
                self.respond(403, {"error": "interactive local origin required"})
                return
            if self.path not in {"/api/action", "/api/chat"}:
                self.respond(404, {"error": "not found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 4096 or self.headers.get("Transfer-Encoding"):
                    self.respond(413, {"error": "bounded body required"})
                    return
                if self.headers.get("Content-Type") != "application/json":
                    self.respond(415, {"error": "JSON required"})
                    return
                def unique(pairs):
                    result = {}
                    for key, value in pairs:
                        if key in result:
                            raise ValueError("duplicate field")
                        result[key] = value
                    return result
                def invalid_constant(value):
                    raise ValueError("invalid constant")
                data = json.loads(self.rfile.read(length), object_pairs_hook=unique,
                                  parse_constant=invalid_constant)
                field = "action" if self.path == "/api/action" else "text"
                if not isinstance(data, dict) or set(data) != {"actor", field} or not isinstance(data["actor"], str):
                    raise ValueError("invalid request shape")
                if not self.authorized(data["actor"]):
                    self.respond(403, {"error": "actor session required"})
                    return
                action = data[field] if field == "action" else ["say", data[field]]
                self.respond(202, room.submit(data["actor"], action))
            except (ValueError, TypeError, RecursionError):
                self.respond(400, {"error": "invalid action, unknown actor, paused room, or full queue"})

        def log_message(self, *args):
            pass  # never log session capabilities or user chat

    return Handler


def serve_in_background(engine, port: int, room=None) -> ThreadingHTTPServer:
    tokens = {name: secrets.token_urlsafe(32) for name in engine.by_name} if room else {}
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(engine, room, tokens))
    server.actor_tokens = tokens
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server
