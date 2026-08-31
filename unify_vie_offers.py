#!/usr/bin/env python3
"""
Script d'unification des annonces VIE
Combine les données de Business France (Azure) et Welcome to the Jungle (Algolia)
Exporte en JSON et peut servir via une API Flask

Utilisation:
    python3 unify_vie_offers.py                    # Scrape et exporte en JSON
    python3 unify_vie_offers.py --api              # Lance l'API Flask (http://localhost:5000)
    python3 unify_vie_offers.py --source vie       # Scrape uniquement Business France
    python3 unify_vie_offers.py --source wtj       # Scrape uniquement Welcome to the Jungle
    python3 unify_vie_offers.py --output mes_offres.json  # Nom de fichier personnalisé
"""

import requests
import json
import re
import argparse                          # ✅ CORRECTION 1 : remplace sys.argv
import os                                # ✅ CORRECTION 1 : pour lire les variables d'env
from datetime import datetime
from typing import List, Dict, Optional
from dataclasses import dataclass, asdict
from enum import Enum

# ✅ CORRECTION 1 : Chargement des variables d'environnement depuis .env
try:
    from dotenv import load_dotenv
    load_dotenv()
    print("✓ Fichier .env chargé")
except ImportError:
    print("⚠️  python-dotenv non installé — installez-le avec: pip install python-dotenv")
    print("   Les variables d'environnement système seront utilisées si disponibles.")

# ============================================================================
# MODÈLE DE DONNÉES UNIFIÉ
# ============================================================================

class ScrapingError(RuntimeError):
    """
    Erreur irrécupérable sur une source.
    Levée plutôt qu'avalée : sans ça, une source qui tombe produit
    silencieusement 0 offre et le cache est écrasé par un export dégradé.
    """


class OfferSource(Enum):
    """Sources possibles pour une annonce"""
    VIE = "Business France (VIE)"
    WTJ = "Welcome to the Jungle"

@dataclass
class UnifiedOffer:
    """Format unifié pour toutes les annonces VIE"""
    
    # Infos de base
    title: str
    company: str
    source: OfferSource                  # ✅ CORRECTION 2 : utilise l'Enum au lieu de str
    
    # Localisation
    city: str
    country: str
    
    # Détails mission
    duration_months: Optional[str]       # "12", "12-24", etc.
    start_date: Optional[str]
    description: Optional[str]
    
    # Rémunération
    salary: Optional[str]               # "€X - €Y/an" ou "€X/mois" etc.
    
    # Contact
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    
    # Secteurs
    sectors: Optional[str] = None
    
    # Lien et référence
    link: str = ""
    reference: str = ""
    
    # Métadonnées
    scraped_at: str = ""
    
    def to_dict(self) -> Dict:
        """Convertit l'offre en dictionnaire (l'Enum est sérialisé en string)"""
        d = asdict(self)
        d['source'] = self.source.value  # ✅ CORRECTION 2 : sérialise l'Enum proprement
        return d


# ============================================================================
# NORMALISATION DES SECTEURS
# ============================================================================

