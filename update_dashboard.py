#!/usr/bin/env python3
"""
update_dashboard.py — Dashboard McKinsey-Jamar (Opcion B completa)
Actualiza: MO statuses, LATE_TASKS, WEEK_TASKS, NO_DATE_TASKS, conteos done/prog/todo
"""
import os, re, json, base64, urllib.request, urllib.error
from collections import defaultdict
from datetime import date, datetime, timedelta

JIRA_BASE  = "https://hubdigitaljamar.atlassian.net"
JIRA_EMAIL = os.environ["JIRA_EMAIL"]
JIRA_TOKEN = os.environ["JIRA_TOKEN"]
GH_PAT     = os.environ["GH_PAT"]
GH_REPO    = "mckinsey-jamar/dashboard-mckinsey-jamar"
TODAY      = date.today().isoformat()
WEEK_END   = (date.today() + timedelta(days=7)).isoformat()

KNOWN_TOTALS = {
    "SOE":99,"DEP":139,"MSOP":29,"MEJ":37,"PROVED":37,"ECI":28,"DEIT":111,
    "SLOBM":14,"JCTR":58,"SLOBDECO":55,"SLOBDECPA":22,"RCD3":43,"IMPCSE":24,
    "MIOT":1,"OPR":20,"ZT5F":13,"FCCDA":38,"WF5":28,"SDR":29,"PDSP":96,"LEANW":14,
    "IM":28,"OP":48,"TLGDL":12,"EEMOC":10,"SF":47,"EODV":42,"ISMC":3,
    "IEPRFEEFDC":70,"ICD":34,"CODCEYBM":9,"IPDPCDAR":50,"IPDPCDBR":54,
    "PTMZR":12,"RF1D":9,"PROP":22,"MDCB":11,
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
    "RF1D":"MO-89","PROP":"MO-88","MDCB":"MO-52","LEANW":"MO-30",
}
MO_TO_SW = {v:k for k,v in SW_TO_MO.items()}
ALL_SW   = ",".join(SW_TO_MO.keys())

def jira_auth():
    cred = base64.b64encode((JIRA_EMAIL+":"+JIRA_TOKEN).encode()).decode()
    return {"Authorization":"Basic "+cred,
            "Accept":"application/json","Content-Type":"application/json"}

