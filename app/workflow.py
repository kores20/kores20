"""Shared export/delivery tracking kept separately from editable order data."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import re

from .form_settings import configured_forms, form_config
from .signing import signing_config, signature_asset_kind

STAGES={'draft':'مسودة','ready':'جاهز للتصدير','exported':'تم التصدير',
        'delivered':'تم التسليم','needs_export':'عُدّل بعد التصدير'}


def now():return datetime.now(timezone.utc).isoformat()


def fingerprint(order,settings,assets):
    forms=configured_forms(order,settings);signing=signing_config(settings)
    kinds=['stamp'] if signing['stampEnabled'] else []
    if signing['signatureEnabled']:
        kind=signature_asset_kind(order.get('inspector',''),assets,settings)
        if kind:kinds.append(kind)
    versions={kind:(assets.get(kind) or {}).get('metadata',assets.get(kind) or {}).get('version') for kind in kinds}
    payload={'order':order,'forms':forms,'configs':{c:form_config(settings,c) for c in forms},
             'signing':signing,'assets':versions}
    return hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def status(order,settings,record,assets,missing_count):
    stage='draft' if missing_count else 'ready'
    if record.get('exportFingerprint'):
        if fingerprint(order,settings,assets)!=record['exportFingerprint']:stage='needs_export'
        else:stage='delivered' if record.get('deliveredAt') else 'exported'
    return {'stage':stage,'label':STAGES[stage],'exportedAt':record.get('exportedAt',''),
            'deliveredAt':record.get('deliveredAt',''),'canDeliver':stage=='exported'}


def context(connection):
    records={uid:json.loads(data) for uid,data in connection.execute('SELECT uid,data FROM order_progress')}
    assets={kind:json.loads(metadata) for kind,metadata in connection.execute('SELECT kind,metadata FROM signing_assets')}
    return records,assets


def record_exports(connection,orders,settings,assets,started_at):
    # Ignore late completion of an older job, and orders deleted during generation.
    for order in orders:
        uid=order['uid']
        if not connection.execute('SELECT uid FROM orders WHERE uid=?',(uid,)).fetchone():continue
        connection.execute('INSERT INTO order_progress(uid,data) VALUES(?,?) ON CONFLICT(uid) DO NOTHING',(uid,'{}'))
        row=connection.execute('SELECT data FROM order_progress WHERE uid=?'+(' FOR UPDATE' if connection.postgres else ''),(uid,)).fetchone()
        previous=json.loads(row[0])
        if previous.get('exportStartedAt','')>started_at:continue
        record={'exportedAt':now(),'exportStartedAt':started_at,
                'exportFingerprint':fingerprint(order,settings,assets),'deliveredAt':''}
        connection.execute('UPDATE order_progress SET data=? WHERE uid=?',(json.dumps(record),uid))


def validate_backup(records,orders):
    if not isinstance(records,dict) or len(records)>500:raise ValueError('بيانات متابعة الأوامر غير صالحة')
    allowed={o['uid'] for o in orders}
    for uid,record in records.items():
        if uid not in allowed or not isinstance(record,dict):raise ValueError('متابعة أمر غير موجود في النسخة')
        if set(record)-{'exportedAt','exportStartedAt','exportFingerprint','deliveredAt'}:raise ValueError('بيانات متابعة غير معروفة')
        for key,value in record.items():
            if not isinstance(value,str) or len(value)>64:raise ValueError('بيانات متابعة غير صالحة')
            if key=='exportFingerprint':
                if not re.fullmatch(r'[a-f0-9]{64}',value):raise ValueError('بيانات نسخة التصدير غير صالحة')
            elif value:datetime.fromisoformat(value)
    return deepcopy(records)
