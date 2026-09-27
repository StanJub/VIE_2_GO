import unittest
from unittest.mock import patch
from countries import normalize_offer, normalize_cache, resolve_country, country_identity
import api


class CountryTests(unittest.TestCase):
    def test_multilingual_and_iso(self):
        for name in ('ALLEMAGNE', 'Germany', 'Deutschland', 'DE', 'DEU', ' Allemagne '):
            self.assertEqual(resolve_country(name), 'DE')
        self.assertEqual(resolve_country("COTE D'IVOIRE"), 'CI')
        self.assertEqual(resolve_country('TCHEQUIE / REPUBLIQUE TCHEQUE'), 'CZ')

    def test_unknown_and_ambiguous_never_guessed(self):
        for name in ('CONGO', 'Korea', 'Atlantis', 'German', ''):
            self.assertIsNone(resolve_country(name))
        self.assertEqual(resolve_country('CONGO', country_code='CD'), 'CD')
        self.assertEqual(resolve_country('Congo-Brazzaville'), 'CG')

    def test_congo_city_context(self):
        for city in ('POINTE NOIRE', 'Pointe-Noire', 'Brazzaville'):
            result = normalize_offer({'country': 'CONGO', 'city': city})
            self.assertEqual(result['country_code'], 'CG')
            self.assertEqual(result['country'], 'Congo-Brazzaville')
            self.assertEqual(result['country_raw'], 'CONGO')
        self.assertEqual(resolve_country('CONGO', city='Kinshasa'), 'CD')
        self.assertIsNone(resolve_country('CONGO', city='Unknown'))
        self.assertEqual(resolve_country('CONGO', country_code='CD', city='Pointe-Noire'), 'CD')

    def test_idempotence_and_original_preserved(self):
        original = {'country': 'Germany', 'source': 'future source', 'title': 'Engineer'}
        normalized = normalize_offer(original)
        self.assertEqual(normalized['country'], 'Allemagne')
        self.assertEqual(normalized['country_raw'], 'Germany')
        self.assertEqual(normalized, normalize_offer(normalized))
        self.assertEqual(original['country'], 'Germany')
        self.assertEqual(country_identity(normalized), 'DE')

    def test_cache_migration_retains_offers_dates_and_reports_unknowns(self):
        data = {'metadata': {'exported_at': 'original-date'}, 'offers': [
            {'country': 'Germany'}, {'country': 'ALLEMAGNE'}, {'country': 'CONGO'}]}
        normalized = normalize_cache(data)
        self.assertEqual(len(normalized['offers']), 3)
        self.assertEqual(normalized['metadata']['exported_at'], 'original-date')
        self.assertEqual(normalized['metadata']['unresolved_countries'], [{'source': '', 'value': 'CONGO'}])
        self.assertEqual(normalized, normalize_cache(normalized))

    def test_api_filters_translations_and_codes(self):
        data = normalize_cache({'metadata': {}, 'offers': [
            {'country': 'Germany', 'title': 'A', 'company': 'A', 'source': 'new'},
            {'country': 'ALLEMAGNE', 'title': 'B', 'company': 'B', 'source': 'old'},
            {'country': 'Spain', 'title': 'C', 'company': 'C', 'source': 'new'}]})
        with patch.object(api, 'load_offers', return_value=(data, None)):
            for name in ('DE', 'Germany', 'Allemagne'):
                response = api.app.test_client().get('/api/offers', query_string={'country': name})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json['count'], 2)

    def test_import_normalizes_before_deduplication(self):
        from unify_vie_offers import UnifiedOffer, OfferSource
        offer = UnifiedOffer(title='A', company='B', source=OfferSource.VIE,
                             city='', country='Germany', duration_months=None,
                             start_date=None, description=None, salary=None)
        self.assertEqual(offer.country_code, 'DE')
        self.assertEqual(offer.to_dict()['country_raw'], 'Germany')
        offers = [dict(offer.to_dict(), country='ALLEMAGNE'), offer.to_dict()]
        self.assertEqual(len(api.unique_offers(offers)), 1)