# Secteurs normalisés avec leurs mots-clés de détection (ordre = priorité)
SECTOR_KEYWORDS = [
    ("Informatique & Tech",      ["tech lead", "développeur", "developer", "software", "data",
                                   "cloud", "devops", "cybersécurité", "cyber", "intelligence artificielle",
                                   "machine learning", "systèmes", "réseau", "informatique", "digital",
                                   "numérique", "erp", "sap", "web", "mobile", "fullstack",
                                   "backend", "frontend", "api", "infrastructure", "it "]),
    ("Finance & Banque",         ["finance", "financier", "comptable", "comptabilité", "audit",
                                   "contrôle de gestion", "trésorerie", "banque", "banking", "risk",
                                   "crédit", "fiscal", "consolidation", "reporting financier"]),
    ("Ingénierie & Industrie",   ["ingénieur", "technicien", "mécanique", "électronique", "électrique",
                                   "automatisme", "production", "industriel", "manufacturing", "usine",
                                   "maintenance", "qualité", "lean", "méthodes", "procédés",
                                   "bureau d'études", "conception"]),
    ("Commerce & Vente",         ["commercial", "vente", "sales", "business development", "account",
                                   "customer", "négociation", "grands comptes", "b2b", "b2c",
                                   "export", "prescription", "avant-vente"]),
    ("Marketing & Communication",["marketing", "communication", "brand", "marque", "digital marketing",
                                   "seo", "sea", "réseaux sociaux", "contenu", "rédaction", "publicité",
                                   "événementiel", "relations presse", "média", "influence"]),
    ("Supply Chain & Logistique",["supply chain", "logistique", "achats", "procurement", "sourcing",
                                   "approvisionn", "stock", "entrepôt", "transport", "douane",
                                   "planification", "demand"]),
    ("Ressources Humaines",      ["ressources humaines", " rh ", "recrutement", "talent", "formation",
                                   "paie", "relations sociales", "compensation", "human resources"]),
    ("Juridique & Compliance",   ["juridique", "droit", "legal", "compliance", "conformité", "contrats",
                                   "propriété intellectuelle", "rgpd", "réglementaire"]),
    ("Conseil & Stratégie",      ["conseil", "consulting", "stratégie", "transformation",
                                   "gestion de projet", "project manager", "chef de projet", "pmo"]),
    ("Énergie & Environnement",  ["énergie", "energy", "environnement", "développement durable", "rse",
                                   "renouvelable", "solaire", "éolien", "nucléaire", "pétrole", "oil",
                                   "gas", "mining", "décarbonation", "esg", "eau"]),
    ("Santé & Pharma",           ["santé", "médical", "pharma", "pharmaceutique", "clinique", "biologie",
                                   "biotechnologie", "dispositif médical", "health"]),
    ("Aéronautique & Défense",   ["aéronautique", "aérospatial", "défense", "aviation", "aerospace",
                                   "drone", "spatial", "satellite", "naval"]),
    ("BTP & Immobilier",         ["btp", "bâtiment", "construction", "génie civil", "immobilier",
                                   "architecture", "chantier", "travaux", "infrastructure"]),
    ("Agroalimentaire",          ["agroalimentaire", "agriculture", "alimentaire", "food",
                                   "agronomie", "viticulture", "élevage"]),
    ("Luxe & Mode",              ["luxe", "mode", "fashion", "cosmétique", "parfum", "joaillerie",
                                   "horlogerie", "lifestyle"]),
    ("Tourisme & Hôtellerie",    ["tourisme", "hôtellerie", "hôtel", "restaurant", "hospitality",
                                   "voyage", "croisière"]),
    ("Éducation & Recherche",    ["éducation", "formation", "enseignement", "recherche", "académique",
                                   "université", "laboratoire"]),
]

def infer_sector(title: str, description: str) -> Optional[str]:
    """Infère le secteur d'une offre BF depuis son titre et sa description."""
    text = f"{title} {description or ''}".lower()
    for sector_name, keywords in SECTOR_KEYWORDS:
        if any(kw in text for kw in keywords):
            return sector_name
    return None

def normalize_wtj_sector(sectors_str: str) -> Optional[str]:
    """Normalise les secteurs WTJ vers les mêmes labels que BF."""
    if not sectors_str:
        return None
    text = sectors_str.lower()
    for sector_name, keywords in SECTOR_KEYWORDS:
        if any(kw in text for kw in keywords):
            return sector_name
    # Fallback : premier segment nettoyé
    first = sectors_str.split(">")[0].split("/")[0].strip()
    return first if first else None

# ============================================================================
# SCRAPER VIE (Business France)
# ============================================================================

