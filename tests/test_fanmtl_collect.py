"""Deterministic offline checks for the FanMTL collector."""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / 'tools' / 'novel_ingest' / 'fanmtl_collect.py'
spec = importlib.util.spec_from_file_location('fanmtl_collect', MODULE)
c = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = c
spec.loader.exec_module(c)

SENTENCE = 'Cross Kahn looked at the blue dragon, and the powerful Haki shook the island. '
BODY = SENTENCE * 14
CATALOG = f'''<html><head><title>{c.ENGLISH_TITLE} Novel</title></head>
<body><h1>{c.ENGLISH_TITLE}</h1><h2>{c.CHINESE_TITLE}</h2>
<p>234 Chapters Ongoing Status</p>
<a href="/novel/{c.SLUG}_1.html">1 Chapter 1: West Blue Overlord</a>
<a href="/novel/{c.SLUG}_55.html">55 Chapter 55: Dragon's Return</a>
<a href="/novel/{c.SLUG}_234.html">234 Chapter 234: Last One</a>
<a href="https://other.com/novel/{c.SLUG}_56.html">56 Chapter 56: Spoof</a>
</body></html>'''


def chapter_html(number=55, text=BODY, prefix=''):
    return f'''<html><head><title>Chapter {number}: Dragon's Return - {c.ENGLISH_TITLE}</title></head>
    <body><h1><a href="{c.CATALOG_URL}">{c.ENGLISH_TITLE}</a></h1>
    <h2>Chapter {number}: Dragon's Return</h2>
    <div class="chapter-content"><div class="ad">Join now and save everything!</div>
    <p>{prefix}{text}</p>
    <p>Celebrate the 7-day long holiday of National Day and enjoy reading books! Recharge 100 and get 500 VIP coupons!</p>
    <nav>Next Previous</nav></div>
    <div class="chapter-content">You'll Also Like</div></body></html>'''


class FakeClient:
    def __init__(self, mapping):
        self.mapping = mapping
        self.urls = []

    def html(self, url):
        self.urls.append(url)
        result = self.mapping[url]
        if isinstance(result, Exception):
            raise result
        return result


