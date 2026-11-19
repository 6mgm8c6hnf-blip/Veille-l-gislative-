"""Outils communs : lecture de la configuration, détection des mots-clés, téléchargements."""
import html as _html
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
    """Repère les thèmes (fichier mots-cles.txt) présents dans un texte.

    Une ligne qui commence par "+" est un mot-clé d'appui : il ne compte que si
    le texte contient aussi au moins un mot-clé principal (sans "+").
    """

    def __init__(self):
        self.regles = []  # (theme, regex, appui)
        theme = "Divers"
        for ligne in lire_lignes("mots-cles.txt"):
            if ligne.startswith("##"):
                theme = ligne.lstrip("#").strip() or "Divers"
                continue
            appui = ligne.startswith("+")
            ligne = ligne.lstrip("+").strip()
            if not ligne:
                continue
            mot = norm(ligne)
            # un mot-clé en MAJUSCULES courtes (sigle) doit être un mot entier : CRS, SDIS…
            sigle = ligne.isupper() and len(ligne) <= 6
            motif = r"(?<![a-z0-9])" + re.escape(mot) + (r"(?![a-z0-9])" if sigle else "")
            self.regles.append((theme, re.compile(motif), appui))

    def themes(self, *textes):
        t = norm(" ".join(x for x in textes if x))
        principaux, appuis = [], []
        for theme, rx, appui in self.regles:
            cible = appuis if appui else principaux
            if theme not in cible and rx.search(t):
                cible.append(theme)
        if not principaux:
            return []
        return principaux + [x for x in appuis if x not in principaux]


    def themes_corps(self, texte, seuil=3):
        """Pour le corps d'un texte (long) : il faut au moins `seuil` occurrences
        de mots-clés principaux, pour éviter qu'une seule mention isolée suffise."""
        t = norm(texte)
        total, principaux, appuis = 0, [], []
        for theme, rx, appui in self.regles:
            n = len(rx.findall(t))
            if not n:
                continue
            if appui:
                appuis.append(theme)
            else:
                total += n
                if theme not in principaux:
                    principaux.append(theme)
        if total < seuil:
            return []
        return principaux + [x for x in dict.fromkeys(appuis) if x not in principaux]


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
    est_zip = url.lower().endswith(".zip")
    if dest.exists() and time.time() - dest.stat().st_mtime < max_age_h * 3600 and dest.stat().st_size > 0 \
            and (not est_zip or zipfile.is_zipfile(dest)):
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
            if est_zip and not zipfile.is_zipfile(str(dest) + ".part"):
                raise RuntimeError("archive incomplète (fichier en cours de mise à jour côté source ?)")
            os.replace(str(dest) + ".part", dest)
            return dest
        except Exception as e:  # réseau capricieux : on réessaie
            derniere = e
            time.sleep(30 * (essai + 1))
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
    for _ in range(3):  # certaines sources encodent deux fois : "&amp;#160;"
        if "&" not in t:
            break
        t = _html.unescape(t)
    t = t.replace("\xa0", " ").replace("\u202f", " ")
    return re.sub(r"\s+", " ", t).strip()


def resume_auteurs(libelle, max_noms=2):
    """'Mme A, Mme B, M. C, ...' -> 'Mme A, Mme B et 28 autres signataires'.
    Garde tel quel 'M. X et les membres du groupe Y'."""
    t = texte_brut(libelle)
    if not t:
        return ""
    if re.search(r"\bet les membres\b|\bgroupe\b", t, re.I) and t.count(",") < 2:
        return t
    noms = [n.strip() for n in re.split(r",\s*|\s+et\s+(?=(?:M\.|Mme|MM\.|Mmes)\s)", t) if n.strip()]
    if len(noms) <= max_noms + 1:
        return t
    reste = len(noms) - max_noms
    return ", ".join(noms[:max_noms]) + f" et {reste} autre{'s' if reste > 1 else ''} signataire{'s' if reste > 1 else ''}"


def ligne_amendement(num, article, auteur):
    """Une ligne lisible : 'Amendement n° 74 · article 5 · Mme Bentorki et 30 autres signataires'."""
    morceaux = [f"Amendement n° {num}" if num else "Amendement"]
    art = texte_brut(article)
    if art:
        art = re.sub(r"^ART(?:ICLE)?\.?\s*", "article ", art, flags=re.I)
        art = re.sub(r"^APR[ÈE]S\s+ART(?:ICLE)?\.?\s*", "après l'article ", art, flags=re.I)
        art = re.sub(r"^AVANT\s+ART(?:ICLE)?\.?\s*", "avant l'article ", art, flags=re.I)
        morceaux.append(re.sub(r"(\d)ER\b", r"\1er", art))
    aut = resume_auteurs(auteur)
    if aut:
        morceaux.append(aut)
    return " · ".join(morceaux)


def premier(x):
    """Les JSON de l'AN mettent parfois un objet, parfois une liste : on uniformise."""
    if x is None:
        return []
    return x if isinstance(x, list) else [x]
