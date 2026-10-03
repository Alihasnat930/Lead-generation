from datetime import datetime
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo
import requests

from core import accounts, integrations, messaging, outreach_scheduler
from core.config import config
from core.outreach_store import OutreachStore

ADMIN={'email':'owner@example.com','subject':'verified-id','verified':True,'provider':'email'}
MEMBER={'email':'client@example.com','subject':'member-id','verified':True,'provider':'email'}


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.admin=patch.object(config,'APP_ADMIN_EMAILS','owner@example.com')
        self.admin.start()
        self.addCleanup(self.admin.stop)
        self.store=OutreachStore(Path(self.temp.name)/'outreach.db')


class AccountTests(StoreTest):
    def test_only_verified_approved_users_enter_workspace(self):
        self.assertEqual(accounts.role_for(ADMIN,self.store),'admin')
        self.assertIsNone(accounts.role_for(dict(ADMIN,verified=False),self.store))
        self.assertIsNone(accounts.role_for(MEMBER,self.store))
        accounts.request_access(MEMBER,self.store)
        self.assertIsNone(accounts.role_for(MEMBER,self.store))
        accounts.set_access(ADMIN,' CLIENT@example.com ','approved',self.store)
        self.assertEqual(accounts.role_for(MEMBER,self.store),'member')
        accounts.set_access(ADMIN,MEMBER['email'],'revoked',self.store)
        accounts.request_access(MEMBER,self.store)
        self.assertIsNone(accounts.role_for(MEMBER,self.store))

    def test_member_cannot_approve_access_or_change_credentials(self):
        accounts.set_access(ADMIN,MEMBER['email'],'approved',self.store)
        with self.assertRaises(PermissionError): accounts.set_access(MEMBER,'other@example.com','approved',self.store)
        with self.assertRaises(PermissionError): integrations.save(MEMBER,{'TWILIO_AUTH_TOKEN':'fixture'},self.store)

    def test_google_requires_verified_email_and_expected_issuer(self):
        claims={'email':ADMIN['email'],'email_verified':True,'sub':'google-sub','iss':'https://accounts.google.com','exp':2000}
        self.assertEqual(accounts.google_identity(claims,1000)['email'],ADMIN['email'])
        for change in ({'email_verified':False},{'email_verified':'false'},{'iss':'https://evil.example'},{'exp':900}):
            with self.assertRaises(ValueError): accounts.google_identity({**claims,**change},1000)

    def test_signup_requires_verification_and_never_grants_membership(self):
        client=Mock()
        with patch('core.accounts.auth_client',return_value=client):
            message=accounts.sign_up(MEMBER['email'],'a-strong-fixture-password','Client')
            self.assertIn('verify',message)
            self.assertIsNone(accounts.role_for(MEMBER,self.store))
            self.assertEqual(client.auth.sign_up.call_count,1)

    def test_session_checks_provider_and_does_not_trust_cached_email(self):
        saved={'access_token':'fixture','refresh_token':'fixture','expires_at':2000,'signed_in_at':900,'email':'owner@example.com'}
        client=Mock()
        client.auth.get_user.return_value.user=SimpleNamespace(email=MEMBER['email'],email_confirmed_at='confirmed',id='real-provider-id')
        with patch('core.accounts.auth_client',return_value=client):
            identity=accounts.verify_session(saved,1000)
            self.assertEqual(identity['email'],MEMBER['email'])
            client.auth.get_user.side_effect=OSError('offline')
            with self.assertRaises(ValueError): accounts.verify_session(saved,1000)

    def test_saved_secrets_are_preserved_when_fields_left_blank(self):
        integrations.save(ADMIN,{'TWILIO_AUTH_TOKEN':'fixture-token'},self.store)
        integrations.save(ADMIN,{'TWILIO_AUTH_TOKEN':''},self.store)
        self.assertEqual(integrations.load(self.store)['TWILIO_AUTH_TOKEN'],'fixture-token')
        self.assertNotIn('fixture-token',json.dumps(self.store.events()))