class Tests(unittest.TestCase):
    def test_valid_urls_only(self):
        self.assertEqual(c.validate_url(c.CATALOG_URL), c.CATALOG_URL)
        self.assertEqual(c.validate_url(c.chapter_url(55), number=55), c.chapter_url(55))
        for url in (
            'http://www.fanmtl.com/novel/' + c.SLUG + '_55.html',
            'https://www.fanmtl.com.bad.org/novel/' + c.SLUG + '_55.html',
            'https://www.fanmtl.com/novel/' + c.SLUG + '_55.html?next=evil',
            'https://www.fanmtl.com/novel/different_55.html',
            'https://evil.org/novel/' + c.SLUG + '_55.html',
        ):
            with self.assertRaises(c.CollectorError, msg=url):
                c.validate_url(url, number=55)
        with self.assertRaises(c.CollectorError):
            c.validate_url(c.chapter_url(54), number=55)

    def test_catalog_metadata_and_real_links(self):
        info = c.parse_catalog(CATALOG)
        self.assertEqual(info['claimed_total'], 234)
        self.assertEqual(info['chapter_numbers_seen'], [1, 55, 234])
        self.assertEqual(info['chapters_seen_on_this_page'], 3)
        self.assertTrue(info['chapters'][55]['url'].endswith('_55.html'))

    def test_catalog_rejects_other_book(self):
        with self.assertRaises(c.CollectorError) as ctx:
            c.parse_catalog(CATALOG.replace(c.CHINESE_TITLE, 'another book'))
        self.assertEqual(ctx.exception.code, 'WRONG_BOOK')

    def test_chapter_text_and_ad_removal(self):
        title, text, selector = c.parse_chapter(chapter_html(), number=55)
        self.assertIn("Chapter 55", title)
        self.assertIn('Cross Kahn', text)
        self.assertNotIn('Join now', text)
        self.assertNotIn('Celebrate the 7-day', text)
        self.assertNotIn('Next Previous', text)
        self.assertEqual(selector, '.chapter-content')

    def test_chapter_wrong_number(self):
        with self.assertRaises(c.CollectorError) as ctx:
            c.parse_chapter(chapter_html(56), number=55)
        self.assertEqual(ctx.exception.code, 'WRONG_CHAPTER')

    def test_chapter_does_not_save_short_content(self):
        with self.assertRaises(c.CollectorError) as ctx:
            c.parse_chapter(chapter_html(text='Kahn.'), number=55)
        self.assertEqual(ctx.exception.code, 'MISSING_BODY')

    def test_chapter_rejects_wrong_work(self):
        with self.assertRaises(c.CollectorError) as ctx:
            c.parse_chapter(chapter_html().replace(c.ENGLISH_TITLE, 'An unrelated novel'), number=55)
        self.assertEqual(ctx.exception.code, 'WRONG_BOOK')

    def test_catalog_audit_does_not_get_any_chapter(self):
        fake = FakeClient({c.CATALOG_URL: CATALOG})
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / 'report'
            data = c.collect(55, 58, out, client=fake)
            self.assertEqual(data['status'], 'CATALOG_ONLY')
            self.assertEqual(fake.urls, [c.CATALOG_URL])
            self.assertEqual(data['saved_text_count'], 0)
            self.assertFalse((out / 'mtl_en').exists())
            self.assertTrue((out / 'summary.md').exists())

    def test_collected_text_has_sha256_and_correct_metadata(self):
        fake = FakeClient({c.CATALOG_URL: CATALOG, c.chapter_url(55): chapter_html()})
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / 'chapter'
            data = c.collect(55, 55, out, save_text=True, rights_confirmed=True, client=fake)
            self.assertEqual(data['status'], 'PASS')
            self.assertEqual(data['saved_text_count'], 1)
            row = data['chapters'][0]
            self.assertEqual(row['status'], 'SAVED')
            self.assertEqual(len(row['sha256']), 64)
            text = (out / row['file']).read_text(encoding='utf-8')
            self.assertEqual(row['characters'], len(text))
            self.assertEqual(json.loads((out / 'manifest.json').read_text())['ru_translated_count'], 0)
            self.assertEqual(row['source_url'], c.chapter_url(55))

    def test_partial_missing_chapter(self):
        fake = FakeClient({c.CATALOG_URL: CATALOG, c.chapter_url(55): chapter_html(),
                           c.chapter_url(56): c.CollectorError('HTTP_ERROR', 'HTTP 404')})
        with tempfile.TemporaryDirectory() as d:
            data = c.collect(55, 56, Path(d) / 'out', save_text=True,
                             rights_confirmed=True, client=fake)
            self.assertEqual(data['status'], 'PARTIAL')
            self.assertEqual([p['status'] for p in data['chapters']], ['SAVED', 'HTTP_ERROR'])

    def test_block_stops_batch_without_bypass(self):
        fake = FakeClient({c.CATALOG_URL: CATALOG,
                           c.chapter_url(55): c.CollectorError('BLOCKED', 'HTTP 403')})
        with tempfile.TemporaryDirectory() as d:
            data = c.collect(55, 58, Path(d) / 'out', save_text=True,
                             rights_confirmed=True, client=fake)
            self.assertEqual(data['status'], 'PARTIAL')
            self.assertEqual(data['saved_text_count'], 0)
            self.assertEqual(len(fake.urls), 2)
            self.assertEqual(data['chapters'][0]['status'], 'BLOCKED')

    def test_network_error_is_not_catalog_error(self):
        fake = FakeClient({c.CATALOG_URL: c.CollectorError('NETWORK_ERROR', 'DNS resolution failed')})
        with tempfile.TemporaryDirectory() as d:
            data = c.collect(55, 58, Path(d) / 'out', client=fake)
            self.assertEqual(data['status'], 'NETWORK_ERROR')
            self.assertEqual(data['saved_text_count'], 0)

    def test_limit_rights_output_protection(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                c.collect(1, 11, Path(d) / 'a')
            with self.assertRaises(ValueError):
                c.collect(55, 55, Path(d) / 'b', save_text=True)
            with self.assertRaises(ValueError):
                c.collect(55, 55, Path(d) / 'c', delay=1)
            out = Path(d) / 'nonempty'
            out.mkdir()
            (out / 'existing.txt').write_text('data')
            with self.assertRaises(ValueError):
                c.collect(55, 55, out)

    def test_robots_allow_and_no_redirect(self):
        class Response:
            def __init__(self, code, text, ctype='text/plain'):
                self.status_code = code
                self.content = text.encode()
                self.text = text
                self.encoding = 'utf-8'
                self.apparent_encoding = 'utf-8'
                self.headers = {'content-type': ctype}

        fake_get = Mock(side_effect=[Response(200, 'User-agent: *\nDisallow: /novel/'),
                                     Response(200, CATALOG, 'text/html')])
        with patch('requests.Session.get', fake_get):
            client = c.Client(delay=3)
            with patch('time.sleep'):
                with self.assertRaises(c.CollectorError) as ctx:
                    client.html(c.CATALOG_URL)
            self.assertEqual(ctx.exception.code, 'ROBOTS_DISALLOWED')
            self.assertEqual(fake_get.call_count, 1)

        with patch('requests.Session.get', return_value=Response(302, 'Moved')):
            with patch('time.sleep'):
                with self.assertRaises(c.CollectorError) as ctx:
                    c.Client().html(c.CATALOG_URL)
            self.assertEqual(ctx.exception.code, 'REDIRECT')


if __name__ == '__main__':
    unittest.main()
