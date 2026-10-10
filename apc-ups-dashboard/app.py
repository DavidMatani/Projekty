import re, shutil, subprocess
from flask import Flask, jsonify, render_template_string

app = Flask(__name__)
APC = shutil.which('apcaccess') or '/usr/sbin/apcaccess'

def num(v):
    m = re.search(r'[-+]?\d+(?:\.\d+)?', v or '')
    return float(m.group()) if m else None

def status_data():
    p = subprocess.run([APC, 'status'], capture_output=True, text=True, check=True, timeout=5)
    r = {}
    for line in p.stdout.splitlines():
        if ':' in line:
            k, v = line.split(':', 1); r[k.strip()] = v.strip()
    load = num(r.get('LOADPCT')); power = num(r.get('NOMPOWER'))
    return {
        'model': r.get('MODEL','APC UPS'), 'name': r.get('UPSNAME','UPS'), 'status': r.get('STATUS','UNKNOWN'),
        'battery': num(r.get('BCHARGE')), 'runtime': num(r.get('TIMELEFT')), 'load': load,
        'watts': round(load*power/100) if load is not None and power else None, 'power': power,
        'linev': num(r.get('LINEV')), 'battv': num(r.get('BATTV')), 'date': r.get('BATTDATE'),
        'last': r.get('LASTXFER'), 'min_batt': num(r.get('MBATTCHG')), 'min_time': num(r.get('MINTIMEL')),
        'serial': r.get('SERIALNO')
    }

PAGE = '''<!doctype html><html lang="cs"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>APC UPS</title><style>
:root{color-scheme:dark;--bg:#07101f;--card:#121f35;--muted:#93a4bd;--green:#43d17d;--yellow:#f6c453;--blue:#5aa7ff}*{box-sizing:border-box}body{margin:0;font-family:system-ui;background:radial-gradient(circle at 20% 0,#17365d 0,transparent 32rem),var(--bg);color:#f5f7fb}.wrap{max-width:1050px;margin:auto;padding:30px 16px}header{display:flex;justify-content:space-between;align-items:center;gap:16px;margin-bottom:20px}h1{margin:0;font-size:clamp(27px,5vw,43px)}.sub,.label{color:var(--muted)}.pill{padding:10px 14px;border-radius:99px;background:#1b2b46;font-weight:800}.pill.online{color:var(--green)}.pill.batt{color:var(--yellow)}.grid{display:grid;grid-template-columns:1.2fr .8fr;gap:15px}.card{background:linear-gradient(180deg,#162642,var(--card));border:1px solid #ffffff12;border-radius:19px;padding:21px}.big{font-size:32px;font-weight:800}.row{display:flex;justify-content:space-between;gap:15px;align-items:center}.battery,.bar{height:24px;border-radius:8px;background:#ffffff12;overflow:hidden;margin:18px 0}.battery i,.bar i{display:block;height:100%;width:0;background:linear-gradient(90deg,var(--green),#55d6e8);transition:.3s}.bar i{background:var(--blue)}.stats{display:grid;grid-template-columns:repeat(2,1fr);gap:1px;background:#ffffff12;border-radius:14px;overflow:hidden}.stat{background:#101c30;padding:16px}.stat b{display:block;margin-top:5px;font-size:18px}.full{grid-column:1/-1}.error{margin-top:15px;padding:12px;border-radius:12px;background:#5a2229;display:none}@media(max-width:650px){.grid{grid-template-columns:1fr}.full{grid-column:auto}header{align-items:flex-start;flex-direction:column}.stats{grid-template-columns:1fr 1fr}}
</style></head><body><main class="wrap"><header><div><div class="sub">NAPÁJENÍ SERVERU</div><h1 id="model">APC UPS</h1><div class="sub" id="name">Načítám…</div></div><div class="pill" id="pill">NAČÍTÁM</div></header><section class="grid"><article class="card"><div class="row"><div><div class="label">Baterie</div><div class="big" id="battery">—</div></div><div><div class="label">Výdrž</div><div class="big" id="runtime">—</div></div></div><div class="battery"><i id="bfill"></i></div><div class="stats"><div class="stat"><span class="label">Napětí baterie</span><b id="battv">—</b></div><div class="stat"><span class="label">Datum baterie</span><b id="date">—</b></div></div></article><article class="card"><div class="row"><div><div class="label">Zatížení</div><div class="big" id="load">—</div></div><div><div class="label">Výkon</div><div class="big" id="watts">—</div></div></div><div class="bar"><i id="lfill"></i></div><div class="sub" id="maxpower">—</div></article><article class="card full"><div class="stats"><div class="stat"><span class="label">Síť</span><b id="linev">—</b></div><div class="stat"><span class="label">Poslední přechod</span><b id="last">—</b></div><div class="stat"><span class="label">Vypnout při baterii</span><b id="minb">—</b></div><div class="stat"><span class="label">Vypnout při výdrži</span><b id="mint">—</b></div></div></article></section><div class="error" id="error"></div></main><script>
const $=x=>document.getElementById(x),f=(v,u='')=>v==null?'—':`${v}${u}`;async function update(){try{let r=await fetch('/api/status',{cache:'no-store'}),j=await r.json();if(!r.ok)throw Error(j.error||'Chyba');$('model').textContent=j.model;$('name').textContent=j.name+(j.serial?' · S/N '+j.serial:'');let s=j.status||'UNKNOWN';$('pill').textContent=s.includes('ONBATT')?'NA BATERII':s.includes('ONLINE')?'ONLINE':s;$('pill').className='pill '+(s.includes('ONLINE')?'online':s.includes('ONBATT')?'batt':'');$('battery').textContent=f(j.battery,' %');$('runtime').textContent=f(j.runtime,' min');$('bfill').style.width=Math.max(0,Math.min(100,j.battery||0))+'%';$('battv').textContent=f(j.battv,' V');$('date').textContent=j.date||'—';$('load').textContent=f(j.load,' %');$('watts').textContent=f(j.watts,' W');$('lfill').style.width=Math.max(0,Math.min(100,j.load||0))+'%';$('maxpower').textContent='Maximum UPS: '+f(j.power,' W');$('linev').textContent=f(j.linev,' V');$('last').textContent=j.last||'—';$('minb').textContent=f(j.min_batt,' %');$('mint').textContent=f(j.min_time,' min');$('error').style.display='none'}catch(e){$('error').textContent='UPS: '+e.message;$('error').style.display='block'}}update();setInterval(update,5000);
</script></body></html>'''

@app.get('/')
def index(): return render_template_string(PAGE)
@app.get('/api/status')
def api_status():
    try: return jsonify(status_data())
    except Exception as e: return jsonify(error=str(e)), 503
@app.get('/healthz')
def health():
    try: status_data(); return jsonify(ok=True)
    except Exception as e: return jsonify(ok=False,error=str(e)), 503

if __name__ == '__main__': app.run('127.0.0.1', 8011)
