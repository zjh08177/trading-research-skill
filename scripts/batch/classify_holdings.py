import json
h=json.load(open('/Users/bytedance/.claude/jobs/f5e850a4/tmp/holdings.json'))
# kind for pack driver + sector for portfolio. Hand-classified.
KIND={'TSLA':'equity','AMD':'equity','META':'equity','AAOI':'equity','MSFT':'equity','NVDA':'equity',
 'AMZN':'equity','AVGO':'equity','VST':'equity','AAPL':'equity','SNPS':'equity','HOOD':'equity',
 'MRVL':'equity','TEM':'equity','COST':'equity','GOOG':'equity','SOFI':'equity','RGTI':'equity',
 'CRDO':'equity','FIGR':'equity','PONY':'adr','WRD':'adr','SPCX':'equity','CAI':'equity','KLAR':'equity',
 'RVI':'equity','RKLB':'equity','ASTS':'equity','NOK':'adr',
 'XLE':'etf','XLF':'etf','NASA':'etf','METU':'etf',
 'BTC':'crypto','ETH':'crypto','DOGE':'crypto','XRP':'crypto'}
SECTOR={'TSLA':'Auto/EV','AMD':'Semis','META':'Megacap platform','AAOI':'Optical/Networking',
 'MSFT':'Megacap platform','NVDA':'Semis','AMZN':'Megacap platform','AVGO':'Semis','VST':'Power/Utilities',
 'AAPL':'Megacap platform','SNPS':'Semis (EDA)','HOOD':'Fintech','MRVL':'Semis','TEM':'Healthcare-AI',
 'COST':'Consumer staples','GOOG':'Megacap platform','SOFI':'Fintech','RGTI':'Quantum','CRDO':'Semis/Networking',
 'FIGR':'Fintech/Crypto','PONY':'Auto/AV','WRD':'Auto/AV','SPCX':'Space','CAI':'Speculative/Other',
 'KLAR':'Fintech','RVI':'Speculative/Other','RKLB':'Space','ASTS':'Space','NOK':'Telecom/Networking',
 'XLE':'Energy (ETF)','XLF':'Financials (ETF)','NASA':'Space (ETF)','METU':'Leveraged META (ETF)',
 'BTC':'Crypto','ETH':'Crypto','DOGE':'Crypto','XRP':'Crypto'}
EXCLUDE={'FDRXX':'cash MMF','SPAXX':'cash MMF','O92E':'other ~$8','TG3Y':'other ~$0','PS':'$32 / 1 share (negligible)'}
rows=h['holdings']; book=h['total_book']
analyzable=[]; excluded=[]
for r in rows:
    s=r['symbol']
    if s in EXCLUDE: excluded.append((s,r['market_value'],EXCLUDE[s])); continue
    analyzable.append((s,KIND.get(s,'equity'),SECTOR.get(s,'?'),r['market_value'],r['pct_of_book']))
analyzable.sort(key=lambda x:-x[3])
print(f"BOOK ${book:,.0f} | analyzable={len(analyzable)} | excluded={len(excluded)}")
print("\n== ANALYZABLE (deep-dive set) ==")
for s,k,sec,mv,pct in analyzable: print(f"  {s:6} {k:7} {sec:22} ${mv:>10,.0f}  {pct:5.2f}%")
print("\n== EXCLUDED ==")
for s,mv,why in excluded: print(f"  {s:6} ${mv:>8,.2f}  {why}")
# sector aggregation
from collections import defaultdict
agg=defaultdict(float)
for s,k,sec,mv,pct in analyzable: agg[sec]+=pct
print("\n== SECTOR WEIGHTS (% of book) ==")
for sec,p in sorted(agg.items(),key=lambda x:-x[1]): print(f"  {sec:24} {p:5.2f}%")
# coarse groups
GROUP={'Semis':'AI/Semis complex','Semis (EDA)':'AI/Semis complex','Semis/Networking':'AI/Semis complex',
 'Optical/Networking':'AI/Semis complex','Megacap platform':'Megacap platform',
 'Crypto':'Crypto','Fintech':'Fintech','Fintech/Crypto':'Fintech','Space':'Space/Frontier',
 'Space (ETF)':'Space/Frontier','Quantum':'Space/Frontier','Auto/AV':'Auto/EV+AV','Auto/EV':'Auto/EV+AV'}
g=defaultdict(float)
for s,k,sec,mv,pct in analyzable: g[GROUP.get(sec,sec)]+=pct
print("\n== COARSE THEME WEIGHTS ==")
for sec,p in sorted(g.items(),key=lambda x:-x[1]): print(f"  {sec:24} {p:5.2f}%")
json.dump({s:{'kind':k,'sector':sec} for s,k,sec,mv,pct in analyzable}, open('/Users/bytedance/.claude/jobs/f5e850a4/tmp/classmap.json','w'),indent=1)
