#!/usr/bin/env python3
"""
update_dashboard.py
Script de actualización automática del Dashboard McKinsey-Jamar.
Consulta Jira en tiempo real y actualiza index.html en el repositorio.
"""

import os, re, json, base64, urllib.request, urllib.error
from collections import defaultdict
from datetime import date, datetime, timedelta

# ── Configuración ──────────────────────────────────────────────────────────────
JIRA_BASE   = "https://hubdigitaljamar.atlassian.net"
JIRA_EMAIL  = os.environ["JIRA_EMAIL"]
JIRA_TOKEN  = os.environ["JIRA_TOKEN"]
GH_PAT      = os.environ["GH_PAT"]
GH_REPO     = "Joha-22/dashboard-mckinsey-jamar"
GH_BRANCH   = "main"
CLOUD_ID    = "d048be2e-ba23-411d-9c1a-1de60db0b49c"

TODAY       = date.today().isoformat()
WEEK_END    = (date.today() + timedelta(days=7)).isoformat()

# ── Helpers ────────────────────────────────────────────────────────────────────
def jira_auth():
    cred = base64.b64encode(f"{JIRA_EMAIL}:{JIRA_TOKEN}".encode()).decode()
    return {"Authorization": f"Basic {cred}", "Accept": "application/json",
            "Content-Type": "application/json"}

def jira_search(jql, fields, max_results=100):
    url = f"{JIRA_BASE}/rest/api/3/search"
    payload = json.dumps({"jql": jql, "fields": fields, "maxResults": max_results}).encode()
    req = urllib.request.Request(url, data=payload, headers=jira_auth(), method="POST")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())["issues"]

def gh_headers():
    return {"Authorization": f"token {GH_PAT}",
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json"}

def gh_get_file(path):
    url = f"https://api.github.com/repos/{GH_REPO}/contents/{path}"
    req = urllib.request.Request(url, headers=gh_headers())
    with urllib.request.urlopen(req) as r:
        d = json.loads(r.read())
    return base64.b64decode(d["content"]).decode("utf-8"), d["sha"]

def gh_put_file(path, content, sha, message):
    url  = f"https://api.github.com/repos/{GH_REPO}/contents/{path}"
    data = json.dumps({"message": message, "content": base64.b64encode(content.encode()).decode(),
                       "sha": sha, "branch": GH_BRANCH}).encode()
    req  = urllib.request.Request(url, data=data, headers=gh_headers(), method="PUT")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())

def clean(s):
    return (s or "").strip().replace('"', '\\"').replace("'", "\\'")

def get_option(fv):
    if not fv: return ""
    if isinstance(fv, list): return fv[0].get("value","") if fv else ""
    if isinstance(fv, dict): return fv.get("value","")
    return str(fv)

def norm_pais(fv):
    if not fv: return ""
    vals = fv if isinstance(fv, list) else [fv]
    vals = [v.get("value","") if isinstance(v,dict) else str(v) for v in vals]
    col = any("olombia" in v or v=="COL" for v in vals)
    pan = any("anam"    in v or v=="PAN" for v in vals)
    return "Global" if col and pan else "PAN" if pan else "COL" if col else ""

# ── Mapeos ────────────────────────────────────────────────────────────────────
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
MO_TO_SW = {v:k for k,v in SW_TO_MO.items()}

ALL_SW = ",".join(SW_TO_MO.keys())

# ── Consultas Jira ────────────────────────────────────────────────────────────
print(f"🔄 Consultando Jira — {TODAY}")

# 1. MO statuses / owners / pais
mo_issues = jira_search(
    "project = MO ORDER BY key ASC",
    ["summary","status","customfield_11057","customfield_11197"]
)
MO_STATUS = {}
for i in mo_issues:
    f = i["fields"]
    st = f["status"]["name"].split(":")[0].strip()
    ow = (f.get("customfield_11057") or [{}])[0].get("value","Sin asignar")
    pa = norm_pais(f.get("customfield_11197"))
    MO_STATUS[i["key"]] = {"status":st, "owner":ow, "pais":pa}
print(f"  ✅ MO: {len(MO_STATUS)} iniciativas")

