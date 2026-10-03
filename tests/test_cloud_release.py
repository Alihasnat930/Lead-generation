import base64
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from core import auth, cloud_runtime
from core.config import config, hash_app_password, verify_app_password
from core.supabase_state import SupabaseState
from core.outreach_store import OutreachStore
from core.outreach_drafts import draft


class CloudSnapshotTests(unittest.TestCase):
    def test_snapshot_includes_wal_and_restores_all_committed_records(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'source.db'
            restored=Path(folder)/'restored.db'
            connection=sqlite3.connect(source)
            self.addCleanup(connection.close)
            connection.execute('PRAGMA journal_mode=WAL')
            connection.execute('CREATE TABLE evidence(value TEXT)')
            connection.execute("INSERT INTO evidence VALUES('committed in WAL')")
            connection.commit()
            client=Mock()
            client.rpc.return_value.execute.return_value.data=True
            with patch('core.supabase_state.get_client',return_value=client):
                state=SupabaseState()
                state.upload_file('outreach',source,lease_name='workspace',owner='owner')
                params=client.rpc.call_args.args[1]
                self.assertEqual(params['lease_owner'],'owner')
                client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data=[{'payload_base64':params['payload']}]
                self.assertTrue(state.download_file('outreach',restored))
            result=sqlite3.connect(restored)
            try:
                self.assertEqual(result.execute('SELECT value FROM evidence').fetchone()[0],'committed in WAL')
            finally:
                result.close()
                connection.close()

    def test_stale_owner_cannot_publish_a_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'db.sqlite'
            db=sqlite3.connect(path)
            db.execute('CREATE TABLE sample(id INTEGER)')
            db.close()
            client=Mock()
            client.rpc.return_value.execute.return_value.data=False
            with patch('core.supabase_state.get_client',return_value=client):
                with self.assertRaisesRegex(ValueError,'no longer owns'):
                    SupabaseState().upload_file('state',path,lease_name='workspace',owner='stale')

    def test_corrupt_cloud_data_does_not_replace_local_database(self):
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'db.sqlite'
            target.write_bytes(b'original remains')
            client=Mock()
            client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data=[{'payload_base64':base64.b64encode(b'not a database').decode()}]
            with patch('core.supabase_state.get_client',return_value=client):
                with self.assertRaises(ValueError): SupabaseState().download_file('state',target)
            self.assertEqual(target.read_bytes(),b'original remains')

    def test_lost_cloud_lease_blocks_future_checkpoints(self):
        state=Mock()
        runtime=cloud_runtime.CloudRuntime(state)
        runtime.owner='owner'
        runtime.deadline=time.monotonic()+60
        state.renew_lease.side_effect=OSError('offline')
        with self.assertRaises(ValueError): runtime.checkpoint()
        state.upload_file.assert_not_called()
        with self.assertRaises(ValueError): runtime.guard()


class AuthTests(unittest.TestCase):
    def setUp(self): auth._failures.clear()
    def tearDown(self): auth._failures.clear()

    def test_salted_password_and_invalid_hashes(self):
        first=hash_app_password('fixture-long-password')
        self.assertNotEqual(first,hash_app_password('fixture-long-password'))
        with patch.object(config,'APP_LOGIN_PASSWORD_HASH',first):
            self.assertTrue(verify_app_password('fixture-long-password'))
            self.assertFalse(verify_app_password('wrong'))
        with patch.object(config,'APP_LOGIN_PASSWORD_HASH','pbkdf2_sha256$bad'):
            self.assertFalse(verify_app_password('anything'))

    def test_login_throttle_and_expiry(self):
        with patch('core.auth.verify_app_password',return_value=False) as verify:
            for i in range(5): self.assertFalse(auth.authenticate('bad',now=i)[0])
            self.assertIn('Wait',auth.authenticate('bad',now=5)[1])
            self.assertEqual(verify.call_count,5)
            auth.authenticate('bad',now=65)
            self.assertEqual(verify.call_count,6)

    def test_cloud_entrypoint_stops_before_data_without_password(self):
        from streamlit.testing.v1 import AppTest
        with patch.object(config,'APP_AUTH_MODE','legacy'), patch.object(config,'CLOUD_MODE',True), patch.object(config,'APP_LOGIN_PASSWORD_HASH',''), patch('core.store.Store') as store:
            app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=30).run()
            self.assertEqual(len(app.exception),0)
            self.assertTrue(any('APP_LOGIN_PASSWORD_HASH' in e.value for e in app.error))
            store.assert_not_called()

    def test_password_login_opens_full_workspace_and_logout_closes_it(self):
        from streamlit.testing.v1 import AppTest
        from core.store import Store
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'campaign.db')
            with patch.object(config,'APP_AUTH_MODE','legacy'), patch.object(config,'APP_LOGIN_PASSWORD_HASH',hash_app_password('fixture-password')), patch.object(config,'OUTREACH_DATABASE_PATH',str(Path(folder)/'outreach.db')), patch('core.store.Store',return_value=store):
                app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=30).run()
                self.assertEqual(len(app.sidebar.radio),0)
                app.text_input[0].set_value('fixture-password')
                next(b for b in app.button if b.label=='Sign in').click().run()
                self.assertEqual(len(app.exception),0)
                self.assertIn('Outreach & CRM',app.sidebar.radio[0].options)
                next(b for b in app.button if b.label=='Sign out').click().run()
                self.assertEqual(len(app.sidebar.radio),0)
                self.assertEqual(len(app.exception),0)


