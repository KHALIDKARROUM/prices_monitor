"""Disposable real HTTP app for browser journeys; never starts providers/email."""
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from argon2 import PasswordHasher
from price_monitor import app, access_store
from price_monitor.http_wsgi import WSGIServer
from price_monitor.services.alerts import AlertManager

access_store.PASSWORDS = PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1)
for name, role in [('owner','admin'),('editor','editor'),('reader','viewer')]:
    app.database.create_team_user({'username':name,'full_name':name,'role':role,'password':'browser-test-password-2026'})
today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
for offset in range(70, 9, -1):
    day = today - timedelta(days=offset)
    for product, instrument, price, source, second in [
        ('brent','brent_spot',80,'EIA/FRED - DCOILBRENTEU',0),
        ('brent','brent_futures',90,'Yahoo Finance - Brent futures BZ=F',1),
        ('gasoil','diesel_spot',2,'EIA/FRED - DDFUELNYH',2)]:
        app.database.insert_observation({'product':product,'instrument':instrument,'price':price,'source':source,
            'source_date':day.date().isoformat(),'collected_at':(day + timedelta(seconds=second)).isoformat()})
app.database.create_alert_rule({'product':'brent','instrument':'brent_futures','direction':'above','threshold':85})
app.refresh_alert_states = lambda: AlertManager(app.database).evaluate_prices(app.database.latest_by_instrument(), dispatch=False)
app.refresh_alert_states()
app.connectivity = SimpleNamespace(snapshot=lambda:{'status':'online','checked_at':today.isoformat(),'changed_at':today.isoformat()})
app.run_collection = lambda db:{'status':'success','rows':0,'providers':[],'source_comparison':db.source_comparison()}
server = WSGIServer(('127.0.0.1',0), app.Handler)
Path(os.environ['PRICE_MONITOR_TEST_READY']).write_text(str(server.server_port))
try:
    server.serve_forever()
finally:
    server.server_close()
