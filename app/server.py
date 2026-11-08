"""Small, self-contained HTTP application. Run: python -m app.server."""
import base64
import csv
import hmac
import io
import itertools
import json
import os
import re
import threading
import time
import traceback
import uuid
import zipfile
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .schema import DEFAULTS, FORMS, WORKS, QUESTIONS, LV_QUESTIONS, PATTERNS, new_order, missing, selected_forms, lv_checks
from .pdf_engine import generate, TEMPLATES, template_path, describe_form, form_image, example_order
from .storage import open_db
from .form_settings import visibility, form_config, check_override, DEFAULT_VISIBILITY, custom_inputs
from .form_editor import validate_config
from .signing import KINDS, inspect_asset, signing_config, validate_signing_config, signature_asset_kind, same_owner
from .consultants import CONSULTANTS

ROOT=Path(__file__).parent
DATA=Path(os.getenv('DATA_DIR',str(ROOT.parent/'.data')));DATA.mkdir(parents=True,exist_ok=True)
STATIC=ROOT/'static';JOBS={};JOBS_LOCK=threading.Lock();PDF_LOCK=threading.Lock()
TEMPLATE_CODES=set(FORMS)|{'04','06','11','12','17','23','28','OH-13','OH-14'}
TEMPLATE_CODES={c for c in TEMPLATE_CODES if not c.startswith('06-')}

