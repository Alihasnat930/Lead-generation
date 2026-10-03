from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from streamlit.testing.v1 import AppTest

from core import accounts
from core.config import config
from core.outreach_store import OutreachStore

APP=str(Path(__file__).resolve().parents[1]/'app.py')
ADMIN={'email':'owner@example.com','subject':'owner-id','verified':True,'provider':'email'}
MEMBER={'email':'member@example.com','subject':'member-id','verified':True,'provider':'email'}


class AccountUITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.patches=[patch.object(config,'APP_AUTH_MODE','accounts'),patch.object(config,'APP_ADMIN_EMAILS','owner@example.com'),
            patch.object(config,'OUTREACH_DATABASE_PATH',str(Path(self.temp.name)/'outreach.db')),
            patch.object(config,'DATABASE_PATH',str(Path(self.temp.name)/'campaign.db')),
            patch('core.integrations.sync_google_secrets',return_value=False),
            patch('core.login_ui.google_available',return_value=False),
            patch('requests.post',side_effect=AssertionError('Real network writes are forbidden in tests'))]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def test_anonymous_sees_login_signup_but_no_workspace(self):
        with patch('core.store.Store') as business_store:
            app=AppTest.from_file(APP,default_timeout=30).run()
            self.assertEqual(len(app.exception),0)
            self.assertEqual([t.label for t in app.tabs],['Sign in','Create account'])
            self.assertEqual(len(app.sidebar.radio),0)
            business_store.assert_not_called()

    def test_pending_and_revoked_accounts_cannot_access_workspace(self):
        with patch('core.accounts.verify_session',return_value=MEMBER):
            app=AppTest.from_file(APP,default_timeout=30)
            app.session_state['account_session']={'fixture':'session'}
            app.run()
            self.assertEqual(len(app.exception),0)
            self.assertEqual(len(app.sidebar.radio),0)
            next(b for b in app.button if b.label=='Request access').click().run()
            store=OutreachStore()
            self.assertEqual(store.get('access_members')[MEMBER['email']]['status'],'pending')
            accounts.set_access(ADMIN,MEMBER['email'],'approved',store)
            app.run()
            self.assertEqual(len(app.exception),0)
            self.assertIn('Schedule',app.sidebar.radio[0].options)
            accounts.set_access(ADMIN,MEMBER['email'],'revoked',store)
            app.run()
            self.assertEqual(len(app.sidebar.radio),0)
            self.assertTrue(any('revoked' in w.value for w in app.warning))

    def test_admin_login_and_secret_forms_do_not_echo_saved_tokens(self):
        with patch('core.accounts.sign_in',return_value={'fixture':'session'}) as login,patch('core.accounts.verify_session',return_value=ADMIN):
            app=AppTest.from_file(APP,default_timeout=30).run()
            next(t for t in app.text_input if t.key=='signin_email').set_value(ADMIN['email'])
            next(t for t in app.text_input if t.key=='signin_password').set_value('fixture-password')
            next(b for b in app.button if b.label=='Sign in').click().run()
            self.assertEqual(len(app.exception),0)
            login.assert_called_once()
            store=OutreachStore()
            store.set('integrations',{'TWILIO_AUTH_TOKEN':'private-fixture-token'})
            app.sidebar.radio[0].set_value('Settings').run()
            self.assertEqual(len(app.exception),0)
            self.assertEqual(next(t for t in app.text_input if t.key=='setting_TWILIO_AUTH_TOKEN').value,'')
            self.assertTrue(any(t.label=='User access' for t in app.tabs))


if __name__=='__main__': unittest.main()