class DeliveryReservationTests(unittest.TestCase):
    def test_concurrent_claims_reserve_only_once(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(config,'GMAIL_ADDRESS','fixture@gmail.com'):
            store=OutreachStore(Path(folder)/'outreach.db')
            lead={'domain':'fixture.example','email':'hello@fixture.example'}
            content=draft(lead,'Fixture')
            with ThreadPoolExecutor(max_workers=4) as pool:
                results=list(pool.map(lambda _:store.reserve(lead,0,content),range(4)))
            self.assertEqual(sum(r is not None for r in results),1)

    def test_transient_retry_has_backoff_stable_id_and_maximum_three_attempts(self):
        with tempfile.TemporaryDirectory() as folder:
            store=OutreachStore(Path(folder)/'outreach.db')
            lead={'domain':'fixture.example','email':'hello@fixture.example'}
            content=draft(lead,'Fixture')
            record=store.reserve(lead,0,content,now=1000)
            failure={'state':'failed','reason':'Temporary SMTP rejection','retryable':True}
            store.record_failure(record['id'],failure,now=1000)
            self.assertIsNone(store.reserve(lead,0,content,now=1299))
            second=store.reserve(lead,0,content,now=1300)
            self.assertEqual(second['message_id'],record['message_id'])
            self.assertEqual(second['attempts'],2)
            store.record_failure(record['id'],failure,now=1300)
            self.assertIsNone(store.reserve(lead,0,content,now=1899))
            third=store.reserve(lead,0,content,now=1900)
            self.assertEqual(third['attempts'],3)
            store.record_failure(record['id'],failure,now=1900)
            self.assertIsNone(store.reserve(lead,0,content,now=10000))

    def test_permanent_rejection_cannot_be_retried(self):
        with tempfile.TemporaryDirectory() as folder:
            store=OutreachStore(Path(folder)/'outreach.db')
            lead={'domain':'fixture.example','email':'hello@fixture.example'}
            content=draft(lead,'Fixture')
            record=store.reserve(lead,0,content,now=1000)
            store.record_failure(record['id'],{'state':'failed','reason':'Recipient does not exist','retryable':False},now=1000)
            self.assertIsNone(store.reserve(lead,0,content,now=100000))


if __name__=='__main__': unittest.main()
