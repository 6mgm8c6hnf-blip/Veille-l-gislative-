# Veille parlementaire sécurité

Un site web qui rassemble, dans un **calendrier** et une **liste filtrable**, toutes les échéances utiles de l'Assemblée nationale et du Sénat sur la **gendarmerie**, la **police** et la **sécurité civile** :

- les **auditions** et **réunions de commission** (publiques ou à huis clos) ;
- les **séances publiques** consacrées aux textes suivis ;
- les **étapes des projets et propositions de loi** (dépôt, examen en commission, séance, CMP, promulgation) ;
- les **amendements** déposés sur ces textes, et les **dates limites de dépôt** quand elles sont publiées ;
- les **questions au Gouvernement**, questions écrites et orales.

Chaque événement renvoie vers **sa page officielle** (assemblee-nationale.fr ou senat.fr). Les données sont mises à jour **automatiquement chaque matin** à partir des données ouvertes officielles.

---

## Utiliser le site

- **Rechercher** : tapez un mot (ex. « LOPMI », « pompiers », « DGGN »).
- **Filtrer** par texte, par thème, par chambre et par type d'événement.
- **Liste** ou **Calendrier** (vue mois ou semaine).
- **Cliquer sur un événement** affiche le détail et le lien vers la source.
- **Exporter vers mon agenda (.ics)** : télécharge les événements affichés pour les importer dans Outlook ou Google Agenda.
- **Copier le lien de cette vue** : copie l'adresse du site avec vos filtres, pour la partager avec l'équipe.

## Modifier ce qui est suivi (sans rien installer)

Tout se règle dans le dossier [`config/`](config/), directement sur GitHub :

| Fichier | À quoi il sert |
|---|---|
| [`config/mots-cles.txt`](config/mots-cles.txt) | Les **mots-clés**, rangés par thème. C'est le fichier principal. |
| [`config/commissions.txt`](config/commissions.txt) | Les **commissions suivies** (tout afficher, ou seulement ce qui contient un mot-clé). |
| [`config/textes-suivis.txt`](config/textes-suivis.txt) | Des **textes à suivre** en plus (ex. le projet de loi de finances). |

**Pour modifier un fichier :**
1. Cliquez sur le fichier (par exemple `config/mots-cles.txt`).
2. Cliquez sur l'icône **crayon** ✏️ en haut à droite (« Edit this file »).
3. Ajoutez ou supprimez des lignes. Les explications sont en haut du fichier.
4. Cliquez sur le bouton vert **Commit changes…**, puis encore sur **Commit changes**.

Le site se met à jour tout seul quelques minutes après (vous pouvez suivre l'avancement dans l'onglet **Actions**).

## Lancer une mise à jour tout de suite

Onglet **Actions** → **Mise à jour du site** (à gauche) → bouton **Run workflow** → **Run workflow**.

## Installation (à faire une seule fois)

1. **Rendre le dépôt public** (nécessaire pour GitHub Pages avec un compte gratuit) :
   *Settings* → tout en bas, *Danger Zone* → **Change visibility** → **Make public**.
   Les données affichées sont publiques (données ouvertes du Parlement) ; seuls vos mots-clés deviennent visibles.
2. **Activer GitHub Pages** : *Settings* → *Pages* (menu de gauche) → *Build and deployment* → *Source* : choisir **GitHub Actions**.
3. **Lancer la première mise à jour** : onglet *Actions* → *Mise à jour du site* → **Run workflow**.
4. Au bout de 5 à 10 minutes, l'adresse du site apparaît dans *Settings* → *Pages* (de la forme `https://<compte>.github.io/Veille-l-gislative-/`). C'est ce lien qu'on partage à l'équipe.

## Sources des données

| Source | Contenu |
|---|---|
| [data.assemblee-nationale.fr](https://data.assemblee-nationale.fr/) | Agenda des réunions et séances, dossiers législatifs, amendements, questions (QAG, écrites, orales) |
| [data.senat.fr](https://data.senat.fr/) | Dossiers législatifs (avec le thème « Police et sécurité »), questions (écrites, orales, d'actualité) |
| [agenda.senat.fr](https://agenda.senat.fr/) | Agenda des commissions et de la séance du Sénat |
| [senat.fr](https://www.senat.fr/) | Amendements des textes suivis (fichiers publiés par texte) |

Fenêtre couverte : les 6 dernières semaines et tout ce qui est annoncé à venir.

## Limites connues

- Les **dates limites de dépôt des amendements** ne sont affichées que lorsque l'Assemblée les publie dans ses données ; au Sénat, elles ne sont pas encore récupérées.
- L'agenda du Sénat est récupéré sur 30 jours en arrière et 60 jours en avant.
- La détection par mots-clés peut laisser passer quelques événements hors sujet (ex. « secours ») : affinez la liste dans `config/mots-cles.txt`.

## Pour les curieux : comment ça marche

- `scripts/collecte.py` télécharge les données, garde ce qui correspond aux mots-clés et aux commissions, et écrit `site/data/evenements.json`.
- `site/` contient le site (HTML, CSS, JavaScript, sans dépendance externe).
- `.github/workflows/mise-a-jour.yml` lance la collecte chaque matin et publie le site sur GitHub Pages.

Pour tester sur un ordinateur : `python scripts/collecte.py` puis `python -m http.server -d site` et ouvrir http://localhost:8000.