# 2. Conteos por sub-proyecto
sw_counts = {}
for sw in SW_TO_MO:
    issues = jira_search(f"project = {sw} ORDER BY status ASC",
                         ["status"], max_results=200)
    done = sum(1 for i in issues if i["fields"]["status"]["statusCategory"]["key"]=="done")
    prog = sum(1 for i in issues if i["fields"]["status"]["statusCategory"]["key"]=="indeterminate")
    todo = sum(1 for i in issues if i["fields"]["status"]["statusCategory"]["key"]=="new")
    total = len(issues)
    sw_counts[sw] = (total, done, prog, todo, 0)   # late se rellena después

# 3. Tardías (due < hoy, no done)
late_issues = jira_search(
    f"project in ({ALL_SW}) AND due < '{TODAY}' AND statusCategory != Done ORDER BY project ASC, due ASC",
    ["summary","status","duedate","assignee","project"], max_results=200
)
late_by_sw = defaultdict(list)
for i in late_issues:
    proj = i["fields"]["project"]["key"]
    late_by_sw[proj].append({
        "key":i["key"], "summary":clean(i["fields"]["summary"]),
        "due":i["fields"].get("duedate",""),
        "assignee":clean((i["fields"].get("assignee") or {}).get("displayName","Sin asignar"))
    })

# Actualizar late count en sw_counts
for sw in SW_TO_MO:
    t,d,p,td,_ = sw_counts[sw]
    l = len(late_by_sw.get(sw,[]))
    sw_counts[sw] = (t,d,p,td,l)
print(f"  ✅ Tardías: {sum(len(v) for v in late_by_sw.values())}")

# 4. Semana (hoy ≤ due ≤ hoy+7, no done)
week_issues = jira_search(
    f"project in ({ALL_SW}) AND due >= '{TODAY}' AND due <= '{WEEK_END}' AND statusCategory != Done ORDER BY project ASC, due ASC",
    ["summary","status","duedate","assignee","project"], max_results=200
)
week_by_mo = defaultdict(list)
for i in week_issues:
    proj = i["fields"]["project"]["key"]
    mo   = SW_TO_MO.get(proj)
    if mo:
        week_by_mo[mo].append({
            "key":i["key"], "summary":clean(i["fields"]["summary"]),
            "due":i["fields"].get("duedate",""),
            "assignee":clean((i["fields"].get("assignee") or {}).get("displayName","Sin asignar")),
            "status":i["fields"]["status"]["name"]
        })
print(f"  ✅ Esta semana: {sum(len(v) for v in week_by_mo.values())}")

# 5. Sin fecha (due is EMPTY, no done)
nodt_issues = jira_search(
    f"project in ({ALL_SW}) AND due is EMPTY AND statusCategory != Done ORDER BY project ASC",
    ["summary","status","assignee","project"], max_results=200
)
nodt_by_mo = defaultdict(list)
for i in nodt_issues:
    proj = i["fields"]["project"]["key"]
    mo   = SW_TO_MO.get(proj)
    if mo:
        nodt_by_mo[mo].append({
            "key":i["key"], "summary":clean(i["fields"]["summary"]),
            "due":"", "assignee":clean((i["fields"].get("assignee") or {}).get("displayName","Sin asignar")),
            "status":i["fields"]["status"]["name"]
        })
print(f"  ✅ Sin fecha: {sum(len(v) for v in nodt_by_mo.values())}")

# ── Construir variables JS ────────────────────────────────────────────────────
def fmt_task(t):
    return (f"    {{key:'{t['key']}',summary:\"{t['summary']}\","
            f"duedate:'{t.get('due','')}',assignee:\"{t['assignee']}\","
            f"status:\"{t.get('status','')}\" }}")

def build_tasks_var(name, by_mo, comment):
    lines = [f"var {name} = {{", f"  // {TODAY} — {comment}"]
    for mo in sorted(by_mo, key=lambda x: int(x.split("-")[1])):
        tasks = by_mo[mo]
        if not tasks: continue
        lines.append(f"  '{mo}': [")
        lines += [fmt_task(t)+("," if j<len(tasks)-1 else "") for j,t in enumerate(tasks)]
        lines.append("  ],")
    lines.append("};")
    return "\n".join(lines)

def build_late_var(by_sw):
    total = sum(len(v) for v in by_sw.values())
    lines = [f"var LATE_TASKS = {{",
             f"  // {TODAY} — {total} tardías en {len(by_sw)} iniciativas"]
    for sw, mo in sorted(SW_TO_MO.items(), key=lambda x: int(x[1].split("-")[1])):
        tasks = by_sw.get(sw,[])
        if not tasks: continue
        lines.append(f"  '{mo}': [")
        lines += [fmt_task(t)+("," if j<len(tasks)-1 else "") for j,t in enumerate(tasks)]
        lines.append("  ],")
    lines.append("};")
    return "\n".join(lines)