class VIEScraper:
    """Scrape l'API Azure de Business France"""
    
    API_BASE_URL = "https://civiweb-api-prd.azurewebsites.net/api/Offers/search"
    SITE_BASE_URL = "https://mon-vie-via.businessfrance.fr"
    # Page publique dont la config Nuxt embarque la clé d'API du site
    CONFIG_PAGE_URL = f"{SITE_BASE_URL}/offres/recherche"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        self.refresh_api_key()

    def refresh_api_key(self) -> Optional[str]:
        """
        Business France a fermé son API derrière un header X-API-KEY : sans lui,
        tous les endpoints répondent 401 (WWW-Authenticate: Bearer).

        La clé est celle que le site public envoie depuis le navigateur de chaque
        visiteur — elle est exposée dans la config Nuxt de la page de recherche
        (window.__NUXT__.config.API_KEY). On la relit à chaque scraping pour
        survivre à ses rotations.

        CIVIWEB_API_KEY (variable d'env) permet de figer une valeur si besoin.
        """
        key = os.getenv('CIVIWEB_API_KEY')

        if not key:
            try:
                response = requests.get(
                    self.CONFIG_PAGE_URL,
                    timeout=15,
                    headers={'User-Agent': self.session.headers['User-Agent']}
                )
                response.raise_for_status()
                # La clé est une chaîne JSON échappée dans le payload Nuxt
                match = re.search(r'API_KEY\s*:\s*("(?:[^"\\]|\\.)*")', response.text)
                key = json.loads(match.group(1)) if match else None
            except Exception as e:
                print(f"  ✗ Impossible de lire la clé d'API sur {self.CONFIG_PAGE_URL}: {e}")
                key = None

        if not key:
            return None

        self.session.headers['X-API-KEY'] = key
        print("  ✓ Clé d'API Business France récupérée")
        return key

    def search_offers(self, skip=0, limit=100):
        """
        Effectue une recherche d'annonces via l'API Azure.
        Lève ScrapingError si l'API est inutilisable — l'appelant doit le savoir.
        """
        payload = {
            "limit": limit,
            "skip": skip,
            "query": None,
            "specializationsIds": [],
            "teletravail": ["0"],
            "porteEnv": ["0"],
            "activitySectorId": [],
            "missionsTypesIds": [],
            "missionsDurations": [],
            "geographicZones": [],
            "countriesIds": [],
            "studiesLevelId": [],
            "companiesSizes": [],
            "entreprisesIds": [0],
            "missionStartDate": None
        }

        for attempt in (1, 2):
            try:
                response = self.session.post(self.API_BASE_URL, json=payload, timeout=20)
            except requests.RequestException as e:
                raise ScrapingError(f"API Business France injoignable : {e}") from e

            # 401 = clé absente, expirée ou tournée → on la relit et on retente une fois
            if response.status_code == 401 and attempt == 1:
                print("  ⚠️  401 — la clé d'API a peut-être tourné, nouvelle tentative")
                if self.refresh_api_key():
                    continue
                raise ScrapingError(
                    "API Business France : 401 et clé X-API-KEY introuvable "
                    "(le format de la page de recherche a peut-être changé)"
                )

            if not response.ok:
                raise ScrapingError(
                    f"API Business France : HTTP {response.status_code}"
                )

            try:
                return response.json()
            except ValueError as e:
                raise ScrapingError(f"Réponse non-JSON de l'API Business France : {e}") from e


    def get_all_offers(self) -> List[Dict]:
        """Récupère TOUTES les annonces VIE avec pagination"""
        all_offers = []
        skip = 0
        limit = 100
        
        print("\n📄 Scraping Business France (VIE)...")
        
        while True:
            result = self.search_offers(skip=skip, limit=limit)

            offers = result.get('result', [])
            if not offers:
                # Zéro offre dès la 1re page = anomalie (l'API en publie des centaines)
                if skip == 0:
                    raise ScrapingError(
                        "API Business France : 0 offre renvoyée — "
                        "le format de la réponse a probablement changé"
                    )
                break

            all_offers.extend(offers)
            print(f"  ✓ {len(offers)} annonces (skip={skip}, total={len(all_offers)})")
            
            total_count = result.get('count', 0)
            if len(all_offers) >= total_count or len(offers) < limit:
                break
            
            skip += limit
        
        print(f"  ✓ Total: {len(all_offers)} annonces VIE récupérées")
        return all_offers
    
    def parse_offer(self, offer: Dict) -> Optional[UnifiedOffer]:
        """Parse une annonce VIE brute en format unifié"""
        try:
            title = offer.get('missionTitle', '').strip()
            company = offer.get('organizationName', '').strip()
            city = offer.get('cityName', '').strip()
            country = offer.get('countryName', '').strip()
            
            # Description
            description = offer.get('missionDescription', '')
            description = self._clean_html(description)
            description = description[:500] if description else None  # ✅ CORRECTION 3 : cohérence avec WTJ

            # Dates
            start_date = self._extract_date(offer.get('missionStartDate', ''))
            
            # Durée
            duration = offer.get('missionDuration', '')
            
            # Indemnité
            indemnite = offer.get('indemnite', '')
            salary = f"€{indemnite:.0f}/mois" if indemnite else None
            
            # Contact
            contact_name = offer.get('contactName', '').strip()
            contact_email = offer.get('contactEmail', '').strip()
            
            # Secteurs — d'abord depuis l'API, sinon inféré depuis titre+description
            sectors = []
            if offer.get('activitySectorN1'):
                sectors.append(offer.get('activitySectorN1'))
            if offer.get('activitySectorN2'):
                sectors.append(offer.get('activitySectorN2'))
            if sectors:
                sectors_str = " > ".join(sectors)
                # Normalise vers les labels unifiés
                sectors_str = normalize_wtj_sector(sectors_str) or sectors_str
            else:
                # Business France renvoie souvent sectors=null → on infère
                sectors_str = infer_sector(title, description or '')
            
            # Lien
            offer_id = offer.get('id', '')
            reference = offer.get('reference', f'VIE{offer_id}')
            link = f"{self.SITE_BASE_URL}/offres/{offer_id}" if offer_id else ""
            
            return UnifiedOffer(
                title=title,
                company=company,
                source=OfferSource.VIE,  # ✅ CORRECTION 2 : utilise l'Enum
                city=city,
                country=country,
                duration_months=str(duration) if duration else None,
                start_date=start_date,
                description=description,
                salary=salary,
                contact_name=contact_name,
                contact_email=contact_email,
                sectors=sectors_str,
                link=link,
                reference=reference,
                scraped_at=datetime.now().isoformat()
            )
        except Exception as e:
            print(f"  ✗ Erreur parsing: {e}")
            return None
    
    @staticmethod
    def _clean_html(text: str) -> str:
        """Supprime les balises HTML"""
        if not text:
            return ""
        text = re.sub(r'<[^>]+>', '', str(text))
        text = re.sub(r'[\r\n]+', ' ', text)
        text = ' '.join(text.split())
        return text.strip()
    
    @staticmethod
    def _extract_date(date_str: str) -> Optional[str]:
        """Extrait une date au format YYYY-MM-DD"""
        if not date_str:
            return None
        try:
            if 'T' in date_str:
                return date_str.split('T')[0]
            return date_str[:10]
        except (ValueError, IndexError, AttributeError):  # ✅ CORRECTION 4 : except précis
            return None