def migration_archive():
    orders,settings=state();buf=io.BytesIO()
    with zipfile.ZipFile(buf,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('orders-backup.json',json.dumps({'orders':orders,'settings':settings},ensure_ascii=False))
        for kind,asset in signing_assets().items():
            suffix='png' if asset['metadata']['mime']=='image/png' else 'jpg'
            z.writestr('signing/'+kind+'.'+suffix,asset['raw'])
        for code in sorted(TEMPLATE_CODES):
            p=TEMPLATES/(code+'.pdf')
            if p.is_file():z.write(p,'templates/'+p.name)
            else:
                with db() as c:row=c.execute('SELECT pdf FROM templates WHERE code=?',(code,)).fetchone()
                if row:z.writestr('templates/'+p.name,bytes(row[0]))
    return buf.getvalue()

def restore_migration(body):
    raw=base64.b64decode(body.get('base64',''),validate=True)
    if len(raw)>30_000_000:raise ValueError('حجم حزمة النقل أكبر من 30 ميغابايت')
    import fitz
    payloads={};backup=None;asset_payloads={}
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        if len(z.infolist())>300:raise ValueError('عدد ملفات الحزمة أكبر من المسموح')
        total=0
        for info in z.infolist():
            name=Path(info.filename).name
            if info.is_dir():continue
            if name=='orders-backup.json':
                if info.file_size>4_000_000:raise ValueError('نسخة البيانات أكبر من المسموح')
                backup=json.loads(z.read(info));continue
            if info.filename in {f'signing/{kind}.{suffix}' for kind in KINDS for suffix in ['png','jpg']}:
                if info.file_size>6_000_000:raise ValueError('صورة التوقيع أو الختم أكبر من المسموح')
                kind=name.rsplit('.',1)[0];raw_asset=z.read(info)
                asset_payloads[kind]=(raw_asset,inspect_asset(raw_asset,kind));continue
            code=name.removesuffix('.pdf')
            if code not in TEMPLATE_CODES or not name.endswith('.pdf'):continue
            total+=info.file_size
            if total>40_000_000 or info.file_size>4_000_000:raise ValueError('حجم النماذج أكبر من المسموح')
            pdf=z.read(info)
            with fitz.open(stream=pdf,filetype='pdf') as doc:
                if len(doc)!=1:raise ValueError('كل نموذج يجب أن يكون صفحة واحدة: '+name)
                rect=doc[0].rect
                expected=(792,612) if code in ['25','04'] else (842,595) if code=='LV' else (612,792)
                if doc[0].rotation or abs(rect.width-expected[0])>2 or abs(rect.height-expected[1])>2:
                    raise ValueError('مقاس أو اتجاه النموذج غير مطابق للنسخة المعتمدة: '+name)
            payloads[code]=pdf
    if not payloads:raise ValueError('لم نجد النماذج داخل الحزمة. استخدم زر نقل الموقع من نسختك المحلية.')
    existing,_=state()
    if backup and existing:raise ValueError('الموقع يحتوي أوامر بالفعل؛ لن نستبدلها بحزمة نقل قديمة. استخدم استيراد نسخة البيانات عند الحاجة.')
    if backup:
        for o in backup.get('orders',[]):validate(o)
        if isinstance(backup.get('settings'),dict):validate_settings(backup['settings'])
    with PDF_LOCK:
        # The database, not the web server's temporary filesystem, owns cloud data.
        with db() as c:
            for code,pdf in payloads.items():c.execute('INSERT INTO templates(code,pdf) VALUES(?,?) ON CONFLICT(code) DO UPDATE SET pdf=excluded.pdf',(code,pdf))
            for kind,(raw_asset,metadata) in asset_payloads.items():
                c.execute('INSERT INTO signing_assets(kind,data,metadata) VALUES(?,?,?) ON CONFLICT(kind) DO UPDATE SET data=excluded.data,metadata=excluded.metadata',(kind,raw_asset,json.dumps(metadata)))
        TEMPLATES.mkdir(parents=True,exist_ok=True)
        for code,pdf in payloads.items():
            dest=TEMPLATES/(code+'.pdf');temporary=dest.with_suffix('.pending');temporary.write_bytes(pdf);temporary.replace(dest)
        if backup:
            put_orders(backup.get('orders',[]))
            if isinstance(backup.get('settings'),dict):
                with db() as c:c.execute('INSERT INTO settings(id,data) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data',(json.dumps(backup['settings'],ensure_ascii=False),))
    return {'templates':len(payloads),'orders':len(backup.get('orders',[])) if backup else 0}

def db():
    return open_db(DATA)

def signing_assets():
    with db() as c:rows=c.execute('SELECT kind,data,metadata FROM signing_assets').fetchall()
    return {kind:{'raw':bytes(raw),'metadata':json.loads(metadata)} for kind,raw,metadata in rows}

def signing_status():
    settings=get_settings()
    with db() as c:assets={kind:json.loads(metadata) for kind,metadata in c.execute('SELECT kind,metadata FROM signing_assets')}
    return {'config':signing_config(settings),'assets':{kind:assets.get(kind) for kind in sorted(KINDS)},
            'consultants':[dict(c,assetKind=signature_asset_kind(c['name'],assets,settings)) for c in CONSULTANTS]}

def store_signing_asset(kind,raw):
    metadata=inspect_asset(raw,kind)
    with db() as c:c.execute('INSERT INTO signing_assets(kind,data,metadata) VALUES(?,?,?) ON CONFLICT(kind) DO UPDATE SET data=excluded.data,metadata=excluded.metadata',(kind,raw,json.dumps(metadata)))
    return metadata

def delete_signing_asset(kind):
    if kind not in KINDS:raise ValueError('صورة غير معروفة')
    with db() as c:
        # Deleting a consultant also removes their old upload, so it cannot reappear.
        owner=signing_config(get_settings(c))['owner']
        legacy=any(profile['signatureKind']==kind and same_owner(profile['name'],owner) for profile in CONSULTANTS)
        c.execute('DELETE FROM signing_assets WHERE kind=? OR kind=?',(kind,'signature' if legacy else kind))

def save_signing_config(config):
    clean=validate_signing_config(config)
    with db() as c:
        c.execute('INSERT INTO settings(id,data) VALUES(1,?) ON CONFLICT(id) DO NOTHING',(json.dumps(DEFAULTS,ensure_ascii=False),))
        row=c.execute('SELECT data FROM settings WHERE id=1'+(' FOR UPDATE' if c.postgres else '')).fetchone()
        settings=json.loads(row[0]);settings['signing']=clean
        c.execute('UPDATE settings SET data=? WHERE id=1',(json.dumps(settings,ensure_ascii=False),))
    return settings

def state():
    with db() as c:
        rows=[json.loads(r[0]) for r in c.execute('SELECT data FROM orders ORDER BY updated_at DESC, uid DESC')]
        settings=c.execute('SELECT data FROM settings WHERE id=1').fetchone()
    return rows,json.loads(settings[0]) if settings else DEFAULTS

def get_settings(connection=None):
    if connection is None:
        with db() as c:return get_settings(c)
    row=connection.execute('SELECT data FROM settings WHERE id=1').fetchone()
    return json.loads(row[0]) if row else DEFAULTS

def validate(o):
    if not isinstance(o,dict):raise ValueError('بيانات الأمر غير صالحة')
    if o.get('id') and not re.fullmatch(r'\d{9}',str(o['id'])):raise ValueError('رقم أمر العمل يجب أن يتكون من 9 أرقام')
    for k in ['date']:
        if o.get(k):date.fromisoformat(o[k])
    if any(w not in WORKS for w in o.get('works',[])):raise ValueError('نوع العمل غير معروف')
    if len(o.get('meters',[]))>100:raise ValueError('الحد 100 عداد لكل أمر')
    for value in [o.get('voltage','')]+[m.get('voltage','') for m in o.get('meters',[])]:
        if str(value or '') not in ['','230','400']:raise ValueError('جهد المنخفض يجب أن يكون 230 أو 400 فولت')
    for vals in o.get('assessments',{}).values():
        if not isinstance(vals,list) or any(x not in ['',None,'نعم','لا','لا ينطبق'] for x in vals):raise ValueError('تقييم غير صالح')
    validate_custom_values(o.get('customFields',{}))
    if not re.fullmatch(r'[a-zA-Z0-9-]{1,80}',o.get('uid','')):raise ValueError('معرّف المسودة غير صالح')
    return o

def validate_custom_values(values):
    if not isinstance(values,dict) or any(c not in FORMS for c in values):raise ValueError('بيانات الخانات الإضافية غير صالحة')
    for fields in values.values():
        if not isinstance(fields,dict) or len(fields)>400:raise ValueError('عدد الخانات الإضافية أكبر من المسموح')
        for key,value in fields.items():
            if not re.fullmatch(r'custom-[a-zA-Z0-9-]{1,70}',key) or not isinstance(value,str) or len(value)>500:raise ValueError('قيمة الخانة الإضافية غير صالحة أو أطول من المسموح')

def save_form_config(code,config):
    if code not in FORMS:raise ValueError('نموذج غير معروف')
    specs=describe_form(code)['fields'] if config.get('fields') or config.get('checks') or config.get('images') else None
    clean=validate_config(code,config,specs)
    with db() as c:
        c.execute('INSERT INTO settings(id,data) VALUES(1,?) ON CONFLICT(id) DO NOTHING',(json.dumps(DEFAULTS,ensure_ascii=False),))
        row=c.execute('SELECT data FROM settings WHERE id=1'+(' FOR UPDATE' if c.postgres else '')).fetchone()
        settings=json.loads(row[0])
        settings.setdefault('formConfigs',{})[code]=clean
        c.execute('INSERT INTO settings(id,data) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data',(json.dumps(settings,ensure_ascii=False),))
    return settings

def validate_settings(settings):
    if not isinstance(settings,dict):raise ValueError('الثوابت غير صالحة')
    configs=settings.get('formConfigs',{})
    if not isinstance(configs,dict) or any(c not in FORMS for c in configs):raise ValueError('إعدادات النماذج غير صالحة')
    for code,config in configs.items():validate_config(code,config)
    if 'signing' in settings:validate_signing_config(settings['signing'])
    return settings

def order_status(o,settings):
    fixed={}
    for code in selected_forms(o,settings):
        config=form_config(settings,code)
        for key in config.get('checks',{}):
            value=check_override(config,key,o)
            if value is not None:fixed[key]=value
    return {'questions':missing(o,settings),'forms':selected_forms(o,settings),
            'lvChecks':lv_checks(o,settings),'fixedChecks':fixed,'customInputs':custom_inputs(o,settings)}

def put_orders(orders,connection=None):
    if len(orders)>500:raise ValueError('الحد 500 أمر في العملية الواحدة')
    for o in orders:validate(o)
    if connection is None:
        with db() as c:return put_orders(orders,c)
    if orders:connection.executemany('INSERT INTO orders(uid,data,updated_at) VALUES (?,?,?) ON CONFLICT(uid) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at',[(o['uid'],json.dumps(o,ensure_ascii=False),datetime.now().isoformat()) for o in orders])

def save_changes(body):
    """Save a batch and calculate its missing data in the same request."""
    if 'settings' in body:validate_settings(body['settings'])
    items=body.get('orders',[])
    with db() as c:
        put_orders(items,c)
        if 'settings' in body:
            c.execute('INSERT INTO settings(id,data) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data',(json.dumps(body['settings'],ensure_ascii=False),))
            settings=body['settings']
        else:settings=get_settings(c)
    return {'saved':True,'statuses':{o['uid']:order_status(o,settings) for o in items}}

def import_table(body):
    settings=get_settings();year=int(body.get('year',date.today().year))
    if body.get('filename','').lower().endswith('.xlsx'):
        from openpyxl import load_workbook
        data=base64.b64decode(body['base64'],validate=True)
        if len(data)>8_000_000:raise ValueError('حجم الملف أكبر من 8 ميغابايت')
        sheet=load_workbook(io.BytesIO(data),read_only=True,data_only=True).active
        rows=list(itertools.islice(sheet.iter_rows(values_only=True,max_col=100),502))
        if len(rows)>501:raise ValueError('الحد 500 أمر في الاستيراد الواحد؛ قسّم الجدول إلى دفعتين')
    else:
        text=body.get('content','').lstrip('\ufeff');lines=[l for l in text.splitlines() if l.strip() and not re.match(r'^\s*\|?\s*-{2}',l)]
        if not lines:return []
        delimiter='\t' if '\t' in lines[0] else '|' if '|' in lines[0] else ','
        rows=list(csv.reader(lines,delimiter=delimiter))
        rows=[[v.strip() for v in r] for r in rows]
        if delimiter=='|':rows=[[v for i,v in enumerate(r) if not (not v and i in [0,len(r)-1])] for r in rows]
    if len(rows)<2:raise ValueError('أضف صف العناوين ثم بيانات الأوامر')
    aliases={'رقم أمر العمل':'id','رقم الأمر':'id','رقم الطلب':'id','order':'id','id':'id','التاريخ':'date','تاريخ التنفيذ':'date','date':'date','رمز العمل':'workCode','نوع العمل':'workCode','workCode':'workCode','رمز الاستشاري':'consultantCode','consultantCode':'consultantCode','المدينة':'city','city':'city','الحي':'site','الموقع':'site','site':'site','المقاول':'contractor','contractor':'contractor','ممثل الاستشاري':'inspector','المفتش':'inspector','inspector':'inspector','ممثل المقاول':'representative','representative':'representative','رقم المغذي':'feeder','feeder':'feeder','رقم التصريح':'permit','permit':'permit','وصف العمل':'description','description':'description','الجهد':'voltage','الجهد (فولت)':'voltage','جهد المنخفض':'voltage','voltage':'voltage'}
    heads=[aliases.get(str(x or '').strip(),str(x or '').strip()) for x in rows[0]]
    if 'id' not in heads:raise ValueError('أضف عمود «رقم أمر العمل» في الصف الأول')
    result=[]
    for line,row in enumerate(rows[1:],2):
        if not any(x is not None and str(x).strip() for x in row):continue
        o=new_order(settings);o['uid']=uuid.uuid4().hex
        for key,value in zip(heads,row):
            if key not in aliases.values() or value is None:continue
            if key=='date':
                if isinstance(value,(date,datetime)):value=value.strftime('%Y-%m-%d')
                else:
                    value=str(value).strip();parsed=None
                    for fmt in ['%Y-%m-%d','%d/%m/%Y','%d-%m-%Y','%d/%m','%d-%m']:
                        try:
                            dt=datetime.strptime(value,fmt)
                            if '%Y' not in fmt:dt=dt.replace(year=year)
                            parsed=dt.strftime('%Y-%m-%d');break
                        except ValueError:pass
                    if not parsed:raise ValueError(f'تاريخ غير واضح في الصف {line}: {value}')
                    value=parsed
            elif isinstance(value,float) and value.is_integer():value=str(int(value))
            o[key]=str(value).strip()
        code=o.get('workCode')
        o['works']={'801':['meter'],'402':['meter','excavation'],'409':['excavation','joint'],'404':['excavation','termination','transformer','rmu','earthing']}.get(code,[])
        if o['works'] and not o.get('description'):o['description']=' + '.join(WORKS[w] for w in o['works'])
        if not o['works']:o['description']=''
        # A different contractor's representative is never inherited silently.
        if o['contractor']!=settings.get('contractor',DEFAULTS['contractor']) and 'representative' not in heads:o['representative']=''
        validate(o);result.append(o)
    return result

def start_export(orders,format_):
    if not orders or len(orders)>100:raise ValueError('اختر من أمر واحد إلى 100 أمر')
    for o in orders:
        validate(o)
        if not o.get('id'):raise ValueError('رقم الأمر مطلوب قبل التصدير')
    settings=get_settings()
    assets=signing_assets()
    key=uuid.uuid4().hex
    with JOBS_LOCK:
        # Keep recent jobs only; downloads are private to this running instance.
        for k in list(JOBS):
            if time.time()-JOBS[k]['created']>3600:JOBS.pop(k)
        JOBS[key]={'state':'working','done':0,'total':len(orders),'created':time.time()}
    def run():
        try:
            blobs=[]
            with PDF_LOCK:
                for o in orders:
                    blobs.append((o['id'],generate(o,settings=settings,assets=assets)))
                    with JOBS_LOCK:JOBS[key]['done']+=1
            if len(blobs)==1 and format_=='pdf':raw=blobs[0][1];name=blobs[0][0]+'.pdf';mime='application/pdf'
            else:
                buf=io.BytesIO()
                with zipfile.ZipFile(buf,'w',zipfile.ZIP_DEFLATED) as z:
                    for index,(oid,blob) in enumerate(blobs,1):z.writestr(f'{index:02d}-{oid}.pdf',blob)
                raw=buf.getvalue();name='work-orders.zip';mime='application/zip'
            with JOBS_LOCK:JOBS[key].update(state='done',raw=raw,filename=name,mime=mime)
        except Exception:
            traceback.print_exc()
            with JOBS_LOCK:JOBS[key].update(state='error',message='تعذر تجهيز PDF. راجع البيانات وحاول مرة أخرى.')
    threading.Thread(target=run,daemon=True).start()
    return key

class Handler(BaseHTTPRequestHandler):
    def reply(self,status,data,mime='application/json; charset=utf-8',extra=None):
        if not isinstance(data,bytes):data=json.dumps(data,ensure_ascii=False).encode()
        self.send_response(status);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
        for key,value in (extra or {}).items():self.send_header(key,value)
        self.end_headers();self.wfile.write(data)
    def authorized(self):
        password=os.getenv('APP_PASSWORD','')
        if not password:return True
        try:given=base64.b64decode(self.headers.get('Authorization','').split(' ',1)[1]).decode().split(':',1)[1]
        except (IndexError,ValueError,UnicodeError):given=''
        if hmac.compare_digest(given.encode(),password.encode()):return True
        self.reply(401,{'error':'تسجيل الدخول مطلوب'},extra={'WWW-Authenticate':'Basic realm="Forms"'});return False
    def do_GET(self):
        if urlparse(self.path).path=='/health':
            self.reply(200,{'status':'ok','version':os.getenv('RENDER_GIT_COMMIT','local')[:12]});return
        if not self.authorized():return
        url=urlparse(self.path);path=unquote(url.path)
        try:
            if path=='/api/state':
                orders,settings=state();self.reply(200,{'orders':orders,'settings':settings,'statuses':{o['uid']:order_status(o,settings) for o in orders}});return
            if path=='/api/schema':
                self.reply(200,{'forms':FORMS,'works':WORKS,'questions':QUESTIONS,'lvQuestions':LV_QUESTIONS,'defaults':DEFAULTS,'patterns':PATTERNS,'consultants':CONSULTANTS});return
            if path=='/api/new':
                settings=get_settings();o=new_order(settings);o['uid']=uuid.uuid4().hex;self.reply(200,o);return
            if path=='/api/signing':self.reply(200,signing_status());return
            if path=='/api/signing/image':
                kind=parse_qs(url.query).get('kind',[''])[0]
                if kind not in KINDS:raise ValueError('صورة غير معروفة')
                asset=signing_assets().get(kind)
                if not asset:self.reply(404,{'error':'لم ترفع الصورة بعد'});return
                self.reply(200,asset['raw'],asset['metadata']['mime']);return
            if path=='/api/template-status':
                with db() as c:stored={r[0] for r in c.execute('SELECT code FROM templates')}
                available={c for c in TEMPLATE_CODES if (TEMPLATES/(c+'.pdf')).is_file()}|stored
                self.reply(200,{'available':sorted(available),'missing':[c for c in ['01','inspection','22','03','02','26','24','25','16','LV'] if c not in available]});return
            if path=='/api/forms':
                settings=get_settings()
                self.reply(200,{'forms':[{'code':c,'name':name,'visibility':visibility(settings,c),'defaultVisibility':DEFAULT_VISIBILITY[c],'config':form_config(settings,c)} for c,name in FORMS.items()]});return
            if path=='/api/forms/editor':
                code=parse_qs(url.query).get('code',[''])[0]
                if code not in FORMS:raise ValueError('نموذج غير معروف')
                settings=get_settings()
                with PDF_LOCK:metadata=describe_form(code,settings)
                self.reply(200,dict(metadata,code=code,visibility=visibility(settings,code),defaultVisibility=DEFAULT_VISIBILITY[code],config=form_config(settings,code)));return
            if path=='/api/migration':
                self.reply(200,migration_archive(),'application/zip',{'Content-Disposition':'attachment; filename="namothaj-transfer-private.zip"'});return
            if path=='/api/template':
                code=parse_qs(url.query).get('code',[''])[0];code='06' if code.startswith('06-') else code
                if code not in set(FORMS)|{'06'}:raise ValueError('نموذج غير معروف')
                self.reply(200,template_path(code).read_bytes(),'application/pdf');return
            if path.startswith('/api/jobs/'):
                key=path.rsplit('/',1)[1]
                with JOBS_LOCK:j=JOBS.get(key,{}).copy()
                if not j:self.reply(404,{'error':'المهمة غير موجودة'});return
                self.reply(200,{k:v for k,v in j.items() if k!='raw'});return
            if path.startswith('/api/download/'):
                with JOBS_LOCK:j=JOBS.get(path.rsplit('/',1)[1],{}).copy()
                if j.get('state')!='done':self.reply(404,{'error':'الملف غير جاهز'});return
                self.reply(200,j['raw'],j['mime'],{'Content-Disposition':f'attachment; filename="{j["filename"]}"'});return
            if path=='/api/import-template':
                sample='رقم أمر العمل,رمز الاستشاري,رمز العمل,المدينة,الحي,المقاول,تاريخ التنفيذ,ممثل المقاول,رقم المغذي,الجهد\n264000001,912,801,أبها,المروج,أفق البنية,2026-10-21,صدام محمد,000609,230\n'
                self.reply(200,b'\xef\xbb\xbf'+sample.encode(),'text/csv; charset=utf-8',{'Content-Disposition':'attachment; filename="orders-template.csv"'});return
            file=STATIC/('index.html' if path=='/' else path.lstrip('/'))
            if STATIC.resolve() not in file.resolve().parents or not file.is_file():self.reply(404,{'error':'غير موجود'});return
            mime={'.html':'text/html; charset=utf-8','.css':'text/css; charset=utf-8','.js':'text/javascript; charset=utf-8'}.get(file.suffix,'application/octet-stream')
            self.reply(200,file.read_bytes(),mime)
        except (ValueError,FileNotFoundError) as e:self.reply(400,{'error':str(e)})
        except Exception:
            traceback.print_exc();self.reply(500,{'error':'تعذر تحميل البيانات. حاول مرة أخرى بعد قليل.'})
    def do_POST(self):
        if not self.authorized():return
        origin=self.headers.get('Origin')
        if origin and urlparse(origin).netloc!=self.headers.get('Host'):self.reply(403,{'error':'مصدر الطلب غير مسموح'});return
        try:
            length=int(self.headers.get('Content-Length',0))
            if length>42_000_000:self.reply(413,{'error':'الحد الأقصى للطلب 42 ميغابايت'});return
            body=json.loads(self.rfile.read(length) or '{}');path=urlparse(self.path).path
            if path=='/api/signing/upload':
                with PDF_LOCK:store_signing_asset(body['kind'],base64.b64decode(body['base64'],validate=True))
                self.reply(200,signing_status());return
            if path=='/api/signing/config':
                settings=save_signing_config(body['config']);self.reply(200,{'settings':settings,'status':signing_status()});return
            if path=='/api/signing/delete':
                with PDF_LOCK:delete_signing_asset(body.get('kind'))
                self.reply(200,signing_status());return
            if path=='/api/forms/save':
                with PDF_LOCK:settings=save_form_config(body['code'],body['config'])
                self.reply(200,{'saved':True,'settings':settings});return
            if path=='/api/forms/preview':
                code=body['code']
                if code not in FORMS:raise ValueError('نموذج غير معروف')
                settings=get_settings();config=body.get('config',form_config(settings,code))
                if body.get('order') is not None:validate(body['order'])
                with PDF_LOCK:
                    metadata=describe_form(code)
                    validate_config(code,config,metadata['fields'])
                    order=body.get('order') or example_order(settings)
                    if body.get('previewValues'):
                        validate_custom_values({code:body['previewValues']})
                        order.setdefault('customFields',{}).setdefault(code,{}).update(body['previewValues'])
                    raw=form_image(code,config,order,settings,signing_assets())
                self.reply(200,raw,'image/png');return
            if path=='/api/migration':self.reply(200,restore_migration(body));return
            if path=='/api/save':self.reply(200,save_changes(body));return
            if path=='/api/delete':
                with db() as c:c.execute('DELETE FROM orders WHERE uid=?',(body['uid'],))
                self.reply(200,{'deleted':True});return
            if path=='/api/import':
                orders=import_table(body);put_orders(orders);self.reply(200,{'orders':orders});return
            if path=='/api/missing':
                settings=get_settings();self.reply(200,order_status(body['order'],settings));return
            if path=='/api/missing-batch':
                items=body.get('orders',[])
                if not isinstance(items,list) or len(items)>500:raise ValueError('الحد 500 أمر في العملية الواحدة')
                settings=get_settings();self.reply(200,{'statuses':{o['uid']:order_status(o,settings) for o in items}});return
            if path=='/api/export':self.reply(202,{'job':start_export(body['orders'],body.get('format','zip'))});return
            if path=='/api/preview':
                o=body['order'];validate(o);form=body.get('form')
                settings=get_settings()
                if form and form not in selected_forms(o,settings):raise ValueError('هذا النموذج ليس ضمن أعمال الأمر')
                with PDF_LOCK:raw=generate(o,form,settings,signing_assets())
                self.reply(200,raw,'application/pdf');return
            self.reply(404,{'error':'غير موجود'})
        except (ValueError,TypeError,KeyError,IndexError) as e:self.reply(400,{'error':str(e)})
        except Exception:
            traceback.print_exc();self.reply(500,{'error':'تعذر إتمام الطلب. تحقق من البيانات وحاول مرة أخرى.'})

def main():
    host=os.getenv('HOST','127.0.0.1');port=int(os.getenv('PORT','8080'))
    if os.getenv('RENDER') and not os.getenv('APP_PASSWORD'):raise RuntimeError('Set APP_PASSWORD before publishing this website.')
    if os.getenv('RENDER') and not os.getenv('DATABASE_URL'):raise RuntimeError('Set DATABASE_URL for durable data before publishing on Render Free.')
    print(f'Forms app: http://{host}:{port}',flush=True)
    ThreadingHTTPServer((host,port),Handler).serve_forever()

if __name__=='__main__':main()