LATE_JS  = build_late_var(late_by_sw)
WEEK_JS  = build_tasks_var("WEEK_TASKS", week_by_mo,
           f"{sum(len(v) for v in week_by_mo.values())} esta semana")
NODT_JS  = build_tasks_var("NO_DATE_TASKS", nodt_by_mo,
           f"{sum(len(v) for v in nodt_by_mo.values())} sin fecha")

# ── Descargar HTML actual ─────────────────────────────────────────────────────
print("📥 Descargando index.html...")
html, sha = gh_get_file("index.html")

# ── Aplicar actualizaciones al HTML ──────────────────────────────────────────
def replace_block(html, start_marker, end_marker, new_block):
    s = html.find(start_marker)
    e = html.find(end_marker, s)
    if s == -1 or e == -1:
        return html
    return html[:s] + new_block + "\n\n" + html[e + len(end_marker):]

# Status + owner + pais en DATA array
for mo_key, vals in MO_STATUS.items():
    ns = vals["status"]; no = vals["owner"].replace("'","\\'"); np = vals["pais"]
    html = re.sub(
        r"(key:'" + re.escape(mo_key) + r"'[^,\n]*?,frente:[^,\n]*?,subfrente:[^,\n]*?,summary:[^,\n]*?,status:')[^']+'",
        r"\g<1>" + ns + "'", html, count=1)
    html = re.sub(
        r"(key:'" + re.escape(mo_key) + r"'[^}]*?owner:')[^']+'",
        r"\g<1>" + no + "'", html, count=1)
    if np:
        html = re.sub(
            r"(key:'" + re.escape(mo_key) + r"'[^}]*?pais:')[^']+'",
            r"\g<1>" + np + "'", html, count=1)

# Tasks en SW_PROJECTS y DATA
sw_pat = re.compile(r"'(?P<sw>[A-Z]+)'\s*:\s*\{[^}]*?tasks\s*:\s*(?:\{[^}]+\}|null)")
def sw_repl(m):
    sw = m.group("sw")
    if sw not in sw_counts: return m.group(0)
    t,d,p,td,l = sw_counts[sw]
    return re.sub(r"tasks\s*:\s*(?:\{[^}]+\}|null)",
                  f"tasks:{{total:{t},done:{d},prog:{p},todo:{td},late:{l}}}", m.group(0))
html = sw_pat.sub(sw_repl, html)

for mo, sw in MO_TO_SW.items():
    if sw not in sw_counts: continue
    t,d,p,td,l = sw_counts[sw]
    html = re.sub(r"(key:'" + re.escape(mo) + r"'[^}]*?,tasks:)(\{[^}]+\}|null)",
                  r"\g<1>" + f"{{total:{t},done:{d},prog:{p},todo:{td},late:{l}}}", html, count=1)

# LATE_TASKS
s = html.find("var LATE_TASKS = {")
e = html.find("};\n\nfunction renderPlanCards")
if s != -1 and e != -1:
    html = html[:s] + LATE_JS + "\n\nfunction renderPlanCards" + html[e+len("};\n\nfunction renderPlanCards"):]

# WEEK_TASKS
s = html.find("var WEEK_TASKS = {")
wt_end = re.search(r"\};\s*\n(?=\n|var NO_DATE)", html[s:]) if s != -1 else None
if s != -1 and wt_end:
    html = html[:s] + WEEK_JS + "\n\n" + html[s + wt_end.end():]

# NO_DATE_TASKS
s = html.find("var NO_DATE_TASKS = {")
nd_end = re.search(r"\};\s*\n", html[s:]) if s != -1 else None
if s != -1 and nd_end:
    html = html[:s] + NODT_JS + "\n" + html[s + nd_end.end():]

# ── Commit al repositorio ─────────────────────────────────────────────────────
print("📤 Subiendo index.html actualizado...")
result = gh_put_file(
    "index.html", html, sha,
    f"auto: actualización Jira — {TODAY} {datetime.now().strftime('%H:%M')} COT"
)
print(f"✅ Commit: {result['commit']['sha'][:7]}")
print(f"🌐 Dashboard: https://joha-22.github.io/dashboard-mckinsey-jamar/")