# ============================================================================
# SCRAPER WTJ (Welcome to the Jungle)
# ============================================================================

class WTJScraper:
    """Scrape l'API Algolia de Welcome to the Jungle"""
    
    WTJ_API_URL = "https://csekhvms53-dsn.algolia.net/1/indexes/*/queries"
    
    def __init__(self):
        # ✅ CORRECTION 1 : clés lues depuis les variables d'environnement
        algolia_api_key = os.getenv('ALGOLIA_API_KEY')
        algolia_app_id = os.getenv('ALGOLIA_APP_ID')

        if not algolia_api_key or not algolia_app_id:
            raise EnvironmentError(
                "Variables ALGOLIA_API_KEY et ALGOLIA_APP_ID manquantes.\n"
                "Créez un fichier .env avec ces valeurs (voir README)."
            )

        self.session = requests.Session()
        self.session.headers.update({
            'Accept': '*/*',
            'Content-Type': 'application/x-www-form-urlencoded',
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
            'x-algolia-api-key': algolia_api_key,    # ✅ CORRECTION 1
            'x-algolia-application-id': algolia_app_id,  # ✅ CORRECTION 1
            'Origin': 'https://www.welcometothejungle.com',
            'Referer': 'https://www.welcometothejungle.com/'
        })
    
    def search_offers(self, page=0, hits_per_page=100):  # ✅ hits_per_page monté à 100
        """
        Effectue une recherche via Algolia.
        Lève ScrapingError si l'API est inutilisable — l'appelant doit le savoir.
        """
        payload = {
            "requests": [
                {
                    "indexName": "wttj_jobs_production_fr",
                    "params": (
                        f"attributesToHighlight=%5B%22name%22%5D&"
                        f"attributesToRetrieve=%5B%22*%22%2C%22-has_benefits%22%2C%22-has_contract_duration%22%2C%22-has_education_level%22%2C%22-has_experience_level_minimum%22%2C%22-has_remote%22%2C%22-has_salary_yearly_minimum%22%2C%22-new_profession%22%2C%22-organization.description%22%2C%22-organization_score%22%2C%22-profile%22%2C%22-rank_group_1%22%2C%22-rank_group_2%22%2C%22-rank_group_3%22%2C%22-source_stage%22%5D&"
                        f"clickAnalytics=true&"
                        f"hitsPerPage={hits_per_page}&"
                        f"maxValuesPerFacet=999&"
                        f"responseFields=%5B%22facets%22%2C%22hits%22%2C%22hitsPerPage%22%2C%22nbHits%22%2C%22nbPages%22%2C%22page%22%2C%22params%22%2C%22query%22%5D&"
                        f"analytics=true&"
                        f"enableABTest=true&"
                        f"facets=%5B%22*%22%5D&"
                        f"filters=(%22contract_type%22%3A%22vie%22)&"
                        f"page={page}&"
                        f"query="
                    )
                }
            ]
        }

        try:
            response = self.session.post(self.WTJ_API_URL, json=payload, timeout=20)
        except requests.RequestException as e:
            raise ScrapingError(f"API Welcome to the Jungle injoignable : {e}") from e

        if response.status_code in (401, 403):
            raise ScrapingError(
                f"API Welcome to the Jungle : HTTP {response.status_code} — "
                "les clés ALGOLIA_APP_ID / ALGOLIA_API_KEY sont probablement périmées"
            )
        if not response.ok:
            raise ScrapingError(f"API Welcome to the Jungle : HTTP {response.status_code}")

        try:
            return response.json()
        except ValueError as e:
            raise ScrapingError(f"Réponse non-JSON de Welcome to the Jungle : {e}") from e
    
    def get_all_offers(self) -> List[Dict]:
        """Récupère TOUTES les annonces WTJ avec pagination"""
        all_offers = []
        page = 0
        hits_per_page = 100
        
        print("\n📄 Scraping Welcome to the Jungle...")
        
        while True:
            result = self.search_offers(page=page, hits_per_page=hits_per_page)

            results = result.get('results', [])
            hits = results[0].get('hits', []) if results else []
            if not hits:
                # Zéro offre dès la 1re page = anomalie (filtre ou index modifié)
                if page == 0:
                    raise ScrapingError(
                        "API Welcome to the Jungle : 0 offre renvoyée — "
                        "l'index ou le filtre contract_type=vie a probablement changé"
                    )
                break

            all_offers.extend(hits)
            print(f"  ✓ {len(hits)} annonces (page={page}, total={len(all_offers)})")
            
            nb_pages = results[0].get('nbPages', 1)
            if page >= nb_pages - 1:
                break
            
            page += 1
        
        print(f"  ✓ Total: {len(all_offers)} annonces WTJ récupérées")
        return all_offers
    
    def parse_offer(self, offer: Dict) -> Optional[UnifiedOffer]:
        """Parse une annonce WTJ brute en format unifié"""
        try:
            title = offer.get('name', '').strip()
            company = offer.get('organization', {}).get('name', '').strip()
            
            # Localisation
            offices = offer.get('offices', [])
            city = offices[0].get('city', '') if offices else ""
            country = offices[0].get('country', '') if offices else ""
            
            # Salaire
            salary_min = offer.get('salary_yearly_minimum', '')
            salary_max = offer.get('salary_yearly_maximum', '')
            
            if salary_min and salary_max:
                salary = f"€{int(salary_min/1000)}k - €{int(salary_max/1000)}k/an"
            elif salary_min:
                salary = f"€{int(salary_min/1000)}k/an"
            else:
                salary = None
            
            # Durée
            duration_min = offer.get('contract_duration_minimum', '')
            duration_max = offer.get('contract_duration_maximum', '')
            
            if duration_min and duration_max:
                duration = f"{duration_min}-{duration_max}"
            elif duration_min:
                duration = str(duration_min)
            else:
                duration = None
            
            # Description (missions principales)  ✅ CORRECTION 3 : uniformisée avec VIE
            key_missions = offer.get('key_missions', [])
            description = " | ".join(key_missions[:3]) if key_missions else None
            description = description[:500] if description else None

            # Secteurs — normalisés vers les labels unifiés
            sectors = offer.get('sectors', [])
            sectors_list = [s.get('name', '') for s in sectors if s.get('name')]
            raw_sectors = " > ".join(sectors_list) if sectors_list else None
            sectors_str = normalize_wtj_sector(raw_sectors) or infer_sector(title, description or '')
            
            # Lien
            job_slug = offer.get('slug', '')
            company_slug = offer.get('organization', {}).get('slug', '')
            if job_slug and company_slug:
                link = f"https://www.welcometothejungle.com/fr/companies/{company_slug}/jobs/{job_slug}"
            elif job_slug:
                link = f"https://www.welcometothejungle.com/fr/jobs/{job_slug}"
            else:
                link = ""
            
            return UnifiedOffer(
                title=title,
                company=company,
                source=OfferSource.WTJ,  # ✅ CORRECTION 2 : utilise l'Enum
                city=city,
                country=country,
                duration_months=duration,
                start_date=None,
                description=description,
                salary=salary,
                contact_name=None,
                contact_email=None,
                sectors=sectors_str,
                link=link,
                reference=offer.get('wk_reference', ''),
                scraped_at=datetime.now().isoformat()
            )
        except Exception as e:
            print(f"  ✗ Erreur parsing: {e}")
            return None

