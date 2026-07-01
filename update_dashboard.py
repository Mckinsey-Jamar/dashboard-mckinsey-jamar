#!/usr/bin/env python3
"""update_dashboard.py — Dashboard McKinsey-Jamar (v4 — robusto)"""
import os, re, json, base64, urllib.request, urllib.error
from collections import defaultdict
from datetime import date, datetime, timedelta

JIRA_BASE  = "https://hubdigitaljamar.atlassian.net"
JIRA_EMAIL = os.environ["JIRA_EMAIL"]
JIRA_TOKEN = os.environ["JIRA_TOKEN"]
GH_PAT     = os.environ["GH_PAT"]
GH_REPO    = "Joha-22/dashboard-mckinsey-jamar"
TODAY      = date.today().isoformat()
WEEK_END   = (date.today() + timedelta(days=7)).isoformat()

# Totales conocidos (se actualizan manualmente cuando hay cambios de scope)
KNOWN_TOTALS = {
    "SOE":99,"DEP":139,"MSOP":29,"MEJ":37,"PROVED":37,"ECI":28,"DEIT":67,
    "SLOBM":14,"JCTR":58,"SLOBDECO":20,"SLOBDECPA":6,"RCD3":43,"IMPCSE":24,
    "MIOT":1,"OPR":20,"ZT5F":13,"FCCDA":34,"WF5":28,"SDR":24,"PDSP":96,
    "IM":21,"OP":48,"TLGDL":12,"EEMOC":10,"SF":47,"EODV":42,"ISMC":3,
    "IEPRFEEFDC":70,"ICD":34,"CODCEYBM":9,"IPDPCDAR":50,"IPDPCDBR":54,
    "PTMZR":12,"RF1D":9,"PROP":22,"MDCB":9,
}

SW_TO_MO = {
    "SOE":"MO-1","DEP":"MO-2","MSOP":"MO-3","MEJ":"MO-4","PROVED":"MO-5",
    "ECI":"MO-7","DEIT":"MO-8","SLOBM":"MO-10","JCTR":"MO-65",
    "SLOBDECO":"MO-66","SLOBDECPA":"MO-67","RCD3":"MO-24","IMPCSE":"MO-28",
    "MIOT":"MO-26","OPR":"MO-76","ZT5F":"MO-33","FCCDA":"MO-34","WF5":"MO-35",
    "SDR":"MO-36","PDSP":"MO-37","IM":"MO-40","OP":"MO-41","TLGDL":"MO-44",
    "EEMOC":"MO-45","SF":"MO-46","EODV":"MO-47","ISMC":"MO-55",
    "IEPRFEEFDC":"MO-54","ICD":"MO-56","CODCEYBM":"MO-50",
    "IPDPCDAR":"MO-58","IPDPCDBR":"MO-59","PTMZR":"MO-11",
    "RF1D":"MO-89","PROP":"MO-88","MDCB":"MO-52",
}
MO_TO_SW = {v: k for k, v in SW_TO_MO.items()}
ALL_SW   = ",".join(SW_TO_MO.keys())

def jira_auth():
    cred = base64.b64encode((JIRA_EMAIL+":"+JIRA_TOKEN).encode()).decode()
    return {"Authorization":"Basic "+cred,"Accept":"application/json","Content-Type":"application/json"}

