# VIE 2 GO 🌍

Le site de Business France pour chercher un VIE, c'est bien. Mais c'est pas pratique.

Alors j'ai bricolé un truc simple pour agréger les offres, les filtrer, et les afficher correctement. C'est minimaliste, ça se prend pas au sérieux, et si ça peut être utile à quelqu'un d'autre — c'est tout le but.

**👉 Tester en ligne : [https://vie-2-go.onrender.com/](https://vie-2-go.onrender.com/)**

---

## Ce que ça fait

- Scrape les offres de VIE depuis **Business France (CiViWeb)** et **Welcome to the Jungle**
- Déduplique les offres
- Les expose via une petite API REST avec filtres (pays, mot-clé, source)
- Sert un frontend directement depuis Flask

C'est tout. Pas de base de données, pas de compte à créer, pas de magie.

---

## Stack

Python / Flask + un peu de HTML/JS vanilla. Cache en JSON local. Déployé sur Railway.

---

## Lancer en local

```bash
git clone https://github.com/StanJub/VIE_2_GO.git
cd VIE_2_GO
pip install -r requirements.txt
python3 api.py
```

Créer un fichier `.env` avec :

```env
ALGOLIA_APP_ID=votre_app_id
ALGOLIA_API_KEY=votre_api_key
```

L'API tourne sur `http://localhost:5000`.

---

## API

| Méthode | Route | Description |
|---------|-------|-------------|
| `GET` | `/` | Frontend |
| `GET` | `/api/offers` | Offres filtrables (`?country=Japon&keyword=finance`) |
| `GET` | `/api/stats` | Stats rapides |
| `POST` | `/api/scrape` | Lance un scraping (`{ "source": "all/vie/wtj" }`) |
| `GET` | `/api/status` | Statut du scraping |

---

## Contribuer

Si tu tombes sur ce repo et que tu veux l'améliorer, go. C'est open source et les PR sont les bienvenues.

1. Fork → branche → PR
2. Ou ouvre juste une Issue si tu as une idée ou un bug

Quelques pistes si tu cherches par où commencer :
- Ajouter de nouvelles sources de scraping
- Améliorer les filtres ou l'interface
- Ajouter un scheduler pour automatiser le scraping
- Écrire des tests

---

MIT License