# ============================================================================
# UNIFICATEUR
# ============================================================================

class VIEUnifier:
    """Unifie les données de plusieurs sources"""
    
    def __init__(self):
        self.offers: List[UnifiedOffer] = []
    
    def add_vie_offers(self) -> int:
        """Ajoute les offres de Business France"""
        scraper = VIEScraper()
        raw_offers = scraper.get_all_offers()
        
        count = 0
        for offer in raw_offers:
            parsed = scraper.parse_offer(offer)
            if parsed:
                self.offers.append(parsed)
                count += 1
        
        print(f"  ✓ {count} annonces VIE parsées et ajoutées")
        return count
    
    def add_wtj_offers(self) -> int:
        """Ajoute les offres de Welcome to the Jungle"""
        scraper = WTJScraper()
        raw_offers = scraper.get_all_offers()
        
        count = 0
        for offer in raw_offers:
            parsed = scraper.parse_offer(offer)
            if parsed:
                self.offers.append(parsed)
                count += 1
        
        print(f"  ✓ {count} annonces WTJ parsées et ajoutées")
        return count
    
    def deduplicate(self) -> int:
        """
        ✅ CORRECTION 4 : Supprime les doublons inter-sources.
        Une offre est considérée comme un doublon si elle a le même titre,
        la même entreprise et le même pays.
        """
        seen = set()
        unique_offers = []
        
        for offer in self.offers:
            key = (
                offer.title.lower().strip(),
                offer.company.lower().strip(),
                offer.country.lower().strip()
            )
            if key not in seen:
                seen.add(key)
                unique_offers.append(offer)
        
        removed = len(self.offers) - len(unique_offers)
        self.offers = unique_offers
        
        if removed > 0:
            print(f"  ✓ {removed} doublon(s) supprimé(s)")
        
        return removed

    def export_json(self, filename: str = "vie_offers.json") -> str:
        """Exporte les annonces en JSON"""
        data = {
            "metadata": {
                "total_offers": len(self.offers),
                "exported_at": datetime.now().isoformat(),
                "sources": list(set(o.source.value for o in self.offers))  # ✅ CORRECTION 2
            },
            "offers": [o.to_dict() for o in self.offers]
        }
        
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        print(f"\n✓ Données exportées dans {filename}")
        return filename
    
    def get_stats(self) -> Dict:
        """Retourne des statistiques sur les données"""
        sources_count = {}
        countries_count = {}
        
        for offer in self.offers:
            sources_count[offer.source.value] = sources_count.get(offer.source.value, 0) + 1  # ✅ CORRECTION 2
            countries_count[offer.country] = countries_count.get(offer.country, 0) + 1
        
        return {
            "total": len(self.offers),
            "by_source": sources_count,
            "by_country": countries_count,
            "exported_at": datetime.now().isoformat()
        }
    
    def print_stats(self):
        """Affiche les statistiques"""
        stats = self.get_stats()
        
        print("\n" + "=" * 70)
        print("📊 STATISTIQUES")
        print("=" * 70)
        print(f"\n📈 Total d'annonces: {stats['total']}")
        
        print(f"\n📌 Par source:")
        for source, count in stats['by_source'].items():
            print(f"   • {source}: {count} annonces")
        
        print(f"\n🌍 Pays principaux:")
        sorted_countries = sorted(stats['by_country'].items(), key=lambda x: x[1], reverse=True)
        for country, count in sorted_countries[:10]:
            print(f"   • {country}: {count} annonces")

