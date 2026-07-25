# -*- coding: utf-8 -*-
from __future__ import annotations
import json, ssl, time, urllib.error, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler

BASE='https://api.mexc.com'; SYMBOL='TAO_USDT'; COUNT=300; N=12; WICK=40
TFS={'15m':('Min15',900),'1h':('Min60',3600)}

def req(path, params=None):
    url=BASE+path+('?' + urllib.parse.urlencode(params) if params else '')
    r=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 TAO-MEXC-Vercel/1.0','Accept':'application/json'})
    try:
        with urllib.request.urlopen(r,timeout=8,context=ssl.create_default_context()) as x:
            data=json.loads(x.read().decode('utf-8'))
    except Exception as e:
        raise RuntimeError(f'MEXC connection error: {e!r}')
    if not isinstance(data,dict) or data.get('success') is False:
        raise RuntimeError(f'Bad MEXC response: {data}')
    return data

def utc(ts): return datetime.fromtimestamp(ts,tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')

def ticker():
    d=req('/api/v1/contract/ticker',{'symbol':SYMBOL}).get('data')
    if isinstance(d,list):
        d=next((x for x in d if x.get('symbol')==SYMBOL),None)
    if not isinstance(d,dict): raise RuntimeError('Ticker unavailable')
    return d

def candles(api_tf,secs):
    now=int(time.time()); start=now-secs*(COUNT+10)
    d=req(f'/api/v1/contract/kline/{SYMBOL}',{'interval':api_tf,'start':start,'end':now}).get('data')
    keys=('time','open','high','low','close','vol')
    if not isinstance(d,dict) or any(k not in d for k in keys): raise RuntimeError('Klines unavailable')
    m=min(len(d[k]) for k in keys); out=[]
    for i in range(m):
        ts=int(d['time'][i]); out.append({'time':ts,'time_utc':utc(ts),'open':float(d['open'][i]),'high':float(d['high'][i]),'low':float(d['low'][i]),'close':float(d['close'][i]),'volume':float(d['vol'][i])})
    return sorted(out,key=lambda x:x['time'])[-COUNT:]

def detect(cs):
    c2=[]; c3=[]; bull=[False]*len(cs); bear=[False]*len(cs)
    for i in range(1,len(cs)):
        p,c=cs[i-1],cs[i]; w=cs[max(0,i-N+1):i+1]
        b=(c['low']==min(x['low'] for x in w) and p['close']<p['open'] and c['low']<p['low'] and c['close']>p['low'])
        s=(c['high']==max(x['high'] for x in w) and p['close']>p['open'] and c['high']>p['high'] and c['close']<p['high'])
        bull[i]=b; bear[i]=s
        if b or s:
            rng=c['high']-c['low']; wt=.01*WICK*rng
            big=(min(c['close'],c['open'])-c['low']>wt) if b else (c['high']-max(c['close'],c['open'])>wt)
            c2.append({'direction':'BULLISH' if b else 'BEARISH','time':c['time'],'time_utc':c['time_utc'],'previous_direction':'BEARISH' if p['close']<p['open'] else 'BULLISH','open':c['open'],'high':c['high'],'low':c['low'],'close':c['close'],'big_wick_40_percent':big})
        if i>=2:
            pb=cs[i-1]
            be=bear[i-1] and c['high']<pb['high'] and c['close']<pb['low']
            bu=bull[i-1] and c['low']>pb['low'] and c['close']>pb['high']
            if be or bu:
                c3.append({'direction':'BULLISH' if bu else 'BEARISH','time':c['time'],'time_utc':c['time_utc'],'after_candle2_time_utc':pb['time_utc'],'open':c['open'],'high':c['high'],'low':c['low'],'close':c['close']})
    return c2,c3

def build():
    with ThreadPoolExecutor(max_workers=3) as ex:
        ft=ex.submit(ticker); fs={k:ex.submit(candles,*v) for k,v in TFS.items()}
        t=ft.result(); raw={k:f.result() for k,f in fs.items()}
    out={}
    now=int(time.time())
    for k,(_,secs) in TFS.items():
        r=raw[k]; closed=[x for x in r if x['time']+secs<=now]; c2,c3=detect(closed)
        out[k]={'seconds_per_candle':secs,'latest_live_candle':r[-1] if r else None,'latest_closed_candle':closed[-1] if closed else None,'recent_closed_candles':closed[-40:],'recent_candle2':c2[-20:],'recent_candle3':c3[-20:]}
    return {'ok':True,'source':'MEXC Futures public API','symbol':SYMBOL,'fetched_at_unix':now,'fetched_at_utc':utc(now),'current_price':t.get('lastPrice'),'high_24h':t.get('high24Price'),'low_24h':t.get('lower24Price'),'settings':{'reversal_filter_enabled':True,'filter_length':N,'wick_percent':WICK},'timeframes':out}

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try: body=json.dumps(build(),ensure_ascii=False,separators=(',',':')).encode(); code=200
        except Exception as e: body=json.dumps({'ok':False,'error':str(e)},ensure_ascii=False).encode(); code=502
        self.send_response(code); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Cache-Control','no-store'); self.end_headers(); self.wfile.write(body)
