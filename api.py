#!/usr/bin/env python3
"""
API Flask pour les annonces VIE
Phase 2 — avec scraping asynchrone, cache, statut et frontend intégré

Structure du projet :
    ├── .env
    ├── .gitignore
    ├── api.py                  ← ce fichier
    ├── index.html              ← frontend servi par Flask
    ├── unify_vie_offers.py     ← script de scraping
    ├── requirements.txt
    ├── Procfile
    └── vie_offers.json         ← généré automatiquement

Utilisation locale :
    pip install -r requirements.txt
    python3 api.py

Déploiement Railway :
    - Pusher sur GitHub
    - Connecter le repo sur railway.app
    - Ajouter les variables ALGOLIA_API_KEY et ALGOLIA_APP_ID
"""

import json
import os
import threading
from datetime import datetime, timezone
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv

# Charge les variables d'environnement depuis .env
load_dotenv()

# Import du script de scraping existant
from unify_vie_offers import VIEUnifier, OfferSource, atomic_write_json

# ============================================================================
# CONFIGURATION
# ============================================================================

OFFERS_FILE = "vie_offers.json"   # fichier cache JSON

app = Flask(__name__)
CORS(app)

# ============================================================================
# ÉTAT DU SCRAPING (partagé entre le thread et l'API)
# ============================================================================

# Ce dictionnaire est lu par /api/status pour informer le frontend
scrape_status = {
    "running": False,         # est-ce qu'un scraping tourne en ce moment ?
    "started_at": None,       # quand il a démarré
    "finished_at": None,      # quand il s'est terminé
    "success": None,          # True / False / None (= jamais lancé)
    "total_offers": 0,        # nombre d'offres récupérées
    "by_source": {},          # Nombre d’annonces, ou pending / running / error
    "error": None             # message d'erreur si échec
}

# Lock pour éviter deux scrapings simultanés
scrape_lock = threading.Lock()

# ============================================================================
# FONCTION DE SCRAPING (tourne dans un thread séparé)
# ============================================================================

def unique_offers(offers):
    seen = set()
    result = []
    for offer in offers:
        key = tuple(str(offer.get(field, "")).strip().lower() for field in ("title", "company", "country"))
        if key not in seen:
            seen.add(key)
            result.append(offer)
    return result


def run_scraping(source: str = "all"):
    """
    Lance le scraping en arrière-plan.
    Appelée dans un thread — ne doit jamais planter sans gérer l'erreur.
    """
    try:
        previous, _ = load_offers()
        previous = previous or {"offers": [], "metadata": {}}
        offers = previous["offers"][:]
        refreshed = dict(previous["metadata"].get("source_refreshed_at", {}))
        errors = []
        completed = []
        for name, source_value, method in (
            ("vie", OfferSource.VIE.value, "add_vie_offers"),
            ("wtj", OfferSource.WTJ.value, "add_wtj_offers"),
        ):
            if source not in (name, "all"):
                continue
            with scrape_lock:
                scrape_status["by_source"][name] = "running"
            try:
                # Isolate partial results: a failing source never enters the cache.
                unifier = VIEUnifier()
                getattr(unifier, method)()
                unifier.deduplicate()
                fresh = [o.to_dict() for o in unifier.offers]
                offers = [o for o in offers if o.get("source") != source_value] + fresh
                refreshed[name] = datetime.now(timezone.utc).isoformat()
                completed.append(name)
                with scrape_lock:
                    scrape_status["by_source"][name] = len(fresh)
            except Exception as e:
                errors.append(f"{name} : {e}")
                with scrape_lock:
                    scrape_status["by_source"][name] = "error"

        if completed:
            metadata = dict(previous["metadata"])
            metadata.update(total_offers=len(unique_offers(offers)),
                            exported_at=datetime.now(timezone.utc).isoformat(),
                            sources=sorted({o["source"] for o in offers}),
                            source_refreshed_at=refreshed)
            atomic_write_json(OFFERS_FILE, {"metadata": metadata, "offers": offers})
        with scrape_lock:
            scrape_status["total_offers"] = len(unique_offers(offers))
            scrape_status["success"] = not errors
            scrape_status["error"] = ("Sources non actualisées (cache conservé) : " + " | ".join(errors)) if errors else None
    except Exception as e:
        with scrape_lock:
            scrape_status["success"] = False
            scrape_status["error"] = str(e)
    finally:
        with scrape_lock:
            scrape_status["running"] = False
            scrape_status["finished_at"] = datetime.now(timezone.utc).isoformat()

# ============================================================================
# HELPER — chargement du cache JSON
# ============================================================================

def load_offers():
    """
    Lit le fichier cache JSON.
    Retourne (data, error) : data est un dict, error est une string ou None.
    """
    if not os.path.exists(OFFERS_FILE):
        return None, "Aucune donnée disponible. Lancez d'abord un scraping via POST /api/scrape"

    try:
        with open(OFFERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict) or not isinstance(data.get("offers"), list) or not isinstance(data.get("metadata"), dict):
                return None, "Format du cache invalide."
            return data, None
    except (OSError, json.JSONDecodeError):
        return None, "Le fichier cache est corrompu. Relancez un scraping."


