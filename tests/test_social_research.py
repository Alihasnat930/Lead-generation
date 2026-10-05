import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from streamlit.testing.v1 import AppTest

from core.config import config
from core import integrations
from core.outreach_store import OutreachStore
from core.providers import ProviderError, SearchPage
from core.social_discord import collect, channel_ids
from core.social_research import DEFAULT_TOPICS, source_url, normalize_record, import_records, export_records, validate_settings
from core.social_sources import plan_queries, search
from core.social_store import SocialStore
from core.social_worker import run


def settings(**changes):
    return {**{'name':'Academic support','platforms':['Reddit','Facebook','Discord'],'markets':['UK','US'],
               'services':['Proofreading','Thesis editing'],'keywords':[], 'target':1000,'max_requests':20,'max_pages':3},**changes}


def post(number=1, **changes):
    return normalize_record({'url':f'https://www.reddit.com/r/GradSchool/comments/a{number}/thesis/',
        'title':'Need thesis proofreading','text':'Looking for a proofreader for my thesis at a UK university.',**changes},settings())


class EvidenceTests(unittest.TestCase):
    def test_assignment_thesis_and_quiz_requests_match_without_university_keyword(self):
        examples = [
            ('Assignment support', 'Need feedback on my assignments in the UK.'),
            ('Thesis & dissertation support', 'Looking for an editor for our theses in the UK.'),
            ('Thesis & dissertation support', 'Need help reviewing dissertations in the UK.'),
            ('Quiz preparation', 'Need a tutor to help prepare for biology quizzes in the UK.'),
            ('Quiz preparation', 'Can anyone explain the concepts for my next quiz?'),
            ('Homework & coursework', 'Struggling with my homework, can anyone explain this?'),
            ('Homework & coursework', 'Need feedback on my course work in the UK.'),
            ('Essay & report support', 'Need feedback on lab reports in the UK.'),
            ('Essay & report support', 'Looking for a tutor to review my essays.'),
            ('Exam preparation', 'Need help preparing for exams in the UK.'),
            ('Exam preparation', 'Looking for a tutor to explain these practice tests.'),
        ]
        for topic, text in examples:
            with self.subTest(topic=topic, text=text):
                result = normalize_record({'url':post()['url'], 'text':text}, settings(services=[topic]))
                self.assertTrue(result['relevant'])
                self.assertEqual(result['intent'], 'Possible request')
                self.assertEqual(result['services'], [topic])
        # Previously saved topic selections remain valid.
        self.assertEqual(validate_settings(settings())['services'], ['Proofreading', 'Thesis editing'])

    def test_new_topics_do_not_import_unselected_topics_or_generic_software_tests(self):
        rows = [
            {'url':post(1)['url'], 'text':'Need a tutor for chemistry quizzes in the UK.'},
            {'url':post(2)['url'], 'text':'Need help with my thesis in the UK.'},
            {'url':post(3)['url'], 'text':'Need help with our software tests in the UK.'},
        ]
        result, skipped = import_records(json.dumps(rows).encode(), 'posts.json', settings(services=['Quiz preparation']))
        self.assertEqual((len(result), skipped), (1, 2))
        self.assertEqual(result[0]['url'], post(1)['url'])
        software = normalize_record(rows[2], settings(services=['Exam preparation']))
        self.assertFalse(software['relevant'])

    def test_normalization_removes_trackers_and_rejects_nonposts_and_private_messages(self):
        self.assertEqual(source_url('https://old.reddit.com/r/GradSchool/comments/abc/title/?utm_source=test'),
                         ('Reddit','https://www.reddit.com/r/gradschool/comments/abc/'))
        for url in ('https://reddit.com.evil.example/r/x/comments/abc/', 'https://facebook.com/profile.php?id=123',
                    'https://discord.com/channels/@me/12345678901234567/12345678901234567',
                    'https://private:secret@reddit.com/r/abc/comments/xyz/', 'http://127.0.0.1/posts/1'):
            self.assertIsNone(source_url(url),url)
        self.assertIsNotNone(source_url('https://www.facebook.com/groups/students/posts/12345'))

    def test_search_region_is_not_location_evidence_and_ads_are_not_requests(self):
        r=post(text='Looking for a thesis proofreader. Please help me.')
        self.assertEqual(r['market'],'Unknown')
        self.assertEqual(r['intent'],'Possible request')
        self.assertEqual(post(text='Need thesis proofreading in the US')['market'],'US')
        self.assertEqual(post(text='Need thesis proofreading, help us')['market'],'Unknown')
        advert=post(text='We offer thesis proofreading in the UK. Contact us.')
        self.assertEqual(advert['intent'],'Service advertisement')
        self.assertLess(advert['score'],post()['score'])

    def test_import_drops_author_data_and_redacts_contacts(self):
        rows=[{'url':'https://reddit.com/r/GradSchool/comments/abc/','title':'Thesis proofreading',
               'text':'Need a thesis proofreader in the UK. person@example.com +44 7700 900123',
               'author':{'email':'private@example.com'},'username':'student-name'}]
        result,skipped=import_records(json.dumps(rows).encode(),'posts.json',settings())
        self.assertEqual((len(result),skipped),(1,0))
        data=json.dumps(result)
        self.assertNotIn('person@example.com',data)
        self.assertNotIn('student-name',data)
        self.assertNotIn('7700',data)
        self.assertIn('[email omitted]',data)

    def test_discord_export_imports_messages_without_members(self):
        data={'guild':{'id':'12345678901234567'},'channel':{'id':'22345678901234567'},'messages':[
            {'id':'32345678901234567','content':'Need thesis proofreading at a UK university','author':{'name':'private'},'timestamp':'2026-10-01T12:00:00Z'}]}
        result,skipped=import_records(json.dumps(data).encode(),'discord.json',settings())
        self.assertEqual(result[0]['platform'],'Discord')
        self.assertTrue(result[0]['posted_at'])
        self.assertNotIn('author',result[0])

    def test_import_limits_and_csv_formula_escaping(self):
        with self.assertRaises(ValueError): import_records(b'x'*(5*1024*1024+1),'large.csv',settings())
        with self.assertRaises(ValueError): import_records(b'null','bad.json',settings())
        value=export_records([{**post(),'title':'=HYPERLINK("https://evil.example")'}]).decode('utf-8-sig')
        self.assertIn("'=HYPERLINK",value)


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store=SocialStore(Path(self.temp.name)/'research.db')

    def test_exact_target_duplicates_resume_and_business_isolation(self):
        job=self.store.create(settings(target=2),[{'engine':'bing','query':'test'}])
        token=self.store.reserve(job)
        query,_=self.store.next_query(job)
        self.assertEqual(self.store.save_records(job,[post(1),post(1),post(2),post(3)],token,query,True),2)
        self.assertEqual(self.store.count(job),2)
        # A partially consumed page stays on the same cursor for a later larger target.
        self.assertEqual(self.store.next_query(job)[0]['page'],1)
        self.store.finish(job,token,'paused','test')
        self.store.revise_limits(job,3,20)
        token=self.store.reserve(job)
        self.assertEqual(self.store.save_records(job,[post(1),post(2),post(3)],token,query,False),1)
        self.assertEqual(self.store.count(job),3)
        self.assertEqual(self.store.base.stats()['processed'],0)
        self.assertEqual(self.store.base.jobs(),[])

    def test_stale_worker_cannot_insert_or_advance_and_reviews_survive_duplicates(self):
        job=self.store.create(settings(),[{'engine':'bing','query':'test'}])
        token=self.store.reserve(job)
        query,_=self.store.next_query(job)
        self.assertEqual(self.store.save_records(job,[post()], 'stale',query,False),0)
        self.assertEqual(self.store.count(job),0)
        self.store.save_records(job,[post()],token,query,False)
        self.store.review(post()['url'],'Dismissed')
        self.store.save_records(job,[post()])
        self.assertEqual(self.store.records(job)[0]['review_status'],'Dismissed')
        with self.assertRaises(ValueError): self.store.reserve(job)

    def test_per_run_evidence_and_discord_cursor_are_saved(self):
        first=self.store.create(settings(),[])
        second=self.store.create(settings(),[{'engine':'discord','channel':'12345678901234567'}])
        self.store.save_records(first,[post(text='Need thesis proofreading in the UK')])
        token=self.store.reserve(second)
        query,_=self.store.next_query(second)
        self.store.save_records(second,[post(text='Need thesis proofreading in the USA')],token,query,True,'22345678901234567')
        self.assertEqual(self.store.records(first)[0]['market'],'UK')
        self.assertEqual(self.store.records(second)[0]['market'],'US')
        self.assertEqual(self.store.next_query(second)[0]['data']['before'],'22345678901234567')

    def test_worker_stops_at_target_and_failure_preserves_query(self):
        job=self.store.create(settings(target=2),[{'engine':'bing','query':'test'}])
        token=self.store.reserve(job)
        with patch.object(self.store,'source_slot',return_value=0):
            run(job,self.store,token,fetch=lambda *a:SearchPage([post(1),post(2),post(3)],True))
        self.assertEqual(self.store.job(job)['status'],'target_reached')
        self.assertEqual(self.store.count(job),2)
        blocked=self.store.create(settings(),[{'engine':'bing','query':'test'}])
        token=self.store.reserve(blocked)
        with patch.object(self.store,'source_slot',return_value=0):
            run(blocked,self.store,token,fetch=Mock(side_effect=ProviderError('captcha','Challenge')))
        self.assertEqual(self.store.job(blocked)['status'],'blocked')
        self.assertEqual(self.store.next_query(blocked)[1],1)

    def test_request_limit_and_pause_do_not_fetch(self):
        job=self.store.create(settings(max_requests=1),[{'engine':'bing','query':'test'}])
        token=self.store.reserve(job)
        self.store.reserve_request(job,token)
        fetch=Mock()
        run(job,self.store,token,fetch=fetch)
        self.assertEqual(self.store.job(job)['status'],'request_limit')
        token=self.store.reserve(job)
        self.store.pause(job)
        run(job,self.store,token,fetch=fetch)
        self.assertEqual(self.store.job(job)['status'],'paused')
        fetch.assert_not_called()

    def test_thousand_post_target_and_checkpoint_hook(self):
        job=self.store.create(settings(target=1000),[])
        with patch('core.cloud_runtime.checkpoint_campaign') as checkpoint:
            self.assertEqual(self.store.save_records(job,[post(i) for i in range(1003)]),1000)
            checkpoint.assert_called_once()
        self.assertEqual(self.store.count(job),1000)


