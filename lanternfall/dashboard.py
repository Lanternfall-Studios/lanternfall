"""Live dashboard: a standard-library HTTP server that renders the world.

Run a season with --serve and open http://localhost:8765/ in a browser.
The page polls /state.json every 2 seconds. Read-only: it never changes
the world, it just shows it.
"""

from __future__ import annotations

import html
import json
import threading
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
async function tick(){
  const s = await (await fetch('/state.json')).json();
  document.getElementById('meta').textContent =
    'tick ' + s.tick + ' · town vault (tolls): ' + s.town_vault + ' sparks';
  const tbl = (rows) => '<table>' + rows.map(r =>
    '<tr>' + r.map(c => '<td>' + c + '</td>').join('') + '</tr>').join('') + '</table>';
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
  let commonsHtml = '<p>' + (c.name || c.zone || 'commons') + ' (' + (c.item || '') +
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
    '<p>bids: ' + (s.bids.map(o => o.owner + ' buys ' + o.qty + '×' + o.item +
      ' @ ' + o.price).join('; ') || '—') + '</p>' +
    '<p>asks: ' + (s.asks.map(o => o.owner + ' sells ' + o.qty + '×' + o.item +
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
      legend += '<span style="color:' + c + '">■</span> ' + item +
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


def make_handler(engine):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/":
                body = PAGE.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/state.json":
                body = json.dumps(engine.world.snapshot()).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args):  # keep the console clean
            pass

    return Handler


def serve_in_background(engine, port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(engine))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server
