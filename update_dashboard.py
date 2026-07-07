#!/usr/bin/env python3
import sys, traceback, os

# Ejecutar el script con manejo de errores explícito
def main():
    """
    update_dashboard.py — Dashboard McKinsey-Jamar v2.0
    Flujo completo en cada ejecución (cada 10 min):
      1. SYNC ESTRUCTURAL: sincroniza summary, frente, subfrente, status, owner, pais, sw
      2. ESTADOS: recalcula done/prog/todo (Opcion B)
      3. LISTAS: reconstruye LATE, WEEK, PROXIMAS+SINFECHA
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
    
    # SW_TO_MO: mapeo definitivo + detectado dinámicamente
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
        "LEANW":"MO-30","LEANWPA":"MO-69","MXAT":"MO-77",
        "MDOOMC":"MO-49","EDP":"MO-53",
    }
    MO_TO_SW = {v:k for k,v in SW_TO_MO.items()}
    
    # KNOWN_TOTALS: se actualiza dinámicamente si not_done > total
    KNOWN_TOTALS = {
        "SOE":99,"DEP":139,"MSOP":29,"MEJ":37,"PROVED":37,"ECI":28,"DEIT":111,
        "SLOBM":14,"JCTR":58,"SLOBDECO":55,"SLOBDECPA":22,"RCD3":43,"IMPCSE":24,
        "MIOT":1,"OPR":20,"ZT5F":13,"FCCDA":38,"WF5":28,"SDR":29,"PDSP":96,
        "IM":28,"OP":48,"TLGDL":12,"EEMOC":10,"SF":47,"EODV":42,"ISMC":3,
        "IEPRFEEFDC":70,"ICD":34,"CODCEYBM":9,"IPDPCDAR":50,"IPDPCDBR":54,
        "PTMZR":12,"RF1D":9,"PROP":22,"MDCB":9,
        "LEANW":0,"LEANWPA":0,"MXAT":0,"MDOOMC":0,"EDP":0,
    }
    # Normalizar nombres de frente/subfrente que llegan de Jira
    # para que coincidan con los nombres en SF_ORDER del dashboard
    SF_NORMALIZE = {
        # Subfrentes sin tilde → con tilde (nombre canónico)
        'Curva de Valor de Credito': 'Curva de Valor Crédito',
        'Curva de Valor Credito':    'Curva de Valor Crédito',
        'E2E proceso de Credito':    'E2E proceso de Crédito',
        'E2E Proceso de Credito':    'E2E proceso de Crédito',
        'Post-Venta':                'Post-venta',
        'Almacen':                   'Almacenamiento',
    }
    def norm_sf(v): return SF_NORMALIZE.get(v, v)
    def norm_fr(v): return v  # frente generalmente viene bien

    
    # ── Helpers Jira ───────────────────────────────────────────────────────────────
    def jira_auth():
        cred = base64.b64encode((JIRA_EMAIL+":"+JIRA_TOKEN).encode()).decode()
        return {"Authorization":"Basic "+cred,
                "Accept":"application/json","Content-Type":"application/json"}
    
    def jira_post(jql, fields=None, max_results=100):
        body = {"jql":jql,"maxResults":max_results}
        if fields: body["fields"] = fields if isinstance(fields,list) else [fields]
        req = urllib.request.Request(
            JIRA_BASE+"/rest/api/3/search/jql",
            data=json.dumps(body).encode(), headers=jira_auth(), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read()).get("issues",[])
        except Exception as ex:
            print("  JIRA ERR: "+str(ex)[:80]+" | "+jql[:60]); return []
    
    def jira_all(jql, fields=None, per_page=100, max_pages=8):
        all_issues=[]; next_token=None
        for page in range(max_pages):
            body={"jql":jql,"maxResults":per_page}
            if fields: body["fields"]=fields if isinstance(fields,list) else [fields]
            if next_token: body["nextPageToken"]=next_token
            req=urllib.request.Request(
                JIRA_BASE+"/rest/api/3/search/jql",
                data=json.dumps(body).encode(),headers=jira_auth(),method="POST")
            try:
                with urllib.request.urlopen(req,timeout=25) as r:
                    data=json.loads(r.read())
            except Exception as ex:
                print("  PAGE ERR p"+str(page)+": "+str(ex)[:60]); break
            issues=data.get("issues",[])
            all_issues.extend(issues)
            if data.get("isLast",True) or not issues: break
            next_token=data.get("nextPageToken")
            if not next_token: break
        return all_issues
    
    # ── Helpers GitHub ─────────────────────────────────────────────────────────────
    def gh_headers():
        return {"Authorization":"token "+GH_PAT,
                "Accept":"application/vnd.github.v3+json","Content-Type":"application/json"}
    
    def gh_get(path):
        req=urllib.request.Request(
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
    
    # ── Helpers generales ─────────────────────────────────────────────────────────
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
    
    def fmt_task(t):
        key     = str(t.get("key") or "")
        summary = str(t.get("summary") or "")
        due     = str(t.get("due") or "")
        assignee= str(t.get("assignee") or "Sin asignar")
        status  = str(t.get("status") or "")
        return ("    {key:'"+key+"',summary:\""+summary+"\","
                "duedate:'"+due+"',assignee:\""+assignee+"\","
                "status:\""+status+"\" }")

    def build_var(name,by_mo,comment):
        lines=["var "+name+" = {","  // "+TODAY+" \u2014 "+comment]
        for mo_key in sorted(MO_TO_SW.keys(),key=lambda x:int(x.split("-")[1])):
            tasks=by_mo.get(mo_key,[])
            if not tasks: continue
            lines.append("  '"+mo_key+"': [")
            for j,t in enumerate(tasks):
                lines.append(fmt_task(t)+("," if j<len(tasks)-1 else ""))
            lines.append("  ],")
        lines.append("};")
        return "\n".join(lines)
    
    # ══════════════════════════════════════════════════════════════════════════════
    print("="*60)
    print("Actualizacion v2.0 — "+TODAY)
    print("="*60)
    
    # ── PASO 1: SYNC ESTRUCTURAL ──────────────────────────────────────────────────
    print("\n[1/3] SYNC ESTRUCTURAL — MO issues con todos los campos...")
    
    mo_issues = jira_post(
        "project = MO ORDER BY key ASC",
        ["summary","status","customfield_11022","customfield_11055",
         "customfield_11057","customfield_11197","issuelinks"], 100)
    
    print("  Issues MO: "+str(len(mo_issues)))
    
    # Construir mapa de datos desde Jira
    jira_data = {}
    for i in mo_issues:
        key = i["key"]
        f   = i["fields"]
    
        # Frente (customfield_11022)
        fr_list = f.get("customfield_11022") or []
        frente  = fr_list[0].get("value","") if fr_list else ""
    
        # Subfrente (customfield_11055)
        sf_list   = f.get("customfield_11055") or []
        subfrente = norm_sf(sf_list[0].get("value","") if sf_list else "")
    
        # Status
        st = f["status"]["name"].split(":")[0].strip()
    
        # Owner (customfield_11057)
        ow_list = f.get("customfield_11057") or []
        owner   = ow_list[0].get("value","Sin asignar") if ow_list else "Sin asignar"
    
        # Pais (customfield_11197)
        pais = get_pais(f.get("customfield_11197"))
    
        # Summary
        summary = f.get("summary","")
    
        # SW desde issuelinks (Polaris work item link)
        sw = ""
        for link in f.get("issuelinks",[]):
            if link.get("type",{}).get("name") == "Polaris work item link":
                inward = link.get("inwardIssue",{})
                if inward and "key" in inward:
                    sw = inward["key"].split("-")[0]; break
    
        # Si encontró sw nuevo no mapeado, agregarlo
        if sw and sw not in SW_TO_MO:
            SW_TO_MO[sw] = key
            MO_TO_SW[key] = sw
            if sw not in KNOWN_TOTALS: KNOWN_TOTALS[sw] = 0
            print("  NUEVO SW detectado: "+sw+" → "+key)
    
        jira_data[key] = {
            "frente":frente,"subfrente":subfrente,"summary":clean(summary),
            "rec": int(f.get("customfield_11094") or 0),  # KPI impacto en USD (customfield_11094) — NO convertir a COP
            "ot":  int(f.get("customfield_11091") or 0) if frente != "Crédito" else 0,  # OT solo para Operaciones
            "status":st,"owner":clean(owner),"pais":pais,"sw":sw
        }
    
    print("  Frentes detectados: "+str(set(v["frente"] for v in jira_data.values() if v["frente"])))
    
    # ── PASO 2: ESTADOS (Opcion B) ────────────────────────────────────────────────
    print("\n[2/3] ESTADOS — Conteos done/prog/todo...")
    
    # ALL_SW_DYN incluye SW hardcoded + detectados dinámicamente
    all_sw_set = set(list(KNOWN_TOTALS.keys()) + [v["sw"] for v in jira_data.values() if v["sw"]])
    ALL_SW_DYN = ",".join(all_sw_set)
    
    # Query A: no-done CON fecha
    nd_fecha=jira_all(
        "project in ("+ALL_SW_DYN+") AND due >= '2020-01-01' AND statusCategory != Done "
        "ORDER BY project ASC",["summary","status","duedate","assignee","project"],100,8)
    
    # Query B: no-done SIN fecha
    nd_nodate=jira_all(
        "project in ("+ALL_SW_DYN+") AND due is EMPTY AND statusCategory != Done "
        "ORDER BY project ASC",["status","project"],100,5)
    
    tot_A,prog_A=count_by_proj(nd_fecha)
    tot_B,prog_B=count_by_proj(nd_nodate)
    print("  no-done-con-fecha: "+str(len(nd_fecha))+" | no-done-sin-fecha: "+str(len(nd_nodate)))
    
    sw_counts={}
    for sw,total in KNOWN_TOTALS.items():
        nd_f=tot_A.get(sw,0); nd_n=tot_B.get(sw,0)
        not_done=nd_f+nd_n
        if not_done>total:
            print("  WARN: "+sw+" creció — not_done="+str(not_done)+" > known="+str(total))
            total=not_done
        done=max(0,total-not_done)
        prog=prog_A.get(sw,0)+prog_B.get(sw,0)
        todo=max(0,not_done-prog)
        sw_counts[sw]=(total,done,prog,todo,0)
    
    # ── PASO 3: LISTAS ───────────────────────────────────────────────────────────
    print("\n[3/3] LISTAS — Late, Semana, Proximas...")
    
    # Tardías
    late_issues=jira_post(
        "project in ("+ALL_SW_DYN+") AND due < '"+TODAY+"' AND statusCategory != Done AND status not in ('Bloqueada','Bloqueado','Blocked') "
        "ORDER BY project ASC, due ASC",
        ["summary","status","duedate","assignee","project"],100)
    late_by_mo=defaultdict(list)
    for i in late_issues:
        if "fields" not in i: continue
        if i["fields"]["status"].get("statusCategory",{}).get("key","")=="done": continue
        if i["fields"]["status"]["name"] in ("Bloqueada","Bloqueado","Blocked"): continue  # excluir bloqueadas de atrasadas
        sw=i["fields"]["project"]["key"]; mo=SW_TO_MO.get(sw); f=i["fields"]
        if mo:
            late_by_mo[mo].append({"key":i["key"],"summary":clean(f["summary"]),
                "due":f.get("duedate",""),
                "assignee":clean((f.get("assignee") or {}).get("displayName","Sin asignar")),
                "status":f["status"]["name"]})
    
    # Actualizar late en sw_counts
    for sw in KNOWN_TOTALS:
        mo=SW_TO_MO.get(sw,"")
        t,d,p,td,_=sw_counts[sw]
        sw_counts[sw]=(t,d,p,td,len(late_by_mo.get(mo,[])))
    total_late=sum(len(v) for v in late_by_mo.values())
    print("  Tardias: "+str(total_late))
    
    # Esta semana
    week_issues=jira_all(
        "project in ("+ALL_SW_DYN+") AND due >= '"+TODAY+"' AND due <= '"+WEEK_END+"' "
        "AND statusCategory != Done ORDER BY project ASC, due ASC",
        ["summary","status","duedate","assignee","project"],100,3)
    week_by_mo=defaultdict(list)
    for i in week_issues:
        if "fields" not in i: continue
        if i["fields"]["status"].get("statusCategory",{}).get("key","")=="done": continue
        sw=i["fields"]["project"]["key"]; mo=SW_TO_MO.get(sw); f=i["fields"]
        if mo:
            week_by_mo[mo].append({"key":i["key"],"summary":clean(f["summary"]),
                "due":f.get("duedate",""),
                "assignee":clean((f.get("assignee") or {}).get("displayName","Sin asignar")),
                "status":f["status"]["name"]})
    total_week=sum(len(v) for v in week_by_mo.values())
    print("  Semana: "+str(total_week))
    
    # Próximas + sin fecha
    nodt_issues=jira_all(
        "project in ("+ALL_SW_DYN+") AND due is EMPTY "
        "AND statusCategory != Done ORDER BY project ASC",
        ["summary","status","duedate","assignee","project"],100,15)
    # Claves con fecha real según nd_fecha (resuelve Jira JQL index lag)
    dated_task_keys = set(i2['key'] for i2 in nd_fecha)
    nodt_by_mo=defaultdict(list)
    for i in nodt_issues:
        if "fields" not in i: continue
        if i["fields"]["status"].get("statusCategory",{}).get("key","")=="done": continue
        sw=i["fields"]["project"]["key"]; mo=SW_TO_MO.get(sw); f=i["fields"]
        if i["key"] in dated_task_keys: continue  # tiene fecha real aunque JQL diga due is EMPTY
        if f.get("duedate"): continue  # excluir si tiene fecha real — no es sin fecha
        if mo:
            assignee_display = (f.get('assignee') or {}).get('displayName', '')
            nodt_by_mo[mo].append({"key":i["key"],"summary":clean(f["summary"]),
                "due":f.get("duedate",""),
                "assignee":clean((f.get("assignee") or {}).get("displayName","Sin asignar")),
                "status":f["status"]["name"]})

    total_nodt=sum(len(v) for v in nodt_by_mo.values())

    # ── NO_OWNER_TASKS: sin fecha O sin responsable ──────────────────────────────
    print('Sin responsable...')
    noown_by_mo=defaultdict(list)
    # Fuente 1: tareas CON fecha pero SIN responsable (de nd_fecha)
    for iss in nd_fecha:
        if 'fields' not in iss: continue
        if iss['fields']['status'].get('statusCategory',{}).get('key','')=='done': continue
        sw2=iss['fields'].get('project',{}).get('key',''); mo2=SW_TO_MO.get(sw2)
        f2=iss['fields']
        if mo2:
            asn2=f2.get('assignee')
            if asn2 is None or not asn2:  # Sin responsable = assignee nulo o vacío
                noown_by_mo[mo2].append({'key':iss['key'],'summary':clean(f2.get('summary') or ''),
                    'due':f2.get('duedate',''),
                    'assignee':'Sin asignar','status':f2['status']['name']})
    # Fuente 2: tareas SIN fecha Y SIN responsable (de nodt_issues)
    for iss in nodt_issues:
        if 'fields' not in iss: continue
        if iss['fields']['status'].get('statusCategory',{}).get('key','')=='done': continue
        sw2=iss['fields'].get('project',{}).get('key',''); mo2=SW_TO_MO.get(sw2)
        f2=iss['fields']
        if mo2:
            asn2=f2.get('assignee')
            if asn2 is None or not asn2:  # Sin responsable = assignee nulo o vacío
                noown_by_mo[mo2].append({'key':iss['key'],'summary':clean(f2.get('summary') or ''),
                    'due':f2.get('duedate',''),
                    'assignee':'Sin asignar','status':f2['status']['name']})
    total_noown=sum(len(v) for v in noown_by_mo.values())
    print('  Sin responsable: '+str(total_noown))
    print("  Proximas+SinFecha: "+str(total_nodt))
    
    # ── ACTUALIZAR HTML ───────────────────────────────────────────────────────────
    print("\nActualizando HTML...")
    html,sha=gh_get("index.html")
    
    # 1. SYNC ESTRUCTURAL: actualizar cada campo por MO
    changed=0
    for mk,vals in jira_data.items():
        base=r"(key:'"+re.escape(mk)+r"'"
        # summary
        old_sm=re.search(r"key:'"+re.escape(mk)+r"'[^,\n]*?,frente:'[^']*',subfrente:'[^']*',summary:'([^']*)'",html)
        # frente, subfrente, summary en secuencia
        if vals["frente"]:
            html=re.sub(r"(key:'"+re.escape(mk)+r"'[^,\n]*?,frente:')[^']*'",
                        r"\g<1>"+vals["frente"]+"'",html,count=1)
        if vals["subfrente"]:
            html=re.sub(r"(key:'"+re.escape(mk)+r"'[^,\n]*?,frente:'[^']*',subfrente:')[^']*'",
                        r"\g<1>"+vals["subfrente"]+"'",html,count=1)
        if vals["summary"]:
            html=re.sub(r"(key:'"+re.escape(mk)+r"'[^,\n]*?,frente:'[^']*',subfrente:'[^']*',summary:')[^']*'",
                        lambda m,v=vals["summary"]: m.group(1)+v+"'",html,count=1)
        # status
        html=re.sub(r"(key:'"+re.escape(mk)+r"'[^,\n]*?,frente:[^,\n]*?,subfrente:[^,\n]*?,summary:[^,\n]*?,status:')[^']+'",
                    lambda m,v=vals["status"]: m.group(1)+v+"'",html,count=1)
        # owner
        html=re.sub(r"(key:'"+re.escape(mk)+r"'[^}]*?owner:')[^']*'",
                    lambda m,v=vals["owner"]: m.group(1)+v+"'",html,count=1)
        # pais
        if vals["pais"]:
            html=re.sub(r"(key:'"+re.escape(mk)+r"'[^}]*?pais:')[^']*'",
                        lambda m,v=vals["pais"]: m.group(1)+v+"'",html,count=1)
        # rec y ot (impactos)
        # rec y ot: solo actualizar si Jira devuelve >0 (evita sobreescribir con 0)
        rec_v=str(int(vals.get('rec',0) or 0)); ot_v=str(int(vals.get('ot',0) or 0))
        if int(rec_v)>0:
            html=re.sub(r"(key:'"+re.escape(mk)+r"'[^}]*?,rec:)\d+",
                       lambda m,v=rec_v: m.group(1)+v,html,count=1)
        if int(ot_v)>0:
            html=re.sub(r"(key:'"+re.escape(mk)+r"'[^}]*?,ot:)\d+",
                       lambda m,v=ot_v: m.group(1)+v,html,count=1)
        # sw (desde SW_TO_MO reversa + issuelinks)
        sw_val = vals["sw"] or MO_TO_SW.get(mk,"")
        if sw_val:
            html=re.sub(r"(key:'"+re.escape(mk)+r"'[^}]*?sw:')[^']*'",
                        lambda m,v=sw_val: m.group(1)+v+"'",html,count=1)
        changed+=1
    
    print("  Iniciativas sincronizadas: "+str(changed))
    
    # 2. Tasks counts
    for sw,(t,d,p,td,l) in sw_counts.items():
        nt="tasks:{total:"+str(t)+",done:"+str(d)+",prog:"+str(p)+",todo:"+str(td)+",late:"+str(l)+"}"
        nt2="{total:"+str(t)+",done:"+str(d)+",prog:"+str(p)+",todo:"+str(td)+",late:"+str(l)+"}"
        html=re.sub(r"('"+re.escape(sw)+r"'\s*:\s*\{[^}]*?)tasks\s*:\s*(?:\{[^}]+\}|null)",
                    lambda m,v=nt: m.group(1)+v,html,count=1)
        mo=SW_TO_MO.get(sw)
        if mo:
            html=re.sub(r"(key:'"+re.escape(mo)+r"'[^}]*?,tasks:)(\{[^}]+\}|null)",
                        lambda m,v=nt2: m.group(1)+v,html,count=1)
    
    # 3. Listas
    html=replace_var(html,"LATE_TASKS",  build_var("LATE_TASKS", late_by_mo,  str(total_late)+" tardias"))
    html=replace_var(html,"WEEK_TASKS",  build_var("WEEK_TASKS", week_by_mo,  str(total_week)+" esta semana"))
    html=replace_var(html,"NO_DATE_TASKS",build_var("NO_DATE_TASKS",nodt_by_mo,str(total_nodt)+" sin fecha"))
    # Post-filtro: eliminar tareas que llegaron con assignee real
    for _mo in list(noown_by_mo.keys()):
        noown_by_mo[_mo]=[t for t in noown_by_mo[_mo]
            if not t.get('assignee') or t.get('assignee')=='Sin asignar']
    html=replace_var(html,"NO_OWNER_TASKS",build_var("NO_OWNER_TASKS",noown_by_mo,str(total_noown)+" sin responsable"))
    
    now_str=datetime.now().strftime("%H:%M")
    result=gh_put("index.html",html,sha,
        "auto v2.0: sync+estados+listas — "+TODAY+" "+now_str+" COT")
    print("\n✅ Commit: "+result["commit"]["sha"][:7])
    print("   Sync:"+str(changed)+" MO | Late:"+str(total_late)+" | Semana:"+str(total_week)+" | Proximas:"+str(total_nodt))

    # ── Actualizar rama gh-pages para GitHub Pages (legacy build) ─────────────
    try:
        # Obtener sha del HTML recién commiteado
        html_info_req=urllib.request.Request(
            "https://api.github.com/repos/"+GH_REPO+"/contents/index.html",
            headers=gh_headers())
        with urllib.request.urlopen(html_info_req) as ri:
            html_info=json.loads(ri.read())
        html_file_sha=html_info["sha"]

        # .nojekyll blob
        nj_req=urllib.request.Request(
            "https://api.github.com/repos/"+GH_REPO+"/git/blobs",
            data=json.dumps({"content":"","encoding":"utf-8"}).encode(),
            headers=gh_headers(),method="POST")
        with urllib.request.urlopen(nj_req) as ri:
            nj_sha=json.loads(ri.read())["sha"]

        # Tree con index.html + .nojekyll
        tree_req=urllib.request.Request(
            "https://api.github.com/repos/"+GH_REPO+"/git/trees",
            data=json.dumps({"tree":[
                {"path":"index.html","mode":"100644","type":"blob","sha":html_file_sha},
                {"path":".nojekyll","mode":"100644","type":"blob","sha":nj_sha}
            ]}).encode(),headers=gh_headers(),method="POST")
        with urllib.request.urlopen(tree_req) as ri:
            tree_sha=json.loads(ri.read())["sha"]

        # Obtener sha actual de gh-pages
        ghp_ref_req=urllib.request.Request(
            "https://api.github.com/repos/"+GH_REPO+"/git/ref/heads/gh-pages",
            headers=gh_headers())
        with urllib.request.urlopen(ghp_ref_req) as ri:
            ghp_sha=json.loads(ri.read())["object"]["sha"]

        # Commit a gh-pages
        ghp_com_req=urllib.request.Request(
            "https://api.github.com/repos/"+GH_REPO+"/git/commits",
            data=json.dumps({"message":"deploy: "+result["commit"]["sha"][:7],
                             "tree":tree_sha,"parents":[ghp_sha]}).encode(),
            headers=gh_headers(),method="POST")
        with urllib.request.urlopen(ghp_com_req) as ri:
            ghp_new_sha=json.loads(ri.read())["sha"]

        # Update ref
        urllib.request.urlopen(urllib.request.Request(
            "https://api.github.com/repos/"+GH_REPO+"/git/refs/heads/gh-pages",
            data=json.dumps({"sha":ghp_new_sha,"force":True}).encode(),
            headers=gh_headers(),method="PATCH"))
        print("✅ gh-pages actualizado — "+ghp_new_sha[:7])
    except Exception as e_ghp:
        print("  WARN gh-pages: "+str(e_ghp)[:80])

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("="*60, file=sys.stderr)
        print("ERROR FATAL:", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        print("="*60, file=sys.stderr)
        sys.exit(1)