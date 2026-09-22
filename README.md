# VIE 2 GO 🌍

VIE 2 GO rassemble les offres de **Business France** et de **Welcome to the Jungle** pour les consulter dans une seule interface : recherche, filtres et tri.

**Site actuellement indiqué pour le projet : [vie-2-go.onrender.com](https://vie-2-go.onrender.com/).**

Ce README décrit le code présent dans ce dépôt. La migration vers un hébergement sans Render, décrite à la fin, est une proposition : elle n’est pas encore implémentée.

## Comprendre le projet en quelques mots

Le projet comporte trois parties :

- **L’interface**, ce que le visiteur voit et utilise dans son navigateur.
- **Le serveur**, qui reçoit les demandes de l’interface et lui renvoie les annonces.
- **Le collecteur**, qui récupère les offres auprès des deux sources et les transforme dans un format commun.

Les annonces sont conservées dans un fichier JSON, une sorte de document structuré que les programmes peuvent lire. Il n’y a pas de base de données à installer.

```text
Business France ────────┐
                       ├── Collecteur Python ──> vie_offers.json
Welcome to the Jungle ─┘                              │
                                                      ▼
                                               Serveur Flask
                                                      │
                                                      ▼
                                            Interface du navigateur
                                            Filtres et tri
```

Render fournit actuellement l’hébergement : une machine distante qui exécute le serveur même lorsque l’ordinateur du propriétaire est éteint. GitHub conserve le code ; il n’exécute pas automatiquement le collecteur dans l’état actuel du dépôt.

## À quoi sert chaque fichier ?

| Fichier | Rôle |
| --- | --- |
| [`index.html`](index.html) | Toute l’interface : structure HTML, styles CSS et logique JavaScript. Affiche les annonces, les filtres et le globe de chargement. |
| [`api.py`](api.py) | Serveur principal Flask. Sert la page, lit les annonces, lance les collectes en arrière-plan, expose leur progression. |
| [`unify_vie_offers.py`](unify_vie_offers.py) | Collecte les deux sources, harmonise les champs des offres, déduit certaines catégories, déduplique et exporte les données. Peut aussi s’exécuter en ligne de commande. |
| `vie_offers.json` | Cache des annonces : contient les offres et leurs métadonnées. Il évite de devoir tout récupérer à chaque lecture. |
| [`requirements.txt`](requirements.txt) | Liste des bibliothèques Python à installer. |
| [`Procfile`](Procfile) | Commande de démarrage en hébergement : lance le serveur avec Gunicorn. |
| [`index_OLD.html`](index_OLD.html) | Ancienne version de l’interface, conservée comme référence. Le serveur principal sert `index.html`. |
| [`tests/test_reliability.py`](tests/test_reliability.py) | Tests Python de fiabilité du serveur et de la collecte. |
| [`tests/frontend.test.cjs`](tests/frontend.test.cjs) | Tests JavaScript de comportements de l’interface. |
| [`.gitignore`](.gitignore) | Indique à Git les fichiers locaux à ignorer, notamment les secrets et les fichiers temporaires. |

Les dossiers `__pycache__` sont des caches générés par Python, pas des éléments à modifier.

## Que se passe-t-il quand on ouvre le site ?

1. Le serveur fournit `index.html` au navigateur.
2. L’interface demande une actualisation avec `POST /api/scrape` et `force:false`.
3. Si les deux sources ont été actualisées avec succès depuis moins de **15 minutes**, le serveur réutilise les annonces enregistrées.
4. Sinon, il lance une collecte et l’interface suit sa progression via `/api/status`.
5. Lorsque cette étape se termine, l’interface charge les annonces via `/api/offers` et les statistiques via `/api/stats`.
6. Les filtres et le tri s’appliquent ensuite dans le navigateur, sans relancer la collecte.

Le bouton de rafraîchissement envoie `force:true` pour demander une nouvelle collecte même si le cache est récent. Une restauration de la page par le bouton retour du navigateur déclenche aussi une vérification de fraîcheur.

**Deux attentes différentes peuvent donc se cumuler :** le démarrage du serveur hébergé et la récupération des nouvelles annonces. Le fonctionnement actuel attend la fin de la tentative d’actualisation avant de charger le catalogue, même si un ancien cache existe.

Chaque requête de l’interface est limitée à 20 secondes, lecture JSON comprise. Le suivi d’une collecte est limité à 5 minutes. Dépasser cette attente dans le navigateur n’arrête pas le travail du serveur ; une visite ultérieure peut retrouver ses résultats.

## Comment les annonces sont-elles récupérées ?

Le script Python envoie des requêtes HTTP aux services de données utilisés par les sources :

- **Business France** : service de recherche CiViWeb. Le code tente de récupérer sa clé dans la configuration publique de la page de recherche ; `CIVIWEB_API_KEY` permet de fournir une valeur explicitement.
- **Welcome to the Jungle** : service de recherche Algolia, configuré avec `ALGOLIA_APP_ID` et `ALGOLIA_API_KEY`.

Le collecteur ne lance pas de navigateur automatisé. Il transforme les réponses en objets communs avec notamment un titre, une entreprise, un pays, une ville, une durée et un lien vers l’annonce.

**Python reste adapté à cette tâche** : requêtes réseau, traitement de données et export JSON. Le remplacer par un autre langage ne supprimerait pas l’attente de démarrage de Render. Séparer la collecte de l’affichage est le changement architectural qui permettrait de ne plus faire attendre le visiteur pendant ce travail.

Les services interrogés peuvent changer leur format ou leur authentification : une collecte automatique peut donc nécessiter une maintenance ponctuelle.

## Cache et erreurs

### Cache côté serveur

`vie_offers.json` contient deux grandes sections : `offers` pour les annonces et `metadata` pour les informations de collecte. Le serveur ajoute notamment les dates d’actualisation par source.

Lors d’une collecte lancée par `api.py` :

- une seule collecte peut démarrer à la fois ; les autres visiteurs suivent celle déjà en cours ;
- une source qui échoue conserve ses anciennes annonces et sa date de fraîcheur ;
- les résultats partiels d’une source en échec sont ignorés ;
- le fichier est remplacé de façon atomique, pour éviter qu’un lecteur lise un JSON à moitié écrit ;
- le cache conserve les données par source, et les réponses de consultation sont dédupliquées.

**Attention : le mode en ligne de commande de `unify_vie_offers.py` ne reprend pas toute cette protection.** Il exporte les résultats obtenus même lorsqu’une source échoue. Avant d’en faire une tâche planifiée, il faudra y intégrer la conservation des anciennes données et la validation du résultat.

### Préférences côté navigateur

Les filtres et le tri sont stockés dans le `localStorage` du navigateur. Ils sont propres à ce navigateur et ne sont pas synchronisés entre appareils.

Effacer les données du site peut supprimer ces préférences. L’interface reste utilisable si le stockage local est indisponible. Les liens d’annonces sont limités aux protocoles HTTP et HTTPS.

## Lancer le projet sur son ordinateur

Prévoir Python 3 et `pip`. Depuis le dossier du projet :

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Sous Windows, l’activation se fait avec `.venv\Scripts\activate`.

Créer ensuite un fichier `.env` à la racine :

```dotenv
ALGOLIA_APP_ID=votre_app_id
ALGOLIA_API_KEY=votre_api_key
```

Puis démarrer le serveur :

```bash
python3 api.py
```

Ouvrir [http://localhost:5000](http://localhost:5000). Garder le terminal ouvert pendant l’utilisation ; `Ctrl+C` arrête le serveur. Ouvrir seulement `index.html` ne suffit pas, car l’interface appelle l’API Flask.

### Variables de configuration

| Variable | Utilité |
| --- | --- |
| `ALGOLIA_APP_ID` | Identifiant Algolia nécessaire à la collecte Welcome to the Jungle. |
| `ALGOLIA_API_KEY` | Clé Algolia nécessaire à cette même collecte. |
| `CIVIWEB_API_KEY` | Facultative : fournit explicitement la clé Business France au lieu de tenter sa récupération automatique. |
| `PORT` | Port du serveur ; `5000` par défaut en lancement local. |

Les valeurs sensibles restent dans `.env` ou dans les paramètres secrets de l’hébergeur. Elles ne doivent pas être placées dans `index.html` ou publiées dans Git.

### Tester une collecte séparément

Pour récupérer les deux sources sans remplacer le cache principal :

```bash
python3 unify_vie_offers.py --source all --output /tmp/vie_offers_test.json
```

`--source vie` et `--source wtj` permettent de choisir une seule source. Sans `--output`, le script écrit dans `vie_offers.json`. Sous Windows, remplacer le chemin `/tmp/...` par un chemin local adapté.

L’option historique `--api` démarre une API simplifiée du collecteur. Pour utiliser l’application complète décrite ici, lancer **`python3 api.py`**.

## Les routes de l’API

Une API est le point de communication entre le navigateur et le serveur. `GET` lit des informations ; `POST` demande une action.

| Méthode | Route | Fonction |
| --- | --- | --- |
| `GET` | `/` | Renvoie l’interface `index.html`. |
| `GET` | `/api/offers` | Renvoie les offres ; accepte `source`, `country`, `keyword` et `limit`. Limite par défaut : 100 ; l’interface demande 2 000 offres. |
| `GET` | `/api/stats` | Renvoie le nombre d’offres, les sources, la date d’export et les principaux pays. |
| `POST` | `/api/scrape` | Demande une collecte avec, par exemple, `{"source":"all","force":false}`. |
| `GET` | `/api/status` | Indique la progression, les résultats par source et les erreurs de la collecte. |

Pour `/api/scrape`, les sources autorisées sont `all`, `vie` et `wtj`. Si `force` est omis, il vaut `true`. La réponse est `200` si le cache est réutilisé, `202` si une collecte démarre et `409` si une collecte est déjà en cours.

Dans le statut, `by_source` contient `pending`, `running`, `error` ou le nombre d’annonces récupérées pour chaque source.

## Hébergement actuel et limites

Le `Procfile` contient :

```text
web: gunicorn api:app --workers 1 --threads 4 --bind 0.0.0.0:$PORT
```

Gunicorn est le programme qui exécute Flask en hébergement. Il démarre ici **un processus et quatre threads**. Le verrou et le statut de collecte vivent dans la mémoire de ce processus : cette configuration suppose une seule instance. Plusieurs processus ou instances nécessiteraient un stockage et une coordination partagés.

Le fichier d’annonces est local au serveur. Pour conserver ses mises à jour entre les redémarrages et redéploiements, il faut un stockage persistant. Le système de fichiers des services web gratuits Render est éphémère.

Sur l’offre gratuite de Render, un service web peut se mettre en veille après 15 minutes sans trafic et prendre environ une minute à redémarrer. Cela peut expliquer la page d’attente avant même l’affichage de VIE 2 GO. Le forfait réellement utilisé doit être vérifié dans le tableau de bord. [Documentation Render](https://render.com/docs/free)

## Évolution envisagée : autonomie sans Render

**Cette architecture n’est pas encore installée dans ce dépôt.** L’objectif serait de publier les annonces à intervalles réguliers, indépendamment des visites :

```text
Planification GitHub Actions
          │
          ▼
Collecte Python → Validation → Publication du site et du JSON
                                         │
                                         ▼
                                  Cloudflare Pages
                                         │
                                         ▼
                            Navigateur : lecture et filtres
```

- **Cloudflare Pages** hébergerait l’interface et les dernières annonces publiées, sans serveur Flask à réveiller pour les consulter.
- **GitHub Actions** exécuterait Python à une fréquence choisie, même ordinateur éteint et sans visite sur le site.
- **La publication automatique** conserverait les anciennes données des sources en échec et n’exposerait que des résultats validés.
- **Le navigateur** lirait directement le JSON ; le bouton d’actualisation rechargerait les dernières données publiées.

Il resterait à adapter l’interface, fiabiliser le mode de collecte autonome, créer la planification, configurer les secrets et automatiser la publication. Déplacer uniquement le HTML tout en gardant l’API sur Render ne supprimerait pas l’attente pour les annonces.

Les fichiers statiques de Cloudflare Pages bénéficient de requêtes gratuites et illimitées. Les exécutions standard de GitHub Actions sont gratuites dans les dépôts publics, sous réserve des conditions de ces services. La planification GitHub peut subir des retards et être désactivée après 60 jours sans activité dans un dépôt public : ce point doit être pris en compte dans le fonctionnement et la surveillance prévus.

Références : [Cloudflare Pages](https://developers.cloudflare.com/pages/functions/pricing/), [facturation GitHub Actions](https://docs.github.com/en/billing/concepts/product-billing/github-actions), [planification GitHub Actions](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).

## Vérifier le fonctionnement

Après installation des dépendances Python, et avec Node.js disponible pour les tests de l’interface :

```bash
python3 -m unittest discover -s tests -v
node --test tests/frontend.test.cjs
```

Les tests utilisent des fichiers temporaires et des sources simulées : ils ne lancent pas de collecte externe et ne modifient pas le cache du projet.

## Drapeaux

Les drapeaux SVG sont fournis par [FlagCDN / Flagpedia](https://flagcdn.com/),
un service gratuit, à partir des codes pays. Ils ne dépendent pas des polices
emoji installées sur Windows, macOS ou Linux. Si une image ne peut pas être
chargée, le nom du pays reste visible. Le filtre pays est trié alphabétiquement
selon la locale française.