# ============================================================================
# API FLASK (OPTIONNELLE)
# ============================================================================

def start_api_server(offers_file: str = "vie_offers.json"):
    """Lance un serveur Flask pour servir les données"""
    try:
        from flask import Flask, jsonify, request
    except ImportError:
        print("⚠️  Flask n'est pas installé")
        print("   Installez-le avec: pip install flask")
        return
    
    try:
        with open(offers_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"❌ Fichier {offers_file} non trouvé!")
        print("   Lancez d'abord: python3 unify_vie_offers.py")
        return
    
    app = Flask(__name__)
    
    @app.route('/api/offers', methods=['GET'])
    def get_offers():
        """Retourne toutes les annonces avec filtres optionnels"""
        source = request.args.get('source')
        country = request.args.get('country')
        keyword = request.args.get('keyword')  # ✅ Nouveau filtre par mot-clé
        limit = request.args.get('limit', default=100, type=int)
        
        offers = data['offers']
        
        if source:
            offers = [o for o in offers if source.lower() in o['source'].lower()]
        if country:
            offers = [o for o in offers if country.lower() in o['country'].lower()]
        if keyword:
            offers = [o for o in offers if keyword.lower() in o['title'].lower()
                      or keyword.lower() in (o.get('description') or '').lower()]
        
        offers = offers[:limit]
        
        return jsonify({
            "count": len(offers),
            "offers": offers
        })
    
    @app.route('/api/stats', methods=['GET'])
    def get_stats():
        """Retourne les statistiques"""
        return jsonify(data['metadata'])
    
    @app.route('/', methods=['GET'])
    def index():
        """Page d'accueil avec doc API"""
        return jsonify({
            "name": "VIE Offers API",
            "version": "1.1",
            "endpoints": {
                "/api/offers": "GET - Récupère toutes les annonces (params: source, country, keyword, limit)",
                "/api/stats": "GET - Récupère les statistiques"
            }
        })
    
    print("\n" + "=" * 70)
    print("🚀 API FLASK DÉMARRÉE")
    print("=" * 70)
    print(f"\n📍 Adresse: http://localhost:5000")
    print(f"\n📚 Endpoints:")
    print(f"   • GET /api/offers?source=VIE&country=Japon&keyword=finance&limit=50")
    print(f"   • GET /api/stats")
    print(f"\n⚠️  Appuyez sur Ctrl+C pour arrêter")
    print("=" * 70 + "\n")
    
    app.run(debug=False, host='0.0.0.0', port=5000)

