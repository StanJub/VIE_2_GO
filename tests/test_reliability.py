import json
import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

import api
from unify_vie_offers import atomic_write_json


def offer(source, title):
    return dict(source=source, title=title, company='Company', country='France', link='https://example.com/'+title)


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.original_file = api.OFFERS_FILE
        api.OFFERS_FILE = os.path.join(self.directory.name, 'offers.json')
        api.scrape_status.update(running=False, success=None, by_source={}, error=None)
        self.client = api.app.test_client()

    def tearDown(self):
        api.OFFERS_FILE = self.original_file
        self.directory.cleanup()

    def cache(self, offers=None, age=0):
        stamp = (datetime.now(timezone.utc)-timedelta(seconds=age)).isoformat()
        atomic_write_json(api.OFFERS_FILE, {'offers': offers or [], 'metadata': {
            'source_refreshed_at': {'vie': stamp, 'wtj': stamp}, 'exported_at': stamp}})

    def test_fresh_cache_skips_thread_but_manual_refresh_forces_it(self):
        self.cache()
        with patch('api.threading.Thread') as thread:
            self.assertEqual(self.client.post('/api/scrape', json={'force':False}).status_code, 200)
            thread.assert_not_called()
            self.assertEqual(self.client.post('/api/scrape', json={'force':True}).status_code, 202)
            thread.return_value.start.assert_called_once()

    def test_old_cache_and_legacy_cache_require_refresh(self):
        self.cache(age=901)
        self.assertFalse(api.cache_is_fresh('all'))
        atomic_write_json(api.OFFERS_FILE, {'offers': [], 'metadata': {'exported_at':datetime.now().isoformat()}})
        self.assertFalse(api.cache_is_fresh('all'))

    def test_concurrent_requests_reserve_only_one_job(self):
        barrier = threading.Barrier(2)
        results = []
        real_thread = threading.Thread
        def request():
            with api.app.test_client() as client:
                barrier.wait()
                results.append(client.post('/api/scrape', json={'force':True}).status_code)
        with patch('api.threading.Thread') as job:
            clients = [real_thread(target=request) for _ in range(2)]
            for client in clients: client.start()
            for client in clients: client.join()
            self.assertEqual(sorted(results), [202,409])
            job.return_value.start.assert_called_once()
        self.assertTrue(api.scrape_status['running'])

    def test_thread_start_failure_releases_reservation(self):
        with patch('api.threading.Thread.start', side_effect=RuntimeError('unavailable')):
            self.assertEqual(self.client.post('/api/scrape', json={}).status_code, 503)
        self.assertFalse(api.scrape_status['running'])

    def test_partial_failure_keeps_previous_source_and_discards_partial_results(self):
        old_vie = offer('Business France (VIE)', 'old-vie')
        old_wtj = offer('Welcome to the Jungle', 'old-wtj')
        self.cache([old_vie, old_wtj], age=1000)
        before = json.loads(Path(api.OFFERS_FILE).read_text())['metadata']['source_refreshed_at']
        class FakeUnifier:
            def __init__(self): self.offers=[]
            def add_vie_offers(self):
                self.offers=[SimpleNamespace(to_dict=lambda:offer('Business France (VIE)', 'partial'))]
                raise RuntimeError('source down')
            def add_wtj_offers(self):
                self.offers=[SimpleNamespace(to_dict=lambda:offer('Welcome to the Jungle','new-wtj'))]
            def deduplicate(self): pass
        with patch('api.VIEUnifier', FakeUnifier): api.run_scraping()
        data, _ = api.load_offers()
        self.assertEqual([o['title'] for o in data['offers']], ['old-vie','new-wtj'])
        self.assertEqual(data['metadata']['source_refreshed_at']['vie'], before['vie'])
        self.assertNotEqual(data['metadata']['source_refreshed_at']['wtj'], before['wtj'])
        self.assertFalse(api.cache_is_fresh('all'))
        self.assertFalse(api.scrape_status['success'])
        self.assertFalse(api.scrape_status['running'])

    def test_all_sources_fail_without_overwriting_cache(self):
        self.cache([offer('Business France (VIE)', 'retained')], age=1000)
        before = Path(api.OFFERS_FILE).read_bytes()
        class FailedUnifier:
            def add_vie_offers(self): raise RuntimeError('down')
            def add_wtj_offers(self): raise RuntimeError('down')
        with patch('api.VIEUnifier', FailedUnifier): api.run_scraping()
        self.assertEqual(Path(api.OFFERS_FILE).read_bytes(), before)
        self.assertFalse(api.scrape_status['running'])
        self.assertFalse(api.scrape_status['success'])

    def test_single_source_refresh_preserves_other_source(self):
        self.cache([offer('Business France (VIE)', 'old-vie'), offer('Welcome to the Jungle', 'keep-wtj')], age=1000)
        class SuccessfulUnifier:
            def __init__(self): self.offers=[]
            def add_vie_offers(self):
                self.offers=[SimpleNamespace(to_dict=lambda:offer('Business France (VIE)', 'new-vie'))]
            def deduplicate(self): pass
        with patch('api.VIEUnifier', SuccessfulUnifier): api.run_scraping('vie')
        self.assertEqual({o['title'] for o in api.load_offers()[0]['offers']}, {'keep-wtj','new-vie'})
        self.assertTrue(api.cache_is_fresh('vie'))
        self.assertFalse(api.cache_is_fresh('all'))

    def test_failed_atomic_replace_keeps_original_and_cleans_temporary_file(self):
        self.cache([offer('Business France (VIE)','original')])
        before = Path(api.OFFERS_FILE).read_bytes()
        with patch('unify_vie_offers.os.replace', side_effect=OSError('disk error')):
            with self.assertRaises(OSError): atomic_write_json(api.OFFERS_FILE, {'offers':[]})
        self.assertEqual(Path(api.OFFERS_FILE).read_bytes(), before)
        self.assertEqual(os.listdir(self.directory.name), ['offers.json'])

    def test_readers_never_observe_partial_json(self):
        self.cache()
        errors=[]
        def read():
            for _ in range(100):
                if api.load_offers()[1]: errors.append(True)
        reader=threading.Thread(target=read)
        reader.start()
        for i in range(10): self.cache([offer('Business France (VIE)',str(i))])
        reader.join()
        self.assertFalse(errors)

    def test_deduplication_does_not_discard_source_snapshots(self):
        self.cache([offer('Business France (VIE)','same'),offer('Welcome to the Jungle','same')])
        self.assertEqual(self.client.get('/api/offers').json['count'], 1)
        self.assertEqual(self.client.get('/api/offers?source=Welcome').json['count'], 1)
        self.assertEqual(len(api.load_offers()[0]['offers']),2)

    def test_invalid_request_is_rejected(self):
        for payload in [['vie'], {'force':'yes'}, {'source':'unknown'}]:
            self.assertEqual(self.client.post('/api/scrape',json=payload).status_code,400)

if __name__ == '__main__': unittest.main()