class ConnectorTests(unittest.TestCase):
    def test_default_searches_cover_dissertations_quizzes_and_coursework(self):
        queries = plan_queries(settings(services=list(DEFAULT_TOPICS)))
        for platform in ('Reddit', 'Facebook'):
            for market in ('UK', 'US'):
                for engine in ('duckduckgo', 'bing'):
                    selected = [q['query'] for q in queries if (q['platform'], q['market'], q['engine']) == (platform, market, engine)]
                    self.assertEqual(len(selected), len(DEFAULT_TOPICS))
                    for term in ('assignments', 'thesis', 'dissertation', 'quizzes', 'homework', 'coursework'):
                        self.assertTrue(any('"'+term+'"' in q for q in selected), term)

    def test_ddgs_keeps_only_selected_platform_post_links(self):
        client=Mock()
        client.__enter__=Mock(return_value=client)
        client.__exit__=Mock(return_value=None)
        client.text.return_value=[{'href':post()['url'],'title':'Need thesis proofreading','body':'UK university thesis'},
                                 {'href':'https://example.com','title':'Need thesis proofreading','body':'UK university thesis'}]
        with patch('ddgs.DDGS',return_value=client):
            result=search({'engine':'duckduckgo','query':'test','market':'UK','platform':'Reddit'},1,settings())
        self.assertEqual(len(result.items),1)
        self.assertTrue(result.items[0]['source_type'].startswith('Search snippet'))

    def test_discord_requires_bot_allowlist_and_server_channel(self):
        channel='12345678901234567'
        values={'DISCORD_BOT_TOKEN':'fixture','DISCORD_RESEARCH_CHANNELS':channel}
        with patch('core.social_discord._get') as get:
            with self.assertRaises(ProviderError):collect({'channel':'99999999999999999'},settings(),values)
            get.assert_not_called()
            get.side_effect=[{'bot':False}]
            with self.assertRaises(ProviderError):collect({'channel':channel},settings(),values)
            get.side_effect=[{'bot':True},{'type':1}]
            with self.assertRaises(ProviderError):collect({'channel':channel},settings(),values)

    def test_discord_empty_content_does_not_look_like_no_results(self):
        values={'DISCORD_BOT_TOKEN':'fixture','DISCORD_RESEARCH_CHANNELS':'12345678901234567'}
        with patch('core.social_discord._get',side_effect=[{'bot':True},{'guild_id':'22345678901234567'},[{'id':'32345678901234567','content':''}]]):
            with self.assertRaisesRegex(ProviderError,'Message Content'):
                collect({'channel':'12345678901234567'},settings(),values)

    def test_admin_can_remove_all_discord_channels(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(config,'APP_ADMIN_EMAILS','owner@example.com'):
            store=OutreachStore(Path(folder)/'outreach.db')
            actor={'email':'owner@example.com','verified':True}
            integrations.save(actor,{'DISCORD_RESEARCH_CHANNELS':'12345678901234567'},store)
            integrations.save(actor,{'DISCORD_RESEARCH_CHANNELS':''},store)
            self.assertEqual(integrations.load(store)['DISCORD_RESEARCH_CHANNELS'],'')


class SocialUITests(unittest.TestCase):
    def test_create_research_does_not_fetch_or_change_business_leads(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(config,'APP_AUTH_MODE','legacy'),patch.object(config,'APP_LOGIN_PASSWORD_HASH',''),patch.object(config,'DATABASE_PATH',str(Path(folder)/'campaign.db')),patch.object(config,'OUTREACH_DATABASE_PATH',str(Path(folder)/'outreach.db')),patch('requests.get') as get,patch('requests.post') as post_request:
            app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=30).run()
            app.sidebar.radio[0].set_value('Social research').run()
            self.assertEqual(len(app.exception),0)
            next(b for b in app.button if b.label=='Create research').click().run()
            self.assertEqual(len(app.exception),0)
            store=SocialStore()
            self.assertEqual(len(store.jobs()),1)
            self.assertEqual(store.job(store.jobs()[0]['id'])['settings']['services'],list(DEFAULT_TOPICS))
            self.assertEqual(store.base.stats()['processed'],0)
            store.save_records(store.jobs()[0]['id'],[post()])
            app.run()
            self.assertEqual(len(app.exception),0)
            self.assertTrue(any(b.label=='Save post review' for b in app.button))
            next(s for s in app.selectbox if s.label=='Set review').set_value('Relevant')
            next(b for b in app.button if b.label=='Save post review').click().run()
            self.assertEqual(len(app.exception),0)
            self.assertEqual(store.records(store.jobs()[0]['id'])[0]['review_status'],'Relevant')
            get.assert_not_called()
            post_request.assert_not_called()


if __name__=='__main__':unittest.main()