# ============================================================================
# MAIN  ✅ CORRECTION 1 : argparse remplace sys.argv
# ============================================================================

def parse_args():
    parser = argparse.ArgumentParser(
        description="Unificateur d'annonces VIE (Business France + Welcome to the Jungle)"
    )
    parser.add_argument(
        '--api',
        action='store_true',
        help="Lance l'API Flask au lieu de scraper"
    )
    parser.add_argument(
        '--source',
        choices=['vie', 'wtj', 'all'],
        default='all',
        help="Source à scraper : vie, wtj, ou all (défaut: all)"
    )
    parser.add_argument(
        '--output',
        default='vie_offers.json',
        help="Nom du fichier JSON de sortie (défaut: vie_offers.json)"
    )
    return parser.parse_args()


def main():
    args = parse_args()

    print("\n" + "=" * 70)
    print("🔄 UNIFICATEUR D'ANNONCES VIE")
    print("=" * 70)
    
    if args.api:
        start_api_server(args.output)
        return

    # Mode scraping et export
    unifier = VIEUnifier()
    
    print("\n🔗 Récupération des données...")
    
    if args.source in ('vie', 'all'):
        try:
            unifier.add_vie_offers()
        except Exception as e:
            print(f"⚠️  Impossible de récupérer les annonces VIE: {e}")
    
    if args.source in ('wtj', 'all'):
        try:
            unifier.add_wtj_offers()
        except Exception as e:
            print(f"⚠️  Impossible de récupérer les annonces WTJ: {e}")
    
    # Déduplication
    print("\n🔍 Déduplication...")
    unifier.deduplicate()

    # Export
    unifier.export_json(args.output)
    unifier.print_stats()
    
    print("\n" + "=" * 70)
    print("✅ SCRAPING TERMINÉ!")
    print("=" * 70)
    print(f"\n💡 Pour lancer l'API Flask:")
    print(f"   python3 unify_vie_offers.py --api --output {args.output}")
    print("\n" + "=" * 70)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⏹️  Arrêt...")
    except Exception as e:
        print(f"\n❌ Erreur: {e}")
        import traceback
        traceback.print_exc()