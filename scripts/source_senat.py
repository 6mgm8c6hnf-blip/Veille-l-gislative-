"""Collecte Sénat : agenda (API publique de agenda.senat.fr), dossiers législatifs, questions, amendements (data.senat.fr / senat.fr)."""
import csv
import io
import json
import re
import time
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from commun import UA, ligne_amendement, telecharger, texte_brut

API_AGENDA = "https://www.senat.fr/api/v1/agenda/events?date={d}"
URL_DOSLEG = "https://data.senat.fr/data/dosleg/dossiers-legislatifs.csv"
URL_QUESTIONS = "https://data.senat.fr/data/questions/questions-depuis-un-an.csv"
URL_QG = "https://data.senat.fr/data/questions/qg.csv"
NATURE = {"QE": "Question écrite", "QOSD": "Question orale", "QO": "Question orale", "QG": "Question d'actualité au Gouvernement",
          "QC": "Question cribles", "QOAD": "Question orale avec débat"}


def _get(url, timeout=40, essais=3):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json, text/html, */*"})
    err = None
    for essai in range(essais):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            err = e
        except Exception as e:  # coupure réseau : on réessaie
            err = e
        time.sleep(2 * (essai + 1))
    raise RuntimeError(f"{url} : {err}")


def _https(u):
    u = (u or "").strip()
    if u.startswith("//"):
        return "https:" + u
    if u.startswith("/"):
        return "https://www.senat.fr" + u
    return u.replace("http://", "https://")


def _csv(chemin):
    brut = open(chemin, "rb").read()
    try:
        texte = brut.decode("utf-8")
    except UnicodeDecodeError:
        texte = brut.decode("latin-1")
    return list(csv.DictReader(io.StringIO(texte), delimiter=";"))


def _date_fr(s):
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", s or "")
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None


class Senat:
    def __init__(self, ctx):
        self.ctx = ctx
        self.dossiers = {}  # url -> infos
        self.pages = {}  # pages de dossier déjà téléchargées

    # ---------- agenda ----------
    def agenda(self):
        ctx, det = self.ctx, self.ctx.detecteur
        suivies = [c for c in ctx.commissions if c["chambre"] == "Sénat"]
        out = []
        jour = ctx.debut_fenetre_agenda
        erreurs = 0
        while jour <= ctx.fin_fenetre_agenda:
            try:
                brut = _get(API_AGENDA.format(d=jour.isoformat()), timeout=15, essais=2)
                data = json.loads(brut) if brut else {}
                erreurs = 0
            except Exception as e:
                print(f"   Sénat agenda {jour} : {e}", flush=True)
                erreurs += 1
                if erreurs >= 3:
                    raise
                jour += timedelta(days=1)
                continue
            for j in data.values() if isinstance(data, dict) else []:
                for e in j.get("events", []) if isinstance(j, dict) else []:
                    ev = self._evenement_agenda(e, suivies, det)
                    if ev:
                        out.append(ev)
            jour += timedelta(days=1)
            time.sleep(0.2)
        return out

    def _evenement_agenda(self, e, suivies, det):
        titre = texte_brut(e.get("title") or "")
        instances = [texte_brut(i) for i in (e.get("instances") or [])]
        organe = ", ".join(instances)
        texte = " ".join([titre, organe, texte_brut(e.get("description") or "")])
        themes = det.themes(texte)
        norm_org = self.ctx.norm(organe)
        commission = next((c for c in suivies if self.ctx.norm(c["code"]) in norm_org), None)
        if not themes and not (commission and commission["tout"]):
            return None
        seance = "seance publique" in norm_org
        if re.match(r"\s*audition", titre, re.I) or "audition" in self.ctx.norm(titre)[:40]:
            typ = "Audition"
        elif seance:
            typ = "Séance publique"
        else:
            typ = "Réunion de commission"
        h = re.match(r"(\d{1,2})h(\d{2})?", e.get("hour") or "")
        debut = e["date"] + (f"T{int(h.group(1)):02d}:{h.group(2) or '00'}" if h else "")
        huis = None
        if e.get("public") is False:
            huis = True
        elif e.get("public") is True:
            huis = False
        if e.get("forecast"):
            titre = "(prévisionnel) " + titre
        return self.ctx.evenement(
            id=f"senat-agenda-{e.get('id')}", debut=debut, journee=not h, chambre="Sénat", type=typ, titre=titre,
            organe=organe, lieu=texte_brut(e.get("place") or ""), huis_clos=huis,
            url=f"https://agenda.senat.fr/?date={e['date']}", themes=themes)

    # ---------- dossiers législatifs ----------
    def charger_dossiers(self):
        det = self.ctx.detecteur
        forces = self.ctx.textes_forces
        n = 0
        for row in _csv(telecharger(URL_DOSLEG)):
            titre = row.get("Titre") or ""
            url = _https(row.get("URL du dossier"))
            themes_senat = row.get("Thèmes") or ""
            themes = det.themes(titre)
            police = "police et securite" in self.ctx.norm(themes_senat)
            force = any(f in self.ctx.norm(titre) or f in url for f in forces)
            if police and "Police et sécurité" not in themes and not themes:
                themes = ["Police et sécurité (Sénat)"]
            d_init = _date_fr(row.get("Date initiale"))
            etat = row.get("État du dossier") or ""
            suivi = bool(themes) or force
            self.dossiers[url] = {"titre": titre, "url": url, "themes": themes, "suivi": suivi, "etat": etat,
                                  "date": d_init, "promulgation": _date_fr(row.get("Date de promulgation"))}
            n += suivi
        return n

    # ---------- contenu des textes (pas seulement le titre) ----------
    def _page(self, url):
        if url not in self.pages:
            try:
                self.pages[url] = (_get(url, timeout=20, essais=2) or b"").decode("utf-8", "replace")
            except Exception:
                self.pages[url] = ""
        return self.pages[url]

    def contenu_textes(self):
        """Lit le texte des dossiers récents en cours dont le titre n'a pas de mot-clé."""
        ctx, det = self.ctx, self.ctx.detecteur
        fini = re.compile(r"promulgu|caduc|retir", re.I)
        debut = (date.today() - timedelta(days=365)).isoformat()
        candidats = [d for d in self.dossiers.values() if not d["suivi"] and not d["promulgation"]
                     and not fini.search(d["etat"]) and d["date"] and d["date"] >= debut]
        candidats.sort(key=lambda d: d["date"], reverse=True)
        print(f"   Sénat : {len(candidats)} textes récents à lire", flush=True)
        limite = time.time() + 360

        def lire(d):
            if time.time() > limite:
                return None
            page = self._page(d["url"])
            liens = re.findall(r"/leg/((?:pjl|ppl)\d{2}-\d+)\.html", page)
            if not liens:
                return d, None
            try:
                brut = _get(f"https://www.senat.fr/leg/{liens[0]}.html", timeout=20, essais=2)
            except Exception:
                return d, None
            return d, texte_brut((brut or b"").decode("utf-8", "replace"))

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
        print(f"   Sénat : {lus} textes lus, {trouves} retenus grâce à leur contenu", flush=True)
        return trouves

    def etapes(self):
        """Dépôt et promulgation des textes suivis au Sénat (les autres étapes viennent des données AN et de l'agenda)."""
        ctx = self.ctx
        out = []
        for d in self.dossiers.values():
            if not d["suivi"]:
                continue
            for quand, quoi in ((d["date"], "Dépôt au Sénat"), (d["promulgation"], "Promulgation")):
                if quand and ctx.dans_fenetre(quand):
                    out.append(ctx.evenement(
                        id=f"senat-dos-{d['url'].rsplit('/', 1)[-1]}-{quoi[:4]}", debut=quand, journee=True, chambre="Sénat",
                        type="Étape d'un texte", titre=f"{quoi} : {d['titre']}", texte_id=d["url"], texte_titre=d["titre"],
                        texte_url=d["url"], url=d["url"], themes=d["themes"], description=f"État du dossier : {d['etat']}"))
        return out

    # ---------- amendements des textes suivis en cours ----------
    def amendements(self):
        ctx = self.ctx
        out = []
        fini = re.compile(r"promulgu|caduc|retir", re.I)
        actifs = [d for d in self.dossiers.values() if d["suivi"] and not d["promulgation"] and not fini.search(d["etat"])
                  and d["date"] and d["date"] >= ctx.debut_fenetre_textes]
        actifs.sort(key=lambda d: d["date"], reverse=True)
        print(f"   Sénat : {len(actifs)} textes en cours à examiner pour les amendements", flush=True)
        limite = time.time() + 480  # au plus 8 minutes pour cette étape
        with ThreadPoolExecutor(max_workers=6) as pool:
            futurs = [pool.submit(self._amendements_dossier, d, limite) for d in actifs]
            for f in futurs:
                try:
                    out.extend(f.result())
                except Exception as e:
                    print(f"   Sénat amendements : {e}", flush=True)
        if time.time() > limite:
            print("   Sénat : temps maximal atteint pour les amendements (liste partielle)", flush=True)
        return out

    def _amendements_dossier(self, d, limite):
        ctx = self.ctx
        out = []
        if time.time() > limite:
            return out
        page = self._page(d["url"])
        if not page:
            return out
        textes = set(re.findall(r"/leg/(?:pjl|ppl|ppr|tas)(\d{2})-(\d+)\.html", page))
        textes |= set(re.findall(r"/(?:enseance|encommission|amendements(?:/commissions)?)/(\d{4})-\d{4}/(\d+)/", page))
        # texte suivi grâce à un mot-clé de son titre : tous ses amendements ;
        # texte ajouté à la main (ex. budget) : seulement les amendements qui contiennent un mot-clé
        tous = bool(d["themes"]) and not d.get("corps")
        groupes = defaultdict(lambda: {"n": 0, "ex": [], "url": None, "themes": set()})
        for an, num in textes:
            an = an if len(an) == 4 else "20" + an
            session = f"{an}-{int(an) + 1}"
            for genre, url in (("séance", f"https://www.senat.fr/amendements/{session}/{num}/jeu_complet_{session}_{num}.csv"),
                               ("commission", f"https://www.senat.fr/amendements/commissions/{session}/{num}/jeu_complet_commission_{session}_{num}.csv")):
                if time.time() > limite:
                    break
                try:
                    brut = _get(url, timeout=20, essais=2)
                except Exception:
                    continue
                if not brut:
                    continue
                lignes = brut.decode("latin-1", "replace").splitlines()
                if lignes and lignes[0].startswith("sep="):
                    lignes = lignes[1:]
                for row in csv.DictReader(lignes, delimiter="\t"):
                    row = {(k or "").strip(): (v or "").strip() for k, v in row.items()}
                    jour = (row.get("Date de dépôt") or "")[:10]
                    if not re.match(r"\d{4}-\d{2}-\d{2}", jour) or not ctx.dans_fenetre(jour):
                        continue
                    themes_am = ctx.detecteur.themes(texte_brut(row.get("Dispositif")), texte_brut(row.get("Objet")))
                    if not tous and not themes_am:
                        continue
                    g = groupes[(jour, genre, num)]
                    g["themes"].update(themes_am)
                    g["n"] += 1
                    g["url"] = g["url"] or row.get("Url amendement")
                    if len(g["ex"]) < 60:
                        g["ex"].append({"t": ligne_amendement(row.get("Numéro"), row.get("Subdivision"), row.get("Auteur")),
                                         "u": _https(row.get("Url amendement") or "") or None})
        for (jour, genre, num), g in groupes.items():
            out.append(ctx.evenement(
                id=f"senat-am-{num}-{genre}-{jour}", debut=jour, journee=True, chambre="Sénat", type="Amendements",
                titre=f"{g['n']} amendement{'s' if g['n'] > 1 else ''} déposé{'s' if g['n'] > 1 else ''} ({genre}) — {d['titre']}",
                organe="Séance publique" if genre == "séance" else "Commission", texte_id=d["url"], texte_titre=d["titre"],
                texte_url=d["url"], url=_https(g["url"] or d["url"]),
                amendements=g["ex"], amendements_autres=max(0, g["n"] - len(g["ex"])),
                themes=sorted(set(d["themes"]) | g["themes"])))
        return out

    # ---------- questions ----------
    def questions(self):
        ctx, det = self.ctx, self.ctx.detecteur
        out = []
        for url, defaut in ((URL_QUESTIONS, None), (URL_QG, "QG")):
            for row in _csv(telecharger(url)):
                nature = (row.get("Nature") or defaut or "").strip()
                pub = (row.get("Date de publication JO") or "")[:10]
                rep = (row.get("Date de réponse JO") or "")[:10]
                titre = row.get("Titre") or ""
                ministere = row.get("Ministère de dépôt") or ""
                themes = det.themes(titre, row.get("Thème(s)"))
                if not themes:
                    continue
                auteur = " ".join(x for x in [row.get("Civilité"), row.get("Prénom"), row.get("Nom")] if x)
                base = dict(chambre="Sénat", type="Question", organe=ministere,
                            url=_https(row.get("URL Question")), themes=themes,
                            description=f"{auteur} ({row.get('Groupe') or ''}, {row.get('Circonscription') or ''})\nThèmes : {row.get('Thème(s)') or ''}")
                lib = f"{NATURE.get(nature, 'Question')} n° {row.get('Numéro')} — {titre}"
                ref = row.get("Référence") or row.get("Numéro")
                if pub and ctx.dans_fenetre(pub):
                    out.append(ctx.evenement(id=f"senat-q-{ref}", debut=pub, journee=True, titre=lib, **base))
                if rep and ctx.dans_fenetre(rep):
                    out.append(ctx.evenement(id=f"senat-q-{ref}-rep", debut=rep, journee=True, titre="Réponse publiée : " + lib, **base))
        return out