def jira_post(jql, fields=None, max_results=100):
    body = {"jql":jql,"maxResults":max_results}
    if fields:
        body["fields"] = fields if isinstance(fields,list) else [fields]
    req = urllib.request.Request(
        JIRA_BASE+"/rest/api/3/search/jql",
        data=json.dumps(body).encode(), headers=jira_auth(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read()).get("issues",[])
    except Exception as ex:
        print("  JIRA ERR: "+str(ex)[:80]+" | "+jql[:50])
        return []

def jira_all(jql, fields=None, per_page=100, max_pages=8):
    all_issues = []; next_token = None
    for page in range(max_pages):
        body = {"jql":jql,"maxResults":per_page}
        if fields: body["fields"] = fields if isinstance(fields,list) else [fields]
        if next_token: body["nextPageToken"] = next_token
        req = urllib.request.Request(
            JIRA_BASE+"/rest/api/3/search/jql",
            data=json.dumps(body).encode(), headers=jira_auth(), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                data = json.loads(r.read())
        except Exception as ex:
            print("  PAGE ERR p"+str(page)+": "+str(ex)[:60]); break
        issues = data.get("issues",[])
        all_issues.extend(issues)
        if data.get("isLast",True) or not issues: break
        next_token = data.get("nextPageToken")
        if not next_token: break
    return all_issues

def gh_headers():
    return {"Authorization":"token "+GH_PAT,
            "Accept":"application/vnd.github.v3+json","Content-Type":"application/json"}

def gh_get(path):
    req = urllib.request.Request(
        "https://api.github.com/repos/"+GH_REPO+"/contents/"+path,
        headers=gh_headers())
    with urllib.request.urlopen(req) as r:
        d=json.loads(r.read())
    return base64.b64decode(d["content"]).decode("utf-8"),d["sha"]

def gh_put(path,content,sha,message):
    data=json.dumps({"message":message,
        "content":base64.b64encode(content.encode()).decode(),
        "sha":sha,"branch":"main"}).encode()
    req=urllib.request.Request(
        "https://api.github.com/repos/"+GH_REPO+"/contents/"+path,
        data=data,headers=gh_headers(),method="PUT")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())

def clean(s):
    return (s or "").strip().replace('"','\\"').replace("'","\\'")

def get_pais(fv):
    if not fv: return ""
    vals=fv if isinstance(fv,list) else [fv]
    vals=[v.get("value","") if isinstance(v,dict) else str(v) for v in vals]
    col=any("olombia" in v or v=="COL" for v in vals)
    pan=any("anam" in v or v=="PAN" for v in vals)
    return "Global" if col and pan else "PAN" if pan else "COL" if col else ""

def replace_var(html,name,new_content):
    s=html.find("var "+name+" = {")
    if s==-1: return html
    depth=0; pos=s+len("var "+name+" = ")
    while pos<len(html):
        if html[pos]=="{": depth+=1
        elif html[pos]=="}":
            depth-=1
            if depth==0: return html[:s]+new_content+html[pos+1:]
        pos+=1
    return html

def count_by_proj(issues):
    total=defaultdict(int); prog=defaultdict(int)
    for iss in issues:
        if "fields" not in iss: continue
        proj=iss["fields"].get("project",{}).get("key","")
        if not proj: continue
        cat=iss["fields"].get("status",{}).get("statusCategory",{}).get("key","")
        total[proj]+=1
        if cat=="indeterminate": prog[proj]+=1
    return total,prog

def fmt_task(key,summary,due,assignee,status=""):
    return ("    {key:'"+key+"',summary:\""+summary+"\","
            "duedate:'"+due+"',assignee:\""+assignee+"\","
            "status:\""+status+"\" }")

def build_var(name,by_mo,comment):
    lines=["var "+name+" = {","  // "+TODAY+" \xe2\x80\x94 "+comment]
    for sw,mo in sorted(SW_TO_MO.items(),key=lambda x:int(x[1].split("-")[1])):
        tasks=by_mo.get(mo,[])
        if not tasks: continue
        lines.append("  '"+mo+"': [")
        for j,t in enumerate(tasks):
            lines.append(fmt_task(t["key"],t["summary"],t.get("due",""),
                                  t["assignee"],t.get("status",""))+
                         ("," if j<len(tasks)-1 else ""))
        lines.append("  ],")
    lines.append("};")
    return "\n".join(lines)

print("Actualizacion Opcion B — "+TODAY)

# 1. MO statuses
print("MO statuses...")
mo_issues=jira_post("project = MO ORDER BY key ASC",
    ["summary","status","customfield_11057","customfield_11197","issuelinks"])
MO_STATUS={}
for i in mo_issues:
    f=i["fields"]; st=f["status"]["name"].split(":")[0].strip()
    ow_list=f.get("customfield_11057") or []
    ow=ow_list[0].get("value","Sin asignar") if ow_list else "Sin asignar"
    # Extraer proyecto SW de issuelinks (Polaris work item link)
    sw_from_link = ""
    for lnk in f.get("issuelinks",[]):
        linked = lnk.get("inwardIssue") or lnk.get("outwardIssue") or {}
        lkey = linked.get("key","")
        if lkey and lnk.get("type",{}).get("name","") == "Polaris work item link":
            sw_from_link = lkey.split("-")[0]
            break
    MO_STATUS[i["key"]]={"status":st,"owner":ow,
        "pais":get_pais(f.get("customfield_11197")),"sw":sw_from_link}
print("  MO: "+str(len(MO_STATUS)))

# 2. Conteos done/prog/todo (Opcion B)
print("Conteos...")
nd_fecha=jira_all(
    "project in ("+ALL_SW+") AND due >= '2020-01-01' AND statusCategory != Done "
    "ORDER BY project ASC",["status","project"],100,8)
nd_nodate=jira_all(
    "project in ("+ALL_SW+") AND due is EMPTY AND statusCategory != Done "
    "ORDER BY project ASC",["status","project"],100,5)
tot_A,prog_A=count_by_proj(nd_fecha)
tot_B,prog_B=count_by_proj(nd_nodate)
print("  no-done-con-fecha: "+str(len(nd_fecha))+" | no-done-sin-fecha: "+str(len(nd_nodate)))

sw_counts={}
for sw,total in KNOWN_TOTALS.items():
    nd_f=tot_A.get(sw,0); nd_n=tot_B.get(sw,0)
    not_done=nd_f+nd_n
    if not_done>total: total=not_done
    done=max(0,total-not_done)
    prog=prog_A.get(sw,0)+prog_B.get(sw,0)
    todo=max(0,not_done-prog)
    sw_counts[sw]=(total,done,prog,todo,0)

# 3. LATE_TASKS
print("Tardias...")
late_issues=jira_post(
    "project in ("+ALL_SW+") AND due < '"+TODAY+"' AND statusCategory != Done "
    "ORDER BY project ASC, due ASC",
    ["summary","status","duedate","assignee","project"],100)
late_by_mo=defaultdict(list)
for i in late_issues:
    if "fields" not in i: continue
    if i["fields"]["status"].get("statusCategory",{}).get("key","")=="done": continue
    mo=SW_TO_MO.get(i["fields"]["project"]["key"]); f=i["fields"]
    if mo:
        late_by_mo[mo].append({"key":i["key"],"summary":clean(f["summary"]),
            "due":f.get("duedate",""),
            "assignee":clean((f.get("assignee") or {}).get("displayName","Sin asignar"))})
for sw in KNOWN_TOTALS:
    mo=SW_TO_MO.get(sw,"")
    t,d,p,td,_=sw_counts[sw]
    sw_counts[sw]=(t,d,p,td,len(late_by_mo.get(mo,[])))
total_late=sum(len(v) for v in late_by_mo.values())
print("  Tardias: "+str(total_late))

# 4. WEEK_TASKS
print("Esta semana...")
week_issues=jira_all(
    "project in ("+ALL_SW+") AND due >= '"+TODAY+"' AND due <= '"+WEEK_END+"' "
    "AND statusCategory != Done ORDER BY project ASC, due ASC",
    ["summary","status","duedate","assignee","project"],100,3)
week_by_mo=defaultdict(list)
for i in week_issues:
    if "fields" not in i: continue
    if i["fields"]["status"].get("statusCategory",{}).get("key","")=="done": continue
    mo=SW_TO_MO.get(i["fields"]["project"]["key"]); f=i["fields"]
    if mo:
        week_by_mo[mo].append({"key":i["key"],"summary":clean(f["summary"]),
            "due":f.get("duedate",""),
            "assignee":clean((f.get("assignee") or {}).get("displayName","Sin asignar")),
            "status":f["status"]["name"]})
total_week=sum(len(v) for v in week_by_mo.values())
print("  Esta semana: "+str(total_week))

# 5. NO_DATE_TASKS
print("Sin fecha...")
nodt_issues=jira_all(
    "project in ("+ALL_SW+") AND due is EMPTY AND statusCategory != Done "
    "ORDER BY project ASC",
    ["summary","status","assignee","project"],100,5)
nodt_by_mo=defaultdict(list)
for i in nodt_issues:
    if "fields" not in i: continue
    if i["fields"]["status"].get("statusCategory",{}).get("key","")=="done": continue
    mo=SW_TO_MO.get(i["fields"]["project"]["key"]); f=i["fields"]
    if mo:
        nodt_by_mo[mo].append({"key":i["key"],"summary":clean(f["summary"]),
            "due":"",
            "assignee":clean((f.get("assignee") or {}).get("displayName","Sin asignar")),
            "status":f["status"]["name"]})
total_nodt=sum(len(v) for v in nodt_by_mo.values())
print("  Sin fecha: "+str(total_nodt))

# 6. Descargar y actualizar HTML
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
    # Actualizar campo sw si se detectó desde issuelinks y está vacío
    sw_detected = vals.get("sw","")
    if sw_detected:
        html=re.sub(r"(key:'"+re.escape(mk)+r"'[^}]*?sw:')[^']*'",
                    r"\g<1>"+sw_detected+"'",html,count=1)

# SW_PROJECTS y DATA tasks
for sw,(t,d,p,td,l) in sw_counts.items():
    nt="tasks:{total:"+str(t)+",done:"+str(d)+",prog:"+str(p)+",todo:"+str(td)+",late:"+str(l)+"}"
    html=re.sub(r"('"+re.escape(sw)+r"'\s*:\s*\{[^}]*?)tasks\s*:\s*(?:\{[^}]+\}|null)",
                r"\g<1>"+nt,html,count=1)
    mo=SW_TO_MO.get(sw)
    if mo:
        nt2="{total:"+str(t)+",done:"+str(d)+",prog:"+str(p)+",todo:"+str(td)+",late:"+str(l)+"}"
        html=re.sub(r"(key:'"+re.escape(mo)+r"'[^}]*?,tasks:)(\{[^}]+\}|null)",
                    r"\g<1>"+nt2,html,count=1)

# LATE_TASKS, WEEK_TASKS, NO_DATE_TASKS
late_js=build_var("LATE_TASKS",late_by_mo,str(total_late)+" tardias (no-Done)")
week_js=build_var("WEEK_TASKS",week_by_mo,str(total_week)+" esta semana")
nodt_js=build_var("NO_DATE_TASKS",nodt_by_mo,str(total_nodt)+" sin fecha")

html=replace_var(html,"LATE_TASKS",late_js)
html=replace_var(html,"WEEK_TASKS",week_js)
html=replace_var(html,"NO_DATE_TASKS",nodt_js)

now_str=datetime.now().strftime("%H:%M")
result=gh_put("index.html",html,sha,
    "auto: actualizacion completa — "+TODAY+" "+now_str+" COT")
print("Commit: "+result["commit"]["sha"][:7])
print("Late="+str(total_late)+" Semana="+str(total_week)+" SinFecha="+str(total_nodt))
