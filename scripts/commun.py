"""Outils communs : lecture de la configuration, détection des mots-clés, téléchargements."""
import io
import os
import re
import time
import unicodedata
import urllib.request
import zipfile
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
CONFIG = RACINE / "config"
CACHE = Path(os.environ.get("VEILLE_CACHE", RACINE / ".cache"))
UA = "veille-parlementaire-securite/1.0 (+https://github.com/)"


def norm(texte):
    """minuscules, sans accents, apostrophes et espaces unifiés"""
    if not texte:
        return ""
    t = unicodedata.normalize("NFD", str(texte))
    t = "".join(c for c in t if unicodedata.category(c) != "Mn").lower()
    t = t.replace("’", "'").replace(" ", " ")
    return re.sub(r"\s+", " ", t)


def lire_lignes(nom):
    for ligne in (CONFIG / nom).read_text(encoding="utf-8").splitlines():
        ligne = ligne.strip()
        if ligne and not (ligne.startswith("#") and not ligne.startswith("##")):
            yield ligne


class Detecteur:
    """Repère les thèmes (fichier mots-cles.txt) présents dans un texte."""

    def __init__(self):
        self.regles = []  # (theme, regex)
        theme = "Divers"
        for ligne in lire_lignes("mots-cles.txt"):
            if ligne.startswith("##"):
                theme = ligne.lstrip("#").strip() or "Divers"
                continue
            mot = norm(ligne)
            # un mot-clé en MAJUSCULES courtes (sigle) doit être un mot entier : CRS, SDIS…
            sigle = ligne.isupper() and len(ligne) <= 6
            motif = r"(?<![a-z0-9])" + re.escape(mot) + (r"(?![a-z0-9])" if sigle else "")
            self.regles.append((theme, re.compile(motif)))

    def themes(self, *textes):
        t = norm(" ".join(x for x in textes if x))
        vus = []
        for theme, rx in self.regles:
            if theme not in vus and rx.search(t):
                vus.append(theme)
        return vus


def lire_commissions():
    res = []
    for ligne in lire_lignes("commissions.txt"):
        morceaux = [m.strip() for m in ligne.split("|")]
        if len(morceaux) < 3:
            continue
        chambre, code, nom = morceaux[:3]
        mode = norm(morceaux[3]) if len(morceaux) > 3 else "tout"
        res.append({"chambre": "AN" if norm(chambre) == "an" else "Sénat", "code": code, "nom": nom,
                    "tout": mode.startswith("tout")})
    return res


def telecharger(url, nom=None, max_age_h=6):
    """Télécharge url dans le cache (réutilisé s'il a moins de max_age_h heures). Renvoie le chemin."""
    CACHE.mkdir(parents=True, exist_ok=True)
    dest = CACHE / (nom or re.sub(r"[^A-Za-z0-9._-]", "_", url.split("://", 1)[-1])[-150:])
    if dest.exists() and time.time() - dest.stat().st_mtime < max_age_h * 3600 and dest.stat().st_size > 0:
        return dest
    derniere = None
    for essai in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=600) as r, open(str(dest) + ".part", "wb") as f:
                while True:
                    bloc = r.read(1 << 20)
                    if not bloc:
                        break
                    f.write(bloc)
            os.replace(str(dest) + ".part", dest)
            return dest
        except Exception as e:  # réseau capricieux : on réessaie
            derniere = e
            time.sleep(5 * (essai + 1))
    raise RuntimeError(f"téléchargement impossible {url} : {derniere}")


def fichiers_zip(chemin, suffixe=".json"):
    """Itère (nom, octets) sur les fichiers d'une archive zip."""
    with zipfile.ZipFile(chemin) as z:
        for info in z.infolist():
            if info.filename.endswith(suffixe):
                yield info.filename, z.read(info)


def texte_brut(html):
    if not html:
        return ""
    t = re.sub(r"<[^>]+>", " ", str(html))
    t = t.replace("&nbsp;", " ").replace("&amp;", "&").replace("&#39;", "'").replace("&quot;", '"')
    return re.sub(r"\s+", " ", t).strip()


def premier(x):
    """Les JSON de l'AN mettent parfois un objet, parfois une liste : on uniformise."""
    if x is None:
        return []
    return x if isinstance(x, list) else [x]
