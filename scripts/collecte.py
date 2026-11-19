"""Collecte quotidienne : produit site/data/evenements.json.

Lancer en local :  python scripts/collecte.py
"""
import json
import sys
import time
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from commun import RACINE, Detecteur, lire_commissions, lire_lignes, norm  # noqa: E402
from source_an import AssembleeNationale  # noqa: E402
from source_senat import Senat  # noqa: E402

SORTIE = RACINE / "site" / "data" / "evenements.json"
JOURS_PASSES = 45      # on garde l'historique récent (6 semaines)
JOURS_FUTURS = 365


class Contexte:
    norm = staticmethod(norm)

    def __init__(self):
        self.detecteur = Detecteur()
        self.commissions = lire_commissions()
        self.textes_forces = [norm(l) for l in lire_lignes("textes-suivis.txt") if not l.startswith("##")]
        auj = date.today()
        self.debut = (auj - timedelta(days=JOURS_PASSES)).isoformat()
        self.fin = (auj + timedelta(days=JOURS_FUTURS)).isoformat()
        self.debut_fenetre_agenda = auj - timedelta(days=30)
        self.fin_fenetre_agenda = auj + timedelta(days=60)
        self.debut_fenetre_textes = (auj - timedelta(days=730)).isoformat()

    def dans_fenetre(self, d):
        return self.debut <= d[:10] <= self.fin

    @staticmethod
    def evenement(**k):
        e = {"id": k.pop("id"), "debut": k.pop("debut")}
        e.update({x: v for x, v in k.items() if v not in (None, "", [])})
        e["journee"] = bool(k.get("journee")) or len(e["debut"]) <= 10
        return e


def main():
    t0 = time.time()
    ctx = Contexte()
    sources, evenements = [], []

    def etape(nom, fonction):
        t = time.time()
        try:
            res = fonction()
            n = len(res) if isinstance(res, list) else res
            if isinstance(res, list):
                evenements.extend(res)
            sources.append({"nom": nom, "ok": True, "nb": n})
            print(f"[ok] {nom} : {n} ({time.time() - t:.0f}s)", flush=True)
        except Exception as e:  # une source en panne ne bloque pas les autres
            traceback.print_exc()
            sources.append({"nom": nom, "ok": False, "erreur": str(e)[:300]})
            print(f"[ÉCHEC] {nom} : {e}", flush=True)

    an, senat = AssembleeNationale(ctx), Senat(ctx)
    etape("AN – commissions (référentiel)", an.charger_organes)
    etape("AN – textes suivis", an.charger_dossiers)
    etape("AN – contenu des textes", an.contenu_textes)
    etape("AN – agenda", an.agenda)
    etape("AN – étapes des textes", an.etapes)
    etape("AN – amendements", an.amendements)
    etape("AN – questions", an.questions)
    etape("Sénat – textes suivis", senat.charger_dossiers)
    etape("Sénat – contenu des textes", senat.contenu_textes)
    etape("Sénat – agenda", senat.agenda)
    etape("Sénat – étapes des textes", senat.etapes)
    etape("Sénat – amendements", senat.amendements)
    etape("Sénat – questions", senat.questions)

    # Les séances du Sénat figurent aussi dans l'agenda AN : on garde la version Sénat si elle a marché.
    if any(s["nom"] == "Sénat – agenda" and s["ok"] for s in sources):
        evenements = [e for e in evenements if not e["id"].startswith("RUSN")]

    vus, uniques = set(), []
    for e in sorted(evenements, key=lambda e: (e["debut"], e["id"])):
        if e["id"] not in vus:
            vus.add(e["id"])
            uniques.append(e)

    # Si tout a échoué, on ne remplace pas les données de la veille.
    if not uniques and SORTIE.exists():
        print("Aucune donnée collectée : on conserve le fichier précédent.")
        sys.exit(1)

    SORTIE.parent.mkdir(parents=True, exist_ok=True)
    SORTIE.write_text(json.dumps({
        "genere_le": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fenetre": {"debut": ctx.debut, "fin": ctx.fin},
        "sources": sources,
        "evenements": uniques,
    }, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{len(uniques)} événements écrits dans {SORTIE} en {time.time() - t0:.0f}s")
    # petit résumé lisible dans le journal GitHub Actions
    from collections import Counter
    print("Par type :", dict(Counter((e["chambre"], e["type"]) for e in uniques)))
    print("Par thème :", dict(Counter(t for e in uniques for t in e.get("themes", []))))
    auj = date.today().isoformat()
    for e in [e for e in uniques if e["debut"] >= auj][:40]:
        print(" ", e["debut"], e["chambre"], e["type"], "|", e["titre"][:110], "|", e.get("url", ""))


if __name__ == "__main__":
    main()
