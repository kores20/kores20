"""Small, self-contained HTTP application. Run: python -m app.server."""
import base64
import csv
import hmac
import io
import itertools
import json
import os
import re
import sqlite3
import threading
import time
import traceback
import uuid
import zipfile
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .schema import DEFAULTS, FORMS, WORKS, QUESTIONS, PATTERNS, new_order, missing, selected_forms
from .pdf_engine import generate, ASSETS, TEMPLATES, template_path
from .storage import open_db

ROOT=Path(__file__).parent
DATA=Path(os.getenv('DATA_DIR',str(ROOT.parent/'.data')));DATA.mkdir(parents=True,exist_ok=True)
STATIC=ROOT/'static';JOBS={};JOBS_LOCK=threading.Lock();PDF_LOCK=threading.Lock()
TEMPLATE_CODES=set(FORMS)|{'04','06','11','12','17','23','28','OH-13','OH-14'}
TEMPLATE_CODES={c for c in TEMPLATE_CODES if not c.startswith('06-')}

def migration_archive():
    orders,settings=state();buf=io.BytesIO()
    with zipfile.ZipFile(buf,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('orders-backup.json',json.dumps({'orders':orders,'settings':settings},ensure_ascii=False))
        for code in sorted(TEMPLATE_CODES):
            p=TEMPLATES/(code+'.pdf')
            if p.is_file():z.write(p,'templates/'+p.name)
            else:
                with db() as c:row=c.execute('SELECT pdf FROM templates WHERE code=?',(code,)).fetchone()
                if row:z.writestr('templates/'+p.name,bytes(row[0]))
    return buf.getvalue()

def restore_migration(body):
    raw=base64.b64decode(body.get('base64',''),validate=True)
    if len(raw)>20_000_000:raise ValueError('حجم حزمة النقل أكبر من 20 ميغابايت')
    import fitz
    payloads={};backup=None
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        if len(z.infolist())>300:raise ValueError('عدد ملفات الحزمة أكبر من المسموح')
        total=0
        for info in z.infolist():
            name=Path(info.filename).name
            if info.is_dir():continue
            if name=='orders-backup.json':
                if info.file_size>4_000_000:raise ValueError('نسخة البيانات أكبر من المسموح')
                backup=json.loads(z.read(info));continue
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
    with PDF_LOCK:
        # The database, not the web server's temporary filesystem, owns cloud data.
        with db() as c:
            for code,pdf in payloads.items():c.execute('INSERT INTO templates(code,pdf) VALUES(?,?) ON CONFLICT(code) DO UPDATE SET pdf=excluded.pdf',(code,pdf))
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

def state():
    with db() as c:
        rows=[json.loads(r[0]) for r in c.execute('SELECT data FROM orders ORDER BY updated_at DESC, uid DESC')]
        settings=c.execute('SELECT data FROM settings WHERE id=1').fetchone()
    return rows,json.loads(settings[0]) if settings else DEFAULTS

def validate(o):
    if not isinstance(o,dict):raise ValueError('بيانات الأمر غير صالحة')
    if o.get('id') and not re.fullmatch(r'\d{9}',str(o['id'])):raise ValueError('رقم أمر العمل يجب أن يتكون من 9 أرقام')
    for k in ['date','date190','date200']:
        if o.get(k):date.fromisoformat(o[k])
    if any(w not in WORKS for w in o.get('works',[])):raise ValueError('نوع العمل غير معروف')
    if len(o.get('meters',[]))>100:raise ValueError('الحد 100 عداد لكل أمر')
    for vals in o.get('assessments',{}).values():
        if not isinstance(vals,list) or any(x not in ['',None,'نعم','لا','لا ينطبق'] for x in vals):raise ValueError('تقييم غير صالح')
    if not re.fullmatch(r'[a-zA-Z0-9-]{1,80}',o.get('uid','')):raise ValueError('معرّف المسودة غير صالح')
    return o

def put_orders(orders):
    if len(orders)>500:raise ValueError('الحد 500 أمر في العملية الواحدة')
    for o in orders:validate(o)
    with db() as c:
        for o in orders:c.execute('INSERT INTO orders(uid,data,updated_at) VALUES (?,?,?) ON CONFLICT(uid) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at',(o['uid'],json.dumps(o,ensure_ascii=False),datetime.now().isoformat()))

def import_table(body):
    _,settings=state();year=int(body.get('year',date.today().year))
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
    aliases={'رقم أمر العمل':'id','رقم الأمر':'id','رقم الطلب':'id','order':'id','id':'id','التاريخ':'date','تاريخ التنفيذ':'date','date':'date','رمز العمل':'workCode','نوع العمل':'workCode','workCode':'workCode','رمز الاستشاري':'consultantCode','consultantCode':'consultantCode','المدينة':'city','city':'city','الحي':'site','الموقع':'site','site':'site','المقاول':'contractor','contractor':'contractor','ممثل المقاول':'representative','representative':'representative','رقم المغذي':'feeder','feeder':'feeder','رقم التصريح':'permit','permit':'permit','وصف العمل':'description','description':'description'}
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
                    blobs.append((o['id'],generate(o)))
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
        if not self.authorized():return
        url=urlparse(self.path);path=unquote(url.path)
        try:
            if path=='/api/state':
                orders,settings=state();self.reply(200,{'orders':orders,'settings':settings});return
            if path=='/api/schema':
                self.reply(200,{'forms':FORMS,'works':WORKS,'questions':QUESTIONS,'defaults':DEFAULTS,'patterns':PATTERNS});return
            if path=='/api/new':
                _,settings=state();o=new_order(settings);o['uid']=uuid.uuid4().hex;self.reply(200,o);return
            if path=='/api/template-status':
                with db() as c:stored={r[0] for r in c.execute('SELECT code FROM templates')}
                available={c for c in TEMPLATE_CODES if (TEMPLATES/(c+'.pdf')).is_file()}|stored
                self.reply(200,{'available':sorted(available),'missing':[c for c in ['01','inspection','22','03','02','26','24','25','16'] if c not in available]});return
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
                sample='رقم أمر العمل,رمز الاستشاري,رمز العمل,المدينة,الحي,المقاول,تاريخ التنفيذ,ممثل المقاول,رقم المغذي\n264000001,912,801,أبها,المروج,أفق البنية,2026-10-21,صدام محمد,000609\n'
                self.reply(200,b'\xef\xbb\xbf'+sample.encode(),'text/csv; charset=utf-8',{'Content-Disposition':'attachment; filename="orders-template.csv"'});return
            file=STATIC/('index.html' if path=='/' else path.lstrip('/'))
            if STATIC.resolve() not in file.resolve().parents or not file.is_file():self.reply(404,{'error':'غير موجود'});return
            mime={'.html':'text/html; charset=utf-8','.css':'text/css; charset=utf-8','.js':'text/javascript; charset=utf-8'}.get(file.suffix,'application/octet-stream')
            self.reply(200,file.read_bytes(),mime)
        except (ValueError,FileNotFoundError) as e:self.reply(400,{'error':str(e)})
    def do_POST(self):
        if not self.authorized():return
        origin=self.headers.get('Origin')
        if origin and urlparse(origin).netloc!=self.headers.get('Host'):self.reply(403,{'error':'مصدر الطلب غير مسموح'});return
        try:
            length=int(self.headers.get('Content-Length',0))
            if length>30_000_000:self.reply(413,{'error':'الحد الأقصى للطلب 30 ميغابايت'});return
            body=json.loads(self.rfile.read(length) or '{}');path=urlparse(self.path).path
            if path=='/api/migration':self.reply(200,restore_migration(body));return
            if path=='/api/save':
                put_orders(body.get('orders',[]))
                if 'settings' in body:
                    with db() as c:c.execute('INSERT INTO settings(id,data) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data',(json.dumps(body['settings'],ensure_ascii=False),))
                self.reply(200,{'saved':True});return
            if path=='/api/delete':
                with db() as c:c.execute('DELETE FROM orders WHERE uid=?',(body['uid'],))
                self.reply(200,{'deleted':True});return
            if path=='/api/import':
                orders=import_table(body);put_orders(orders);self.reply(200,{'orders':orders});return
            if path=='/api/missing':self.reply(200,{'questions':missing(body['order']),'forms':selected_forms(body['order'])});return
            if path=='/api/export':self.reply(202,{'job':start_export(body['orders'],body.get('format','zip'))});return
            if path=='/api/preview':
                o=body['order'];validate(o);form=body.get('form')
                if form and form not in selected_forms(o):raise ValueError('هذا النموذج ليس ضمن أعمال الأمر')
                with PDF_LOCK:raw=generate(o,form)
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
