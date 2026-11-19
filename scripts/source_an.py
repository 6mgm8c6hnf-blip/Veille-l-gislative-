"""Collecte Assemblée nationale (data.assemblee-nationale.fr, 17e législature)."""
import json
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict

from commun import UA, fichiers_zip, ligne_amendement, premier, telecharger, texte_brut

LEG = "17"
BASE = f"https://data.assemblee-nationale.fr/static/openData/repository/{LEG}"
URL_AGENDA = f"{BASE}/vp/reunions/Agenda.json.zip"
URL_ORGANES = f"{BASE}/amo/deputes_actifs_mandats_actifs_organes/AMO10_deputes_actifs_mandats_actifs_organes.json.zip"
URL_DOSSIERS = f"{BASE}/loi/dossiers_legislatifs/Dossiers_Legislatifs.json.zip"
URL_AMENDEMENTS = f"{BASE}/loi/amendements_div_legis/Amendements.json.zip"
URL_QUESTIONS = {
    "QG": f"{BASE}/questions/questions_gouvernement/Questions_gouvernement.json.zip",
    "QE": f"{BASE}/questions/questions_ecrites/Questions_ecrites.json.zip",
    "QOSD": f"{BASE}/questions/questions_orales_sans_debat/Questions_orales_sans_debat.json.zip",
}
SITE = "https://www.assemblee-nationale.fr"
NOM_QUESTION = {"QG": "Question au Gouvernement", "QE": "Question écrite", "QOSD": "Question orale sans débat"}


def _val(x):
    """texte d'un champ qui peut être une chaîne, une liste ou {'item': ...}"""
    if x is None:
        return ""
    if isinstance(x, str):
        return x
    if isinstance(x, list):
        return " · ".join(_val(i) for i in x)
    if isinstance(x, dict):
        if x.get("@xsi:nil") == "true":
            return ""
        return " · ".join(_val(v) for k, v in x.items() if not k.startswith("@"))
    return str(x)


def _date(ts):
    """'2026-12-02T14:45:00.000+01:00' -> '2026-12-02T14:45' ; '2026-11-18+01:00' -> '2026-11-18'"""
    if not ts:
        return None
    m = re.match(r"(\d{4}-\d{2}-\d{2})(?:T(\d{2}:\d{2}))?", ts)
    if not m:
        return None
    return m.group(1) + ("T" + m.group(2) if m.group(2) and m.group(2) != "00:00" else "")


MAX_LIENS = 60  # amendements listés (avec lien) par événement


