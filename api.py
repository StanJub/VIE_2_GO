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
from datetime import datetime
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv
import gspread
from google.oauth2.service_account import Credentials
import base64

# Charge les variables d'environnement depuis .env
load_dotenv()

# Import du script de scraping existant
from unify_vie_offers import VIEUnifier

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
    "by_source": {},          # {"vie": 771, "wtj": 83} ou {"vie": "<erreur>"}
    "error": None             # message d'erreur si échec
}

# Lock pour éviter deux scrapings simultanés
scrape_lock = threading.Lock()

# ============================================================================
# FONCTION DE SCRAPING (tourne dans un thread séparé)
# ============================================================================

def run_scraping(source: str = "all"):
    """
    Lance le scraping en arrière-plan.
    Appelée dans un thread — ne doit jamais planter sans gérer l'erreur.
    """
    global scrape_status

    scrape_status["running"] = True
    scrape_status["started_at"] = datetime.now().isoformat()
    scrape_status["finished_at"] = None
    scrape_status["success"] = None
    scrape_status["error"] = None
    scrape_status["total_offers"] = 0
    scrape_status["by_source"] = {}

    try:
        unifier = VIEUnifier()
        errors = []

        # Chaque source est isolée : si l'une tombe, on récupère quand même
        # l'autre — mais on garde trace de l'échec au lieu de l'avaler.
        for name, fetch in (("vie", unifier.add_vie_offers),
                            ("wtj", unifier.add_wtj_offers)):
            if source not in (name, "all"):
                continue
            try:
                scrape_status["by_source"][name] = fetch()
            except Exception as e:
                scrape_status["by_source"][name] = f"échec : {e}"
                errors.append(f"{name} : {e}")

        unifier.deduplicate()

        # Garde-fou : ne pas remplacer un cache correct par un export dégradé.
        # Sans ça, une source qui tombe divise silencieusement le catalogue.
        previous, _ = load_offers()
        previous_total = len(previous["offers"]) if previous else 0
        new_total = len(unifier.offers)

        if errors and previous_total and new_total < previous_total / 2:
            scrape_status["total_offers"] = previous_total
            scrape_status["success"] = False
            scrape_status["error"] = (
                f"Scraping dégradé ({new_total} offres contre {previous_total} en cache) — "
                f"cache conservé. Détail : " + " | ".join(errors)
            )
            return

        unifier.export_json(OFFERS_FILE)
        scrape_status["total_offers"] = new_total
        scrape_status["success"] = not errors
        if errors:
            scrape_status["error"] = "Sources en échec : " + " | ".join(errors)

    except Exception as e:
        scrape_status["success"] = False
        scrape_status["error"] = str(e)

    finally:
        # Toujours exécuté, même en cas d'erreur
        scrape_status["running"] = False
        scrape_status["finished_at"] = datetime.now().isoformat()

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
            return json.load(f), None
    except json.JSONDecodeError:
        return None, "Le fichier cache est corrompu. Relancez un scraping."


# ============================================================================
# GOOGLE SHEETS — helper
# ============================================================================

def get_sheet():
    """
    Retourne l'onglet 'VIE' du Google Sheet configuré.
    Les credentials sont stockés en variable d'environnement (base64).
    """
    creds_b64 = os.environ.get("GOOGLE_CREDENTIALS_B64")
    spreadsheet_id = os.environ.get("GOOGLE_SHEET_ID")

    if not creds_b64 or not spreadsheet_id:
        raise ValueError("Variables GOOGLE_CREDENTIALS_B64 ou GOOGLE_SHEET_ID manquantes")

    # Décode le JSON des credentials depuis la variable d'env
    creds_json = base64.b64decode(creds_b64).decode("utf-8")
    creds_dict = json.loads(creds_json)

    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)

    spreadsheet = client.open_by_key(spreadsheet_id)
    return spreadsheet.worksheet("VIE")


# ============================================================================
# ENDPOINTS
# ============================================================================

# Endpoint pour ajouter une annonce dans Google Sheets

@app.route("/api/add-to-sheet", methods=["POST"])
def add_to_sheet():
    """
    Ajoute une annonce dans l'onglet 'VIE' du Google Sheet.

    Body JSON attendu :
        {
            "title":            "Chef de projet digital",
            "company":          "Air Liquide",
            "country":          "Japon",
            "city":             "Tokyo",
            "duration_months":  12
        }

    Colonnes écrites : Poste | Entreprise | Lieu | Durée
    """
    body = request.get_json(silent=True)
    if not body:
        return jsonify({"error": "Body JSON manquant"}), 400

    # Construit la ligne : Poste | Entreprise | Lieu | Durée
    lieu = body.get("city") or body.get("country") or "—"
    duree = body.get("duration_months")
    duree_str = f"{duree} mois" if duree else "—"

    row = [
        body.get("title", "—"),
        body.get("company", "—"),
        lieu,
        duree_str,
    ]

    try:
        sheet = get_sheet()
        sheet.append_row(row, value_input_option="USER_ENTERED")
        return jsonify({"success": True, "row": row}), 200

    except ValueError as e:
        return jsonify({"error": str(e)}), 500
    except Exception as e:
        return jsonify({"error": f"Erreur Google Sheets : {str(e)}"}), 500
    
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

    offers = offers[:limit]

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
    # Empêche deux scrapings simultanés
    if scrape_status["running"]:
        return jsonify({
            "error": "Un scraping est déjà en cours.",
            "started_at": scrape_status["started_at"]
        }), 409  # 409 Conflict

    # Récupère la source depuis le body JSON (optionnel)
    body = request.get_json(silent=True) or {}
    source = body.get("source", "all")

    if source not in ("all", "vie", "wtj"):
        return jsonify({
            "error": "Valeur 'source' invalide. Choisissez : 'all', 'vie', ou 'wtj'."
        }), 400

    # Lance le scraping dans un thread séparé
    thread = threading.Thread(
        target=run_scraping,
        args=(source,),
        daemon=True   # le thread s'arrête si l'API s'arrête
    )
    thread.start()

    return jsonify({
        "message": f"Scraping lancé (source: {source}). Suivez l'avancement sur GET /api/status",
        "source": source
    }), 202  # 202 Accepted = "reçu, traitement en cours"


@app.route("/api/status", methods=["GET"])
def get_status():
    """
    Retourne le statut du scraping en cours ou du dernier scraping terminé.
    """
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