# Endpoint pour servir le frontend (index.html)

@app.route("/", methods=["GET"])
def serve_frontend():
    """Sert la page HTML du frontend"""
    return send_from_directory(".", "index.html")


@app.route("/api/offers", methods=["GET"])
def get_offers():
    """
    Retourne les annonces avec filtres optionnels.

    Paramètres :
        source  — filtre par source  (ex: ?source=VIE)
        country — filtre par pays    (ex: ?country=Japon)
        keyword — filtre par mot-clé dans le titre ou la description
        limit   — nombre max d'annonces retournées (défaut: 100)
    """
    data, error = load_offers()

    if error:
        return jsonify({"error": error}), 404

    offers = data["offers"]

    # Filtres
    source  = request.args.get("source")
    country = request.args.get("country")
    keyword = request.args.get("keyword")
    limit   = request.args.get("limit", default=100, type=int)

    if source:
        offers = [o for o in offers if source.lower() in o["source"].lower()]

    if country:
        offers = [o for o in offers if country.lower() in o["country"].lower()]

    if keyword:
        kw = keyword.lower()
        offers = [
            o for o in offers
            if kw in o["title"].lower()
            or kw in (o.get("description") or "").lower()
        ]

    offers = unique_offers(offers)[:limit]

    return jsonify({
        "count": len(offers),
        "filters_applied": {
            "source": source,
            "country": country,
            "keyword": keyword,
            "limit": limit
        },
        "offers": offers
    })


@app.route("/api/stats", methods=["GET"])
def get_stats():
    """
    Retourne des statistiques sur les données en cache.
    """
    data, error = load_offers()

    if error:
        return jsonify({"error": error}), 404

    offers = data["offers"]

    offers = unique_offers(offers)

    # Compte par pays
    by_country = {}
    for o in offers:
        c = o.get("country", "Inconnu")
        by_country[c] = by_country.get(c, 0) + 1

    # Top 10 pays
    top_countries = sorted(by_country.items(), key=lambda x: x[1], reverse=True)[:10]

    return jsonify({
        "total_offers": len(offers),
        "exported_at": data["metadata"].get("exported_at"),
        "sources": data["metadata"].get("sources", []),
        "top_countries": dict(top_countries)
    })


@app.route("/api/scrape", methods=["POST"])
def start_scrape():
    """
    Lance un scraping en arrière-plan.

    Body JSON optionnel :
        { "source": "all" }   ← "all" (défaut), "vie", ou "wtj"

    Retourne immédiatement — le scraping tourne en fond.
    Interrogez GET /api/status pour suivre l'avancement.
    """
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify(error="Body JSON invalide"), 400
    source = body.get("source", "all")
    force = body.get("force", True)  # Existing manual API clients still force refresh.
    if source not in ("all", "vie", "wtj") or not isinstance(force, bool):
        return jsonify(error="Source ou paramètre force invalide"), 400

    with scrape_lock:
        if scrape_status["running"]:
            return jsonify(message="Une recherche est déjà en cours"), 409
        if not force and cache_is_fresh(source):
            return jsonify(cached=True, message="Les annonces sont à jour"), 200
        # Reserve before starting the thread: no second request can slip through.
        scrape_status.update(running=True, started_at=datetime.now(timezone.utc).isoformat(),
                             finished_at=None, success=None, total_offers=0, error=None,
                             by_source={name: "pending" for name in ("vie", "wtj") if source in ("all", name)})
        try:
            threading.Thread(target=run_scraping, args=(source,), daemon=True).start()
        except Exception:
            scrape_status.update(running=False, success=False, error="Impossible de lancer la recherche")
            return jsonify(error=scrape_status["error"]), 503
    return jsonify(cached=False, source=source), 202


def cache_is_fresh(source, max_age=15 * 60):
    data, _ = load_offers()
    if not data:
        return False
    # Legacy caches are refreshed once to establish per-source freshness.
    stamps = data["metadata"].get("source_refreshed_at", {})
    now = datetime.now(timezone.utc)
    for name in ("vie", "wtj"):
        if source not in ("all", name):
            continue
        try:
            stamp = datetime.fromisoformat(stamps[name])
            age = (now - stamp).total_seconds()
            if not 0 <= age < max_age:
                return False
        except (KeyError, TypeError, ValueError):
            return False
    return True


@app.route("/api/status", methods=["GET"])
def get_status():
    """
    Retourne le statut du scraping en cours ou du dernier scraping terminé.
    """
    with scrape_lock:
        return jsonify(scrape_status)


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("🚀  VIE Offers API — Phase 2")
    print("=" * 60)
    print(f"\n📍 http://localhost:5000")
    print(f"\n📚 Endpoints :")
    print(f"   GET  /api/offers?country=Japon&keyword=finance")
    print(f"   GET  /api/stats")
    print(f"   POST /api/scrape        body: {{\"source\": \"all\"}}")
    print(f"   GET  /api/status")
    print(f"\n⚠️  Ctrl+C pour arrêter")
    print("=" * 60 + "\n")

    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)