class AssembleeNationale:
    def __init__(self, ctx):
        self.ctx = ctx  # contexte partagé (détecteur, fenêtre de dates, commissions…)
        self.organes = {}
        self.dossiers = {}  # uid -> infos
        self.docs = {}  # uid document -> dossier uid

    # ---------- référentiels ----------
    def charger_organes(self):
        for _, b in fichiers_zip(telecharger(URL_ORGANES)):
            if b'"organe"' not in b[:200]:
                continue
            o = json.loads(b)["organe"]
            self.organes[o["uid"]] = o.get("libelle") or o["uid"]
        return len(self.organes)

    def nom_organe(self, ref):
        return self.organes.get(ref, "")

    def charger_dossiers(self):
        det = self.ctx.detecteur
        forces = self.ctx.textes_forces
        for nom, b in fichiers_zip(telecharger(URL_DOSSIERS)):
            d = json.loads(b)
            if "document" in d:
                doc = d["document"]
                self.docs[doc["uid"]] = {"dossier": doc.get("dossierRef"), "depot": doc.get("depotAmendements"),
                                          "titre": (doc.get("titres") or {}).get("titrePrincipalCourt")}
                continue
            dp = d.get("dossierParlementaire")
            if not dp:
                continue
            titre = (dp.get("titreDossier") or {}).get("titre") or ""
            chemin = (dp.get("titreDossier") or {}).get("titreChemin")
            senat = (dp.get("titreDossier") or {}).get("senatChemin")
            proc = (dp.get("procedureParlementaire") or {}).get("libelle") or ""
            legislatif = bool(re.search(r"loi|ratification|traité|convention", proc, re.I))
            themes = det.themes(titre)
            force = any(f in self.ctx.norm(titre) or f == dp["uid"] or (chemin and f == self.ctx.norm(chemin)) for f in forces)
            self.dossiers[dp["uid"]] = {
                "uid": dp["uid"], "titre": titre, "proc": proc, "themes": themes,
                "suivi": legislatif and (bool(themes) or force), "force": force,
                "url": f"{SITE}/dyn/{LEG}/dossiers/{chemin}" if chemin else None,
                "url_senat": senat.replace("http://", "https://") if senat else None, "actes": dp.get("actesLegislatifs"),
            }
        return sum(1 for x in self.dossiers.values() if x["suivi"])

    # ---------- contenu des textes (pas seulement le titre) ----------
    def contenu_textes(self):
        """Lit le texte déposé des projets/propositions de loi actifs dont le titre n'a pas de mot-clé.
        S'il contient assez de mots-clés, le texte devient suivi (et seuls ses amendements
        contenant un mot-clé seront affichés)."""
        ctx, det = self.ctx, self.ctx.detecteur
        initial = {}
        for uid, doc in self.docs.items():
            if doc.get("dossier") and re.match(r"(PRJL|PION)", uid):
                initial.setdefault(doc["dossier"], []).append(uid)
        candidats = []
        for d in self.dossiers.values():
            if d["suivi"] or not re.search(r"loi", d["proc"], re.I) or d["uid"] not in initial:
                continue
            dates = [(_date(a.get("dateActe")) or "") for a in self._feuilles(d["actes"])]
            if max(dates, default="") >= ctx.debut:  # texte qui a bougé récemment
                candidats.append((max(dates), d, sorted(initial[d["uid"]])[0]))
        candidats.sort(key=lambda x: x[0], reverse=True)
        limite = time.time() + 360
        print(f"   AN : {len(candidats)} textes récents à lire", flush=True)

        def lire(c):
            _, d, doc_uid = c
            if time.time() > limite:
                return None
            req = urllib.request.Request(f"{SITE}/dyn/opendata/{doc_uid}.html", headers={"User-Agent": UA})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    return d, texte_brut(r.read().decode("utf-8", "replace"))
            except Exception:
                return d, None

        lus = trouves = 0
        with ThreadPoolExecutor(max_workers=6) as pool:
            for res in pool.map(lire, candidats):
                if not res or not res[1]:
                    continue
                lus += 1
                d, corps = res
                themes = det.themes_corps(corps)
                if themes:
                    d.update(suivi=True, themes=themes, corps=True)
                    trouves += 1
        print(f"   AN : {lus} textes lus, {trouves} retenus grâce à leur contenu", flush=True)
        return trouves

    def dossier(self, uid):
        return self.dossiers.get(uid) or {}

    # ---------- agenda : réunions de commission, auditions, séances ----------
    def agenda(self):
        ctx, det = self.ctx, self.ctx.detecteur
        suivies = {c["code"]: c for c in ctx.commissions if c["chambre"] == "AN"}
        out = []
        for _, b in fichiers_zip(telecharger(URL_AGENDA)):
            r = json.loads(b).get("reunion")
            if not r:
                continue
            debut = _date(r.get("timeStampDebut"))
            if not debut or not ctx.dans_fenetre(debut):
                continue
            etat = (r.get("cycleDeVie") or {}).get("etat") or ""
            if etat.lower().startswith("supprim"):
                continue
            uid = r["uid"]
            chambre = "Sénat" if uid.startswith("RUSN") else "AN"
            organe = r.get("organeReuniRef")
            xtype = r.get("@xsi:type", "")
            odj = r.get("ODJ") or {}
            points = premier((odj.get("pointsODJ") or {}).get("pointODJ"))
            objets = [p.get("objet") or "" for p in points]
            types_pt = [p.get("typePointODJ") or "" for p in points]
            dossiers = []
            for p in points:
                for ref in premier((p.get("dossiersLegislatifsRefs") or {}).get("dossierRef")):
                    if ref and ref not in dossiers:
                        dossiers.append(ref)
            convoc = texte_brut(_val(odj.get("convocationODJ")))
            resume = texte_brut(_val(odj.get("resumeODJ")))
            auditionnes = self._auditionnes((r.get("participants") or {}).get("personnesAuditionnees"))
            titres_dossiers = [self.dossier(x).get("titre", "") for x in dossiers]
            texte = " ".join([convoc, resume, " ".join(objets), auditionnes] + titres_dossiers)
            themes = det.themes(texte)
            dos_suivis = [x for x in dossiers if self.dossier(x).get("suivi")]
            commission = suivies.get(organe)
            garder = bool(themes) or bool(dos_suivis) or (commission and commission["tout"])
            if not garder:
                continue
            seance = "seance" in xtype.lower()
            if seance:
                typ = "Séance publique"
            elif any("audition" in t.lower() for t in types_pt) or re.match(r"\s*audition", resume or convoc, re.I):
                typ = "Audition"
            else:
                typ = "Réunion de commission"
            # titre lisible
            if seance:
                morceaux = []
                for p in points:
                    refs = premier((p.get("dossiersLegislatifsRefs") or {}).get("dossierRef"))
                    t = self.dossier(refs[0]).get("titre") if refs else ""
                    morceaux.append(((p.get("typePointODJ") or p.get("objet") or "") + (" : " + t if t else "")).strip(" :"))
                titre = "Séance publique" + (" — " + " ; ".join(dict.fromkeys(m for m in morceaux if m)) if morceaux else "")
            else:
                titre = (convoc if convoc and len(convoc) < 300 else "") or resume or convoc or " ; ".join(o for o in objets if o) or "Réunion"
            titre = re.sub(r"\s+", " ", titre).strip()
            if len(titre) > 220:
                titre = titre[:217] + "…"
            if etat.lower().startswith("annul"):
                titre = "[ANNULÉE] " + titre
            dos_principal = (dos_suivis or dossiers or [None])[0]
            d = self.dossier(dos_principal) if dos_principal else {}
            huis = None
            if r.get("ouverturePresse") in ("true", "false"):
                huis = r.get("ouverturePresse") == "false"
            if any(p.get("comiteSecret") == "true" for p in points):
                huis = True
            nt = ctx.norm(texte)
            if "huis clos" in nt:
                huis = True
            elif re.search(r"ouverte? a la presse", nt):
                huis = False
            out.append(ctx.evenement(
                id=uid, debut=debut, fin=_date(r.get("timeStampFin")), chambre=chambre, type=typ, titre=titre,
                organe=self.nom_organe(organe) or ("Séance publique" if seance else organe),
                lieu=(r.get("lieu") or {}).get("libelleLong"), huis_clos=huis,
                texte_id=dos_principal if d else None, texte_titre=d.get("titre"), texte_url=d.get("url"),
                url=self.url_reunion(uid, organe, debut, seance, chambre, d),
                description="\n".join(x for x in [convoc if convoc != titre else "", ("Personnes auditionnées : " + auditionnes) if auditionnes else ""] + [o for o in objets if o and o not in titre] if x),
                themes=list(dict.fromkeys(themes + (d.get("themes") or []))),
                etat=etat,
            ))
        return out

    @staticmethod
    def _auditionnes(bloc):
        noms = []
        for p in premier((bloc or {}).get("personneAuditionnee") if isinstance(bloc, dict) else bloc):
            if not isinstance(p, dict):
                continue
            ident = p.get("ident") or p
            nom = " ".join(x for x in [ident.get("civ"), ident.get("prenom"), ident.get("nom")] if isinstance(x, str) and x)
            qualite = p.get("qualite") or p.get("fonction") or ""
            nom = (nom + (", " + qualite if isinstance(qualite, str) and qualite else "")).strip(", ")
            if nom:
                noms.append(nom)
        return " ; ".join(noms)

    SLUGS = {"PO59051": "lois", "PO59046": "defense", "PO59048": "finances", "PO59047": "affaires-etrangeres",
             "PO419604": "affaires-culturelles", "PO420120": "affaires-sociales", "PO419610": "affaires-economiques",
             "PO419865": "developpement-durable"}

    def url_reunion(self, uid, organe, debut, seance, chambre, dossier):
        if chambre == "Sénat":
            return f"https://agenda.senat.fr/?date={debut[:10]}"
        if organe in self.SLUGS:
            return f"{SITE}/dyn/{LEG}/organes/commissions-permanentes/{self.SLUGS[organe]}/agenda"
        if dossier.get("url"):
            return dossier["url"]
        return f"{SITE}/dyn/{LEG}/organes/{organe}" if organe and not seance else f"{SITE}/dyn/{LEG}/seance-publique"

    # ---------- étapes des textes suivis ----------
    def etapes(self):
        ctx = self.ctx
        out = []
        for d in self.dossiers.values():
            if not d["suivi"]:
                continue
            for acte in self._feuilles(d["actes"]):
                date = _date(acte.get("dateActe"))
                if not date or not ctx.dans_fenetre(date):
                    continue
                code = acte.get("codeActe") or ""
                if code.endswith("-SAISIE") or code.endswith("-NOMIN"):
                    continue  # étapes administratives peu utiles
                lib = (acte.get("libelleActe") or {}).get("nomCanonique") or code
                chambre = "Sénat" if code.startswith("SN") else "AN"
                if code.startswith("CMP"):
                    lib = "CMP — " + lib
                out.append(ctx.evenement(
                    id=f"{d['uid']}-{acte.get('uid')}", debut=date[:10], journee=True, chambre=chambre,
                    type="Étape d'un texte", titre=f"{lib} : {d['titre']}", organe=self.nom_organe(acte.get("organeRef")),
                    texte_id=d["uid"], texte_titre=d["titre"], texte_url=d["url"],
                    url=(d["url_senat"] if chambre == "Sénat" and d.get("url_senat") else d["url"]),
                    themes=d["themes"],
                ))
        # délais limites de dépôt des amendements (quand l'AN les publie)
        for uid, doc in self.docs.items():
            d = self.dossier(doc["dossier"])
            if not d.get("suivi") or not doc.get("depot"):
                continue
            dep = doc["depot"]
            limites = [("séance", (dep.get("amendementsSeance") or {}).get("dateLimiteDepot"), None)]
            for c in premier((dep.get("amendementsCommission") or {}).get("commission")):
                limites.append(("commission", c.get("dateLimiteDepot"), c.get("organeRef")))
            for ou, lim, org in limites:
                date = _date(lim)
                if date and ctx.dans_fenetre(date):
                    out.append(ctx.evenement(
                        id=f"{uid}-dl-{ou}-{org or ''}", debut=date, chambre="AN", type="Amendements",
                        titre=f"Date limite de dépôt des amendements ({ou}) : {d['titre']}",
                        organe=self.nom_organe(org) if org else "Séance publique",
                        texte_id=d["uid"], texte_titre=d["titre"], texte_url=d["url"], url=d["url"], themes=d["themes"]))
        return out

    def _feuilles(self, actes):
        for a in premier((actes or {}).get("acteLegislatif") if isinstance(actes, dict) else actes):
            enfants = a.get("actesLegislatifs")
            if enfants:
                yield from self._feuilles(enfants)
            else:
                yield a

    # ---------- amendements ----------
    def amendements(self):
        """Regroupe par texte et par jour : 'N amendements déposés'. Textes suivis + amendements contenant un mot-clé."""
        ctx, det = self.ctx, self.ctx.detecteur
        groupes = defaultdict(lambda: {"n": 0, "themes": set(), "exemples": [], "organe": None})
        for nom, b in fichiers_zip(telecharger(URL_AMENDEMENTS, max_age_h=20)):
            parts = nom.split("/")
            dos_uid = parts[1] if len(parts) >= 4 else None
            d0 = self.dossier(dos_uid)
            suivi = d0.get("suivi") and bool(d0.get("themes")) and not d0.get("corps")  # texte "sécurité" : tous ses amendements
            # lecture rapide de la date de dépôt sans tout décoder
            m = re.search(rb'"dateDepot":\s*"(\d{4}-\d{2}-\d{2})', b)
            if not m or not ctx.dans_fenetre(m.group(1).decode()):
                continue
            a = json.loads(b)["amendement"]
            corps = (a.get("corps") or {}).get("contenuAuteur") or {}
            texte = texte_brut(_val(corps.get("dispositif"))) + " " + texte_brut(_val(corps.get("exposeSommaire")))
            themes = det.themes(texte)
            if not suivi and not themes:
                continue
            date = m.group(1).decode()
            texte_ref = a.get("texteLegislatifRef")
            examen = (a.get("identification") or {}).get("prefixeOrganeExamen") or ""
            org = re.search(r"PO\d+", a.get("uid", ""))
            cle = (dos_uid, texte_ref, date, org.group(0) if org else "")
            g = groupes[cle]
            g["n"] += 1
            g["themes"].update(themes)
            g["organe"] = org.group(0) if org else None
            g["url"] = g.get("url") or f"{SITE}/dyn/{LEG}/amendements/{a.get('uid')}"
            if len(g["exemples"]) < MAX_LIENS:
                num = (a.get("identification") or {}).get("numeroLong")
                art = ((a.get("pointeurFragmentTexte") or {}).get("division") or {}).get("articleDesignationCourte") or ""
                g["exemples"].append({"t": ligne_amendement(num, art, (a.get("signataires") or {}).get("libelle")),
                                      "u": f"{SITE}/dyn/{LEG}/amendements/{a.get('uid')}"})
        out = []
        for (dos_uid, texte_ref, date, org), g in groupes.items():
            d = self.dossier(dos_uid)
            titre_txt = d.get("titre") or self.docs.get(texte_ref, {}).get("titre") or texte_ref
            lieu = self.nom_organe(org) or ("Séance publique" if org == "PO838901" else org)
            out.append(ctx.evenement(
                id=f"am-{texte_ref}-{date}-{org}", debut=date, journee=True, chambre="AN", type="Amendements",
                titre=f"{g['n']} amendement{'s' if g['n'] > 1 else ''} déposé{'s' if g['n'] > 1 else ''} — {titre_txt}",
                organe=lieu, texte_id=dos_uid if d.get("suivi") else None, texte_titre=d.get("titre"), texte_url=d.get("url"),
                url=g["url"] or d.get("url"),
                amendements=g["exemples"], amendements_autres=max(0, g["n"] - len(g["exemples"])),
                themes=sorted(set(g["themes"]) | set(d.get("themes") or [])),
            ))
        return out

    # ---------- questions ----------
    def questions(self):
        ctx, det = self.ctx, self.ctx.detecteur
        out = []
        for typ, url in URL_QUESTIONS.items():
            for _, b in fichiers_zip(telecharger(url)):
                q = json.loads(b).get("question")
                if not q:
                    continue
                num = (q.get("identifiant") or {}).get("numero")
                idx = q.get("indexationAN") or {}
                analyse = _val((idx.get("analyses") or {}).get("analyse"))
                rubrique = idx.get("rubrique") or ""
                tq = (q.get("textesQuestion") or {}).get("texteQuestion")
                tq = premier(tq)[0] if tq else {}
                texte = texte_brut(tq.get("texte"))
                ministere = (q.get("minInt") or {}).get("developpe") or ""
                d_q = _date(((tq.get("infoJO") or {}).get("dateJO")))
                rep = premier((q.get("textesReponse") or {}).get("texteReponse"))
                d_r = _date(((rep[0].get("infoJO") or {}).get("dateJO"))) if rep else None
                if typ == "QG":
                    texte = texte or texte_brut(rep[0].get("texte"))[:3000] if rep else texte
                    d_q = d_r or _date(((premier((q.get("minAttribs") or {}).get("minAttrib")) or [{}])[0].get("infoJO") or {}).get("dateJO"))
                    d_r = None
                if not ((d_q and ctx.dans_fenetre(d_q)) or (d_r and ctx.dans_fenetre(d_r))):
                    continue
                themes = det.themes(analyse, rubrique, texte)
                if not themes:
                    continue
                auteur = (q.get("auteur") or {}).get("groupe", {}) or {}
                lien = f"{SITE}/dyn/{LEG}/questions/{q['uid']}"
                base = dict(chambre="AN", type="Question", organe=ministere, url=lien, themes=themes,
                            description=(f"Rubrique : {rubrique}\nGroupe : {auteur.get('developpe') or ''}\n\n" + texte[:1500]).strip())
                titre = f"{NOM_QUESTION[typ]} n° {num} — {analyse or rubrique}"
                if d_q and ctx.dans_fenetre(d_q):
                    out.append(ctx.evenement(id=f"{q['uid']}", debut=d_q[:10], journee=True, titre=titre, **base))
                if d_r and ctx.dans_fenetre(d_r):
                    out.append(ctx.evenement(id=f"{q['uid']}-rep", debut=d_r[:10], journee=True, titre="Réponse publiée : " + titre, **base))
        return out