class MessagingTests(StoreTest):
    def setUp(self):
        super().setUp()
        self.values={**integrations.load(self.store),'TWILIO_ACCOUNT_SID':'AC'+'a'*32,'TWILIO_AUTH_TOKEN':'fixture-token',
            'TWILIO_WHATSAPP_FROM':'whatsapp:+14155550100','TWILIO_CONTENT_SID':'HX'+'b'*32,
            'WHATSAPP_PHONE_NUMBER_ID':'12345678','WHATSAPP_ACCESS_TOKEN':'fixture-meta',
            'WHATSAPP_API_VERSION':'v23.0','WHATSAPP_TEMPLATE_NAME':'approved_template','WHATSAPP_TEMPLATE_LANGUAGE':'en_US'}
        self.patch=patch('core.integrations.load',return_value=self.values)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.phone='+14155550101'

    def queue(self,phone=None,provider='twilio'):
        phone=phone or self.phone
        messaging.set_consent(self.store,phone,True,'Website opt-in recorded 2026-10-03')
        return messaging.queue_message(self.store,provider,phone,['Client'])

    def test_optin_is_required_and_rechecked_after_queueing(self):
        with self.assertRaises(ValueError): messaging.queue_message(self.store,'twilio',self.phone,[])
        self.queue()
        messaging.set_consent(self.store,self.phone,False,'Opted out')
        with patch('core.messaging.submit') as send:
            self.assertEqual(messaging.run_queue(self.store),0)
            send.assert_not_called()
        self.assertEqual(messaging.messages(self.store)[0]['state'],'blocked')

    def test_queue_dedup_and_provider_acceptance_is_not_delivery(self):
        first=self.queue()
        self.assertEqual(first,self.queue())
        with patch('core.messaging.submit',return_value={'state':'accepted','provider_id':'SMfixture','reason':'Accepted'}) as send:
            self.assertEqual(messaging.run_queue(self.store),1)
            self.assertEqual(messaging.run_queue(self.store),0)
            self.assertEqual(send.call_count,1)
        self.assertEqual(messaging.messages(self.store)[0]['state'],'accepted')

    def test_uncertain_submission_is_never_automatically_retried(self):
        self.queue()
        with patch('core.messaging.requests.post',side_effect=requests.Timeout) as post:
            messaging.run_queue(self.store)
            messaging.run_queue(self.store)
            self.assertEqual(post.call_count,1)
        self.assertEqual(messaging.messages(self.store)[0]['state'],'uncertain')

    def test_restarted_inflight_request_is_blocked(self):
        identifier=self.queue()
        with self.store.db() as db: db.execute("UPDATE channel_messages SET state='sending' WHERE id=?",(identifier,))
        with patch('core.messaging.submit') as send:
            messaging.run_queue(self.store)
            send.assert_not_called()
        self.assertEqual(messaging.messages(self.store)[0]['state'],'uncertain')

    def test_daily_cap_includes_previous_runs(self):
        self.store.save_settings({'whatsapp_daily_limit':1})
        self.queue()
        self.queue('+14155550102')
        with patch('core.messaging.submit',return_value={'state':'accepted','provider_id':'SMfixture','reason':'Accepted'}) as send:
            messaging.run_queue(self.store)
            messaging.run_queue(self.store)
            self.assertEqual(send.call_count,1)

    def test_meta_payload_uses_template_and_provider_id(self):
        self.queue(provider='meta')
        response=Mock(status_code=200)
        response.json.return_value={'messages':[{'id':'wamid.fixture'}]}
        with patch('core.messaging.requests.post',return_value=response) as post:
            messaging.run_queue(self.store)
            body=post.call_args.kwargs['json']
            self.assertEqual(body['type'],'template')
            self.assertEqual(body['recipient_type'],'individual')
            self.assertEqual(body['template']['components'][0]['parameters'][0]['text'],'Client')
        self.assertEqual(messaging.messages(self.store)[0]['provider_id'],'wamid.fixture')

    def test_authentication_checks_never_post(self):
        response=Mock(status_code=200)
        response.json.return_value={'sid':self.values['TWILIO_ACCOUNT_SID'],'status':'active'}
        with patch('core.messaging.requests.get',return_value=response),patch('core.messaging.requests.post') as post:
            self.assertTrue(messaging.check_authentication('twilio',self.values))
            post.assert_not_called()


class ScheduleTests(StoreTest):
    def test_weekday_timezone_and_once_per_day(self):
        self.store.save_settings({'auto_sync_enabled':False,'auto_send_enabled':True,'schedule_timezone':'America/New_York',
            'schedule_weekdays':[0],'hour':9,'minute':0,'initial_enabled':False})
        monday=datetime(2026,10,5,9,0,tzinfo=ZoneInfo('America/New_York')).timestamp()
        with patch('core.pipeline.reconcile'),patch('core.pipeline.run_cycle') as run:
            outreach_scheduler.tick(self.store,monday-1)
            run.assert_not_called()
            outreach_scheduler.tick(self.store,monday)
            outreach_scheduler.tick(self.store,monday+60)
            self.assertEqual(run.call_count,1)
            self.assertEqual(run.call_args.args[0],'followup')

    def test_invalid_schedule_and_empty_enabled_days_rejected(self):
        for values in ({'schedule_timezone':'not/a/timezone'},{'auto_send_enabled':True,'schedule_weekdays':[]},{'sync_interval_minutes':0}):
            with self.assertRaises(ValueError): self.store.save_settings(values)

    def test_next_run_handles_dst_gap(self):
        self.store.save_settings({'auto_send_enabled':True,'schedule_timezone':'America/New_York','hour':2,'minute':30})
        before=datetime(2026,3,8,0,0,tzinfo=ZoneInfo('America/New_York')).timestamp()
        upcoming=outreach_scheduler.next_run(self.store.settings(),before)
        self.assertEqual((upcoming.hour,upcoming.minute),(3,0))

    def test_configurable_sync_interval(self):
        self.store.save_settings({'auto_sync_enabled':True,'sync_interval_minutes':10})
        self.store.set('scheduler_sync_attempt',1000)
        with patch('core.pipeline.sync_local_leads') as sync:
            outreach_scheduler.tick(self.store,1599)
            sync.assert_not_called()
            outreach_scheduler.tick(self.store,1600)
            sync.assert_called_once()


if __name__=='__main__': unittest.main()