def jira_post(jql, fields=None, max_results=100):
    body = {"jql": jql, "maxResults": max_results}
    if fields:
        body["fields"] = fields if isinstance(fields, list) else [fields]
    req = urllib.request.Request(
        JIRA_BASE+"/rest/api/3/search/jql",
        data=json.dumps(body).encode(), headers=jira_auth(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            d = json.loads(r.read())
            return d.get("issues", [])
    except Exception as ex:
        print("  JIRA ERR: "+str(ex)[:80]+" | JQL: "+jql[:50])
        return []

def gh_headers():
    return {"Authorization":"token "+GH_PAT,"Accept":"application/vnd.github.v3+json",
            "Content-Type":"application/json"}

def gh_get(path):
    req = urllib.request.Request(
        "https://api.github.com/repos/"+GH_REPO+"/contents/"+path,
        headers=gh_headers())
    with urllib.request.urlopen(req) as r:
        d = json.loads(r.read())
    return base64.b64decode(d["content"]).decode("utf-8"), d["sha"]

def gh_put(path, content, sha, message):
    data = json.dumps({"message":message,
        "content":base64.b64encode(content.encode()).decode(),
        "sha":sha,"branch":"main"}).encode()
    req = urllib.request.Request(
        "https://api.github.com/repos/"+GH_REPO+"/contents/"+path,
        data=data, headers=gh_headers(), method="PUT")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())

def clean(s):
    return (s or "").strip().replace('"','\\"').replace("'","\\'")

def get_pais(fv):
    if not fv: return ""
    vals = fv if isinstance(fv,list) else [fv]
    vals = [v.get("value","") if isinstance(v,dict) else str(v) for v in vals]
    col = any("olombia" in v or v=="COL" for v in vals)
    pan = any("anam" in v or v=="PAN" for v in vals)
    return "Global" if col and pan else "PAN" if pan else "COL" if col else ""

def replace_var(html, name, new_content):
    """Reemplaza var NAME = {...} usando balance de llaves."""
    s = html.find("var "+name+" = {")
    if s == -1: return html
    depth=0; pos=s+len("var "+name+" = ")
    while pos < len(html):
        if html[pos]=="{": depth+=1
        elif html[pos]=="}":
            depth-=1
            if depth==0:
                return html[:s]+new_content+html[pos+1:]
        pos+=1
    return html

def fmt_task(t):
    return ("    {key:'"+t["key"]+"',summary:\""+t["summary"]+"\","
            "duedate:'"+t.get("due","")+"',assignee:\""+t["assignee"]+"\" }")

print("Actualizacion — "+TODAY)

# ── 1. MO statuses ─────────────────────────────────────────────────────────────
print("MO statuses...")
mo_issues = jira_post("project = MO ORDER BY key ASC",
    ["summary","status","customfield_11057","customfield_11197"])
MO_STATUS = {}
for i in mo_issues:
    f=i["fields"]; st=f["status"]["name"].split(":")[0].strip()
    ow_list=f.get("customfield_11057") or []
    ow=ow_list[0].get("value","Sin asignar") if ow_list else "Sin asignar"
    MO_STATUS[i["key"]]={"status":st,"owner":ow,"pais":get_pais(f.get("customfield_11197"))}
print("  MO: "+str(len(MO_STATUS)))

# ── 2. Conteos por proyecto: query ALL issues por lotes pequeños ───────────────
print("Conteos por proyecto (no-Done por lotes)...")
# Estrategia: buscar issues NO-Done (funciona con statusCategory != Done)
# y calcular done = total - no_done
not_done_by=defaultdict(int)
prog_by=defaultdict(int)
sw_list=list(KNOWN_TOTALS.keys())

for i in range(0, len(sw_list), 5):
    batch=",".join(sw_list[i:i+5])
    # Querie de no-Done (sabemos que funciona statusCategory != Done)
    issues=jira_post(
        "project in ("+batch+") AND statusCategory != Done ORDER BY project ASC",
        ["status","project"], 200)
    for iss in issues:
        proj=iss["fields"]["project"]["key"]
        cat=iss["fields"]["status"].get("statusCategory",{}).get("key","")
        not_done_by[proj]+=1
        if cat=="indeterminate":
            prog_by[proj]+=1

# done = total - not_done
done_by=defaultdict(int)
for sw,total in KNOWN_TOTALS.items():
    nd=not_done_by.get(sw,0)
    done_by[sw]=max(0, total-nd)

print("  Done="+str(sum(done_by.values()))+" Prog="+str(sum(prog_by.values())))

# ── 3. Tardías (filtro doble) ──────────────────────────────────────────────────
print("Tardias...")
late_issues=jira_post(
    "project in ("+ALL_SW+") AND due < '"+TODAY+"' AND statusCategory != Done "
    "ORDER BY project ASC, due ASC",
    ["summary","status","duedate","assignee","project"],100)
late_by_sw=defaultdict(list)
for i in late_issues:
    cat=i["fields"]["status"].get("statusCategory",{}).get("key","")
    if cat=="done": continue  # doble filtro
    proj=i["fields"]["project"]["key"]; f=i["fields"]
    late_by_sw[proj].append({"key":i["key"],"summary":clean(f["summary"]),
        "due":f.get("duedate",""),
        "assignee":clean((f.get("assignee") or {}).get("displayName","Sin asignar"))})
total_late=sum(len(v) for v in late_by_sw.values())
print("  Tardias: "+str(total_late))

# ── 4. Construir COUNTS ────────────────────────────────────────────────────────
sw_counts={}
for sw,total in KNOWN_TOTALS.items():
    d=done_by.get(sw,0); p=prog_by.get(sw,0); l=len(late_by_sw.get(sw,[]))
    sw_counts[sw]=(total,d,p,max(0,total-d-p),l)

# ── 5. Descargar y actualizar HTML ─────────────────────────────────────────────
print("HTML...")
html,sha=gh_get("index.html")

# MO statuses
for mk,vals in MO_STATUS.items():
    ns=vals["status"]; no=vals["owner"].replace("'","\\'"); np=vals["pais"]
    html=re.sub(r"(key:'"+re.escape(mk)+r"'[^,\n]*?,frente:[^,\n]*?,subfrente:[^,\n]*?,summary:[^,\n]*?,status:')[^']+'",
                r"\g<1>"+ns+"'",html,count=1)
    html=re.sub(r"(key:'"+re.escape(mk)+r"'[^}]*?owner:')[^']+'",
                r"\g<1>"+no+"'",html,count=1)
    if np:
        html=re.sub(r"(key:'"+re.escape(mk)+r"'[^}]*?pais:')[^']+'",
                    r"\g<1>"+np+"'",html,count=1)

# SW_PROJECTS y DATA
for sw,(t,d,p,td,l) in sw_counts.items():
    nt="tasks:{total:"+str(t)+",done:"+str(d)+",prog:"+str(p)+",todo:"+str(td)+",late:"+str(l)+"}"
    html=re.sub(r"('"+re.escape(sw)+r"'\s*:\s*\{[^}]*?)tasks\s*:\s*(?:\{[^}]+\}|null)",
                r"\g<1>"+nt,html,count=1)
for mo,sw in MO_TO_SW.items():
    if sw not in sw_counts: continue
    t,d,p,td,l=sw_counts[sw]
    nt="{total:"+str(t)+",done:"+str(d)+",prog:"+str(p)+",todo:"+str(td)+",late:"+str(l)+"}"
    html=re.sub(r"(key:'"+re.escape(mo)+r"'[^}]*?,tasks:)(\{[^}]+\}|null)",
                r"\g<1>"+nt,html,count=1)

# LATE_TASKS
lt=["var LATE_TASKS = {","  // "+TODAY+" — "+str(total_late)+" tardias (no-Done)"]
for sw,mo in sorted(SW_TO_MO.items(),key=lambda x:int(x[1].split("-")[1])):
    tasks=late_by_sw.get(sw,[])
    if not tasks: continue
    lt.append("  '"+mo+"': [")
    lt+=[fmt_task(t)+("," if j<len(tasks)-1 else "") for j,t in enumerate(tasks)]
    lt.append("  ],")
lt.append("};")
html=replace_var(html,"LATE_TASKS","\n".join(lt))

# Subir
now_str=datetime.now().strftime("%H:%M")
result=gh_put("index.html",html,sha,"auto: actualizacion Jira — "+TODAY+" "+now_str+" COT")
print("✅ Commit: "+result["commit"]["sha"][:7])
print("   Done="+str(sum(done_by.values()))+" Prog="+str(sum(prog_by.values()))+" Late="+str(total_late))
