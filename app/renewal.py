"""Reviewable PDF renewal: copy original ink into current template fields.

OCR identifies scanned forms and suggests text; it never silently replaces ink.
The original upload is immutable and unknown pages remain available unchanged.
"""
from collections import Counter
from copy import deepcopy
from datetime import datetime
from functools import lru_cache
import io
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import threading
import time
import unicodedata
import uuid

import fitz
from PIL import Image,ImageChops,ImageFilter

from . import pdf_engine as pdf
from .check_layouts import check_specs
from .ocr_setup import DATA as OCR_DATA
from .schema import FORMS,WORKS
from .workflow import now

ROOT=Path(os.getenv('DATA_DIR',str(Path(__file__).resolve().parent.parent/'.data')))/'renewal'
LOCK=threading.RLock();JOBS={};TTL=7200;MAX_PAGES=50;MAX_BYTES=25_000_000;ZOOM=2
DB=None;PDF_LOCK=threading.RLock()
OPTIONS={c:n for c,n in FORMS.items() if c!='06-rmu'}
OPTIONS['06-transformer']='قواعد المحطات — محول أو وحدة حلقية'
LEGACY=json.loads((Path(__file__).parent/'assets'/'legacy-layouts.json').read_text())


def cleanup():
    ROOT.mkdir(parents=True,exist_ok=True)
    with LOCK:
        for key,job in list(JOBS.items()):
            if job['state']!='working' and job['exportState']!='working' and not job.get('editing') and time.time()-job.get('touched',job['created'])>TTL:
                shutil.rmtree(job['directory'],ignore_errors=True);JOBS.pop(key,None)
        for folder in ROOT.iterdir():
            if folder.is_dir() and folder.name not in JOBS and time.time()-folder.stat().st_mtime>TTL:shutil.rmtree(folder,ignore_errors=True)
    if DB:
        cutoff=time.strftime('%Y-%m-%dT%H:%M:%S',time.gmtime(time.time()-TTL))
        with DB() as c:c.execute('DELETE FROM renewal_jobs WHERE updated_at<?',(cutoff,))


def persist(job,source=None):
    if not DB:return
    job['touched']=time.time()
    with LOCK:data=json.dumps({k:v for k,v in job.items() if k not in ['directory','pdfLock']},ensure_ascii=False)
    with DB() as c:
        if source is not None:c.execute('INSERT INTO renewal_jobs(id,data,source,updated_at) VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data,source=excluded.source,updated_at=excluded.updated_at',(job['id'],data,source,now()))
        else:c.execute('UPDATE renewal_jobs SET data=?,updated_at=? WHERE id=?',(data,now(),job['id']))


def get(key):
    if not isinstance(key,str) or not re.fullmatch(r'[a-f0-9]{32}',key):raise ValueError('جلسة تجديد غير صالحة')
    with LOCK:job=JOBS.get(key)
    if job is None and DB:
        with DB() as c:row=c.execute('SELECT data,source,updated_at FROM renewal_jobs WHERE id=?',(key,)).fetchone()
        if row:
            if time.time()-datetime.fromisoformat(row[2]).timestamp()>TTL:
                with DB() as c:c.execute('DELETE FROM renewal_jobs WHERE id=?',(key,))
                raise ValueError('انتهت جلسة التجديد؛ ارفع الملف مرة أخرى')
            job=json.loads(row[0]);directory=ROOT/key;directory.mkdir(parents=True,exist_ok=True)
            temporary=directory/(uuid.uuid4().hex+'.source');temporary.write_bytes(bytes(row[1]));temporary.replace(directory/'source.pdf')
            job.update(directory=directory,pdfLock=PDF_LOCK)
            if job['state']=='working':job['state']='error';job['message']='انقطعت قراءة الملف؛ ارفعه مجددًا. تبقى نسختك الأصلية دون تغيير.'
            if not (directory/'renewed.pdf').is_file():job['exportState']='idle'
            with LOCK:job=JOBS.setdefault(key,job)
    if not job:raise ValueError('انتهت جلسة التجديد؛ ارفع الملف مرة أخرى')
    if job['state']!='working' and job['exportState']!='working' and not job.get('editing') and time.time()-job.get('touched',job['created'])>TTL:
        if DB:
            with DB() as c:c.execute('DELETE FROM renewal_jobs WHERE id=?',(key,))
        with LOCK:JOBS.pop(key,None)
        shutil.rmtree(job['directory'],ignore_errors=True)
        raise ValueError('انتهت جلسة التجديد؛ ارفع الملف مرة أخرى')
    return job


def public(job):
    with LOCK:
        result={k:deepcopy(job[k]) for k in ['id','filename','state','done','total','message','orderId','exportState','exportDone']}
        result['pages']=[{k:deepcopy(v) for k,v in p.items() if k!='fields'} for p in job['pages']]
        return result


def validate_pdf(raw):
    if not raw or len(raw)>MAX_BYTES:raise ValueError('ارفع PDF لا يتجاوز 25 ميغابايت')
    if b'%PDF-' not in raw[:1024]:raise ValueError('الملف المرفوع ليس PDF')
    try:
        with fitz.open(stream=raw,filetype='pdf') as doc:
            if doc.needs_pass:raise ValueError('الملف محمي بكلمة مرور؛ ارفع نسخة قابلة للفتح')
            if not 1<=len(doc)<=MAX_PAGES:raise ValueError('الحد من صفحة واحدة إلى 50 صفحة للملف')
            if any(p.rect.width<40 or p.rect.height<40 or max(p.rect.width,p.rect.height)>4000 for p in doc):raise ValueError('مقاس صفحة في الملف غير مدعوم')
            return len(doc)
    except (fitz.FileDataError,RuntimeError) as e:raise ValueError('تعذر فتح PDF؛ تأكد من سلامة الملف') from e


def render(page,zoom=ZOOM):
    factor=min(zoom,2000/max(page.rect.width,page.rect.height))
    pix=page.get_pixmap(matrix=fitz.Matrix(factor,factor),alpha=False,colorspace=fitz.csRGB)
    return Image.frombytes('RGB',(pix.width,pix.height),pix.samples)


def save_image(image,path):
    pending=path.with_suffix('.pending');image.save(pending,format='PNG');pending.replace(path)


def frame(image):
    small=image.convert('L').resize((612,792))
    ink=small.point(lambda p:255 if p<150 else 0)
    ys=[i for i,v in enumerate(ink.resize((1,792),Image.Resampling.BOX).tobytes()) if v>85]
    xs=[i for i,v in enumerate(ink.resize((612,1),Image.Resampling.BOX).tobytes()) if v>85]
    if not xs or not ys or max(xs)-min(xs)<240 or max(ys)-min(ys)<220:return None
    return [min(xs)/612,min(ys)/792,(max(xs)+1)/612,(max(ys)+1)/792]


def normalized(image,size,target_frame=None):
    source_frame=frame(image)
    if source_frame and target_frame:
        source_box=[round(v*(image.width if i%2==0 else image.height)) for i,v in enumerate(source_frame)]
        target_box=[round(v*(size[0] if i%2==0 else size[1])) for i,v in enumerate(target_frame)]
        sx=(source_box[2]-source_box[0])/(target_box[2]-target_box[0]);sy=(source_box[3]-source_box[1])/(target_box[3]-target_box[1])
        # Align the grid without erasing notes or logos outside the table frame.
        return image.transform(size,Image.Transform.AFFINE,(sx,0,source_box[0]-target_box[0]*sx,0,sy,source_box[1]-target_box[1]*sy),Image.Resampling.BICUBIC,fillcolor='white')
    return image.resize(size,Image.Resampling.BICUBIC)


def deskew(image):
    small=image.convert('L');small.thumbnail((600,850))
    def score(angle):
        rotated=small.rotate(angle,fillcolor=255)
        ink=rotated.point(lambda v:255 if v<150 else 0)
        return sum(v*v for v in ink.resize((1,ink.height),Image.Resampling.BOX).tobytes())+sum(v*v for v in ink.resize((ink.width,1),Image.Resampling.BOX).tobytes())
    baseline=score(0);best=max([(score(a/2),a/2) for a in range(-6,7)],key=lambda p:p[0])
    angle=best[1] if best[0]>baseline*1.15 else 0
    return (image.rotate(angle,fillcolor='white') if angle else image),angle


@lru_cache(maxsize=4)
def template_image(code,path,mtime):
    with fitz.open(path) as doc:return render(doc[0])


def target_image(code):
    path=pdf.template_path('06' if code.startswith('06-') else code)
    if not path.is_file():raise ValueError('النموذج الجديد غير مرفوع في الموقع')
    return template_image(code,str(path),path.stat().st_mtime_ns)


@lru_cache(maxsize=32)
def _version(path,mtime):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def template_version(code):
    path=pdf.template_path('06' if code.startswith('06-') else code)
    return _version(str(path),path.stat().st_mtime_ns)


def check_version(entry):
    if entry.get('code') and entry.get('templateVersion') and entry['templateVersion']!=template_version(entry['code']):raise ValueError('تغيّر النموذج الجديد؛ أعد تطبيق المطابقة ثم راجع الصفحة')


def text_code(text):
    value=unicodedata.normalize('NFKC',text).upper()
    match=re.search(r'(?:FORM\s*[-_:]?\s*SEC|SEC\s*[-_:]?\s*FORM)\s*[-_:]?\s*0?(\d{1,2})(?!\d)',value)
    if match:
        code=match[1].zfill(2);code='06-transformer' if code=='06' else code
        if code in OPTIONS:return code
    compact=re.sub(r'\s+','',value).replace('ـ','')
    if 'LOWVOLTAGE' in compact or 'LVTEST' in compact:return 'LV'
    if 'نموذجالتفتيشعلىموقععمل' in compact or ('تصريح' in value and 'Toolbox' in text):return 'inspection'
    arabic=compact.translate(str.maketrans({'أ':'ا','إ':'ا','آ':'ا','ى':'ي'}))
    if all(word in arabic for word in ['بطاقة','تسليم','الموقع']):return '01'
    for words,code in [('بطاقةتسليمالموقع','01'),('برنامجالعملاليوميللمقاول','02'),('برنامجالعملاليوميللمفتشين','03'),
                       ('تقريرتفتيشالسلامة','22'),('متابعةمخالفاتالموقع','26'),('وصلاتلحام','10'),
                       ('استلامانتهاءاعمالالتركيبات','24'),('ادخالبياناتالعدادات','25'),('تاريضالمحطات','08'),('قواعدالمحطات','06-transformer')]:
        if words in arabic:return code
    return None


def line_signature(image):
    ink=image.convert('L').resize((612,792)).point(lambda v:255 if v<150 else 0)
    return {'rows':[i for i,v in enumerate(ink.resize((1,792)).tobytes()) if v>85],
            'cols':[i for i,v in enumerate(ink.resize((612,1)).tobytes()) if v>85]}


def line_distance(a,b):
    def distance(x,y):
        if len(x)<2 or len(y)<2:return 1
        return sum(min(20,min(abs(i-j) for j in y))/20 for i in x)/len(x)
    return sum(distance(a[k],b[k])+distance(b[k],a[k]) for k in ['rows','cols'])/4


def source_layout(image,code,prefer_legacy=False):
    if code not in LEGACY:return 'current'
    target=target_image(code)
    old=normalized(image,target.size,LEGACY[code]['frame']);new=normalized(image,target.size,frame(target))
    old_score=line_distance(line_signature(old),LEGACY[code])
    new_score=line_distance(line_signature(new),line_signature(target))
    return 'legacy' if old_score+.025<new_score or prefer_legacy and old_score<=new_score+.025 else 'current'


def ocr_text(page):
    if not all((OCR_DATA/(lang+'.traineddata')).is_file() for lang in ['eng','ara']):return ''
    try:return page.get_text(textpage=page.get_textpage_ocr(language='eng+ara',dpi=160,full=True,tessdata=str(OCR_DATA)))
    except (RuntimeError,ValueError):return ''


def identify(page,image):
    native=page.get_text();code=text_code(native)
    if code:
        angle=0
        try:
            template=target_image(code)
            if (image.width>image.height)!=(template.width>template.height) or page.rotation==180:angle=(360-page.rotation)%360
        except (ValueError,FileNotFoundError):pass
        return code,angle,'text',native
    if frame(image) is None:
        text=ocr_text(page);return text_code(text),0,'ocr' if text_code(text) else 'unknown',text or native
    # Compare form grids, including rotated scanned pages, before invoking OCR.
    candidates=[]
    for candidate in OPTIONS:
        try:template=target_image(candidate)
        except (ValueError,FileNotFoundError):continue
        reference=template.convert('L').resize((72,92)).filter(ImageFilter.GaussianBlur(.6))
        for angle in [0,90,180,270]:
            rotated=image.rotate(-angle,expand=True) if angle else image
            if (rotated.width>rotated.height)!=(template.width>template.height):continue
            trial=normalized(rotated,(template.width,template.height),frame(template)).convert('L').resize((72,92)).filter(ImageFilter.GaussianBlur(.6))
            difference=ImageChops.difference(reference,trial)
            score=sum(i*n for i,n in enumerate(difference.histogram()))/(72*92*255)
            candidates.append((score,candidate,angle))
    candidates.sort()
    if candidates and candidates[0][0]<.055 and (len(candidates)<2 or candidates[1][0]-candidates[0][0]>.006):
        return candidates[0][1],candidates[0][2],'grid',native
    legacy=[]
    for candidate,layout in LEGACY.items():
        try:target_image(candidate)
        except (ValueError,FileNotFoundError):continue
        for angle in [0,90,180,270]:
            rotated=image.rotate(-angle,expand=True) if angle else image
            if rotated.width>rotated.height:continue
            trial=normalized(rotated,(1224,1584),layout['frame'])
            legacy.append((line_distance(line_signature(trial),layout),candidate,angle))
    legacy.sort()
    if legacy and legacy[0][0]<.07 and (len(legacy)<2 or legacy[1][0]-legacy[0][0]>.04):return legacy[0][1],legacy[0][2],'legacy-grid',native
    text=ocr_text(page);code=text_code(text)
    if code:return code,0,'ocr',text
    if candidates and candidates[0][0]<.09 and candidates[1][0]-candidates[0][0]>.01:
        return candidates[0][1],candidates[0][2],'grid-review',text
    return None,0,'unknown',text or native


def crop_pixels(box,image):
    return [round(box[0]*ZOOM),round(box[1]*ZOOM),round(box[2]*ZOOM),round(box[3]*ZOOM)]


def patch(image,reference,box,raw=False):
    rect=crop_pixels(box,image);crop=image.crop(rect)
    if raw:return crop
    r,g,b=crop.split();bright=ImageChops.lighter(ImageChops.lighter(r,g),b);dark=ImageChops.darker(ImageChops.darker(r,g),b)
    color=ImageChops.difference(bright,dark).point(lambda v:255 if v>22 else 0)
    if color.histogram()[255]>=6:
        mask=color.filter(ImageFilter.MaxFilter(3))
    else:
        original=crop.convert('L').point(lambda v:255 if v<155 else 0)
        background=reference.crop(rect).convert('L').point(lambda v:255 if v<190 else 0).filter(ImageFilter.MaxFilter(5))
        mask=ImageChops.subtract(original,background)
        if mask.histogram()[255]<6:return None
    if not mask.getbbox():return None
    result=crop.convert('RGBA');result.putalpha(mask);return result


def recover_name(image,field,width,height):
    # Earlier generated files placed footer names on the opposite side of the
    # same name row. Only inspect that row, never a signature column or header.
    if field['kind']!='text' or field['sourceBox'][1]<500 or not re.fullmatch(r'(inspector|representative)(#\d+)?',field['id']):return None
    y0=max(0,field['sourceBox'][1]-4);y1=min(height,field['sourceBox'][3]+4)
    area=[width*.44,y0,width*.92,y1];crop=image.crop(crop_pixels(area,image));r,g,b=crop.split()
    bright=ImageChops.lighter(ImageChops.lighter(r,g),b);dark=ImageChops.darker(ImageChops.darker(r,g),b)
    mask=ImageChops.difference(bright,dark).point(lambda v:255 if v>22 else 0);bounds=mask.getbbox()
    if not bounds or mask.histogram()[255]<12:return None
    return [max(0,area[0]+bounds[0]/ZOOM-1),max(0,y0+bounds[1]/ZOOM-1),
            min(width,area[0]+bounds[2]/ZOOM+1),min(height,y0+bounds[3]/ZOOM+1)]


def _check_regions(code):
    path=pdf.template_path('06' if code.startswith('06-') else code)
    order=pdf.example_order();order['works']=list(WORKS)
    with fitz.open(path) as doc:return {s['id']:s for s in check_specs(code,doc[0],order)}


def _fields(code,settings,layout='current'):
    metadata=pdf.describe_form(code,settings);checks=_check_regions(code);result=[]
    for field in metadata['fields']:
        source=list(field['defaultBox']);destination=list(field['box']);kind=field['kind']
        if kind=='check':
            spec=checks.get(field['id'],{})
            if spec.get('naBox'):
                old=list(source);source=list(fitz.Rect(source)|fitz.Rect(spec['naBox']))
                destination=[source[0]+destination[0]-old[0],source[1]+destination[1]-old[1],source[2]+destination[2]-old[2],source[3]+destination[3]-old[3]]
        result.append({'id':field['id'],'label':field['label'],'kind':kind,'sourceBox':source,'box':destination,
                       'baseBox':list(destination),'fontSize':field.get('fontSize',22),'mode':'blank','text':'','hasInk':False,'raw':False})
        if kind=='check':
            result[-1]['sourcePositions']=deepcopy(checks.get(field['id'],{}).get('positions',{}))
            result[-1]['editableValues']=checks.get(field['id'],{}).get('default') is None
        if layout=='legacy':
            legacy=LEGACY.get(code,{}).get('fields',{}).get(field['id'])
            if legacy:
                result[-1]['sourceBox']=list(legacy)
                if kind=='check':
                    tag=checks[field['id']]['tag'];y=(legacy[1]+legacy[3])/2
                    xs={'inspection':{'نعم':260,'لا':225,'لا ينطبق':190},'26':{'نعم':339,'لا':306},
                        '10-joint':{'نعم':314,'لا':348},'10-termination':{'نعم':314,'لا':348}}.get(tag,{})
                    result[-1]['sourcePositions']={v:[x,y] for v,x in xs.items()}
            else:result[-1]['unmapped']=True
    return metadata,result


def analyze_page(job,index,code=None,rotation=None,layout=None):
    with job['pdfLock']:
        with fitz.open(job['directory']/'source.pdf') as doc:
            page=doc[index];image=render(page);skew=0
            if len(page.get_text().strip())<30:image,skew=deskew(image)
            auto,angle,method,text=identify(page,image) if code is None else (code,rotation or 0,'manual',page.get_text())
            code=auto;rotation=angle if rotation is None else rotation
            if rotation:image=image.rotate(-rotation,expand=True)
            if code:
                try:target=target_image(code)
                except (ValueError,FileNotFoundError):code=None;method='missing-template'
            if code:
                hint=any(p.get('layout')=='legacy' for p in job['pages'])
                layout=source_layout(image,code,hint) if layout is None else layout
                target=target_image(code);image=normalized(image,target.size,LEGACY[code]['frame'] if layout=='legacy' else frame(target))
                meta,fields=_fields(code,job['settings'],layout);width,height=meta['width'],meta['height']
            else:
                width,height=image.width/ZOOM,image.height/ZOOM;fields=[]
            save_image(image,job['directory']/f'source-{index}.png')
            for field in fields:
                ink=patch(image,Image.new('RGB',image.size,'white') if layout=='legacy' else target,field['sourceBox'])
                if ink is None and layout!='legacy':
                    recovered=recover_name(image,field,width,height)
                    if recovered:field['sourceBox']=recovered;ink=patch(image,target,recovered)
                field['hasInk']=ink is not None
                # A different printed signatory must not be mistaken for a new signature.
                field['mode']='image' if field['hasInk'] and field['kind']!='image' and not field.get('unmapped') else 'blank'
                field['review']=field['kind']=='image' or field['hasInk']
            numbers=re.findall(r'(?<!\d)\d{9}(?!\d)',text.translate(str.maketrans('٠١٢٣٤٥٦٧٨٩','0123456789')))
    result={'index':index,'code':code,'name':OPTIONS.get(code,'صفحة غير معروفة — تحفظ كما هي'),
            'width':width,'height':height,'rotation':rotation,'deskew':skew,'layout':layout or 'current','method':method,'fields':fields,'templateVersion':template_version(code) if code else '',
            'confirmed':False,'keepOriginal':not bool(code),'orderId':Counter(numbers).most_common(1)[0][0] if numbers else '',
            'warnings':['التواقيع والأختام لا تنقل تلقائيًا؛ حدد ما تريد نقله وراجع موضعه. يمكنك إضافة خانة نقل لأي كتابة غير مطابقة.'] if code else ['النموذج الجديد غير متوفر في الموقع؛ ارفع حزمة النماذج من «نقل الموقع» أو احتفظ بالصفحة الأصلية.' if method=='missing-template' else 'لم نتعرف على النموذج. اختر نوعه أو احتفظ بالصفحة الأصلية.']}
    with LOCK:
        if index<len(job['pages']):job['pages'][index]=result
        else:job['pages'].append(result)
    return deepcopy(result)


def source_image(job,entry):
    check_version(entry)
    path=job['directory']/f"source-{entry['index']}.png"
    if not path.is_file():
        with job['pdfLock']:
            with fitz.open(job['directory']/'source.pdf') as doc:image=render(doc[entry['index']])
            if entry.get('deskew'):image=image.rotate(entry['deskew'],fillcolor='white')
            if entry['rotation']:image=image.rotate(-entry['rotation'],expand=True)
            if entry['code']:
                target=target_image(entry['code']);image=normalized(image,target.size,LEGACY[entry['code']]['frame'] if entry.get('layout')=='legacy' else frame(target))
            save_image(image,path)
    return path


def start(raw,filename,settings,pdf_lock):
    if not isinstance(filename,str) or not filename.strip() or len(filename)>500:raise ValueError('اسم ملف غير صالح')
    count=validate_pdf(raw);cleanup()
    if DB:
        with DB() as c:used=c.execute('SELECT COALESCE(SUM(LENGTH(source)+LENGTH(data)),0) FROM renewal_jobs').fetchone()[0]
        if used+len(raw)>200_000_000:raise ValueError('مساحة جلسات التجديد ممتلئة مؤقتًا؛ احذف الجلسة المنتهية أو أعد المحاولة لاحقًا')
    with LOCK:
        if any(j['state']=='working' for j in JOBS.values()):raise ValueError('انتظر انتهاء معالجة الملف الحالي ثم ارفع الملف التالي')
        key=uuid.uuid4().hex;directory=ROOT/key;directory.mkdir()
        (directory/'source.pdf').write_bytes(raw)
        job={'id':key,'directory':directory,'filename':Path(filename).name[:150], 'created':time.time(),
             'state':'working','done':0,'total':count,'message':'جارٍ قراءة الصفحات…','pages':[],
             'settings':deepcopy(settings),'pdfLock':pdf_lock,'orderId':(re.search(r'(?<!\d)\d{9}(?!\d)',Path(filename).name).group(0) if re.search(r'(?<!\d)\d{9}(?!\d)',Path(filename).name) else ''),'exportState':'idle','exportDone':0}
        JOBS[key]=job
    try:persist(job,raw)
    except Exception:
        with LOCK:JOBS.pop(key,None)
        shutil.rmtree(directory,ignore_errors=True);raise
    def run():
        try:
            for index in range(count):
                result=analyze_page(job,index)
                with LOCK:job['done']=index+1;job['message']=f'تمت قراءة {index+1} من {count}';job['orderId']=job['orderId'] or result['orderId']
            with LOCK:job['state']='ready';job['message']='راجع نقل البيانات ثم نزّل النماذج الجديدة.'
            persist(job)
        except Exception:
            import traceback;traceback.print_exc()
            with LOCK:job['state']='error';job['message']='تعذرت قراءة الملف. راجع سلامته وحاول مرة أخرى.'
    threading.Thread(target=run,daemon=True).start();return key


def ready(key):
    job=get(key)
    if job['state']!='ready':raise ValueError('انتظر انتهاء قراءة الملف أولًا')
    if job['exportState']=='working':raise ValueError('انتظر انتهاء تجهيز النسخة الجديدة')
    if job.get('editing'):raise ValueError('انتظر انتهاء مطابقة الصفحة')
    return job


def page_at(job,index):
    if not isinstance(index,int) or isinstance(index,bool) or not 0<=index<len(job['pages']):raise ValueError('رقم صفحة غير صالح')
    return job['pages'][index]


def valid_box(value,width,height):
    if not isinstance(value,list) or len(value)!=4 or any(not isinstance(n,(int,float)) or isinstance(n,bool) or not math.isfinite(n) for n in value):raise ValueError('إحداثيات خانة غير صالحة')
    if not (0<=value[0]<value[2]<=width and 0<=value[1]<value[3]<=height) or min(value[2]-value[0],value[3]-value[1])<1:raise ValueError('ضع الخانة داخل حدود الصفحة')
    return value


def save_page(key,index,changes):
    job=ready(key)
    with LOCK:
        if job['exportState']=='working' or job.get('editing'):raise ValueError('انتظر انتهاء معالجة الجلسة')
        page=deepcopy(page_at(job,index))
        if set(changes)-{'fields','confirmed','keepOriginal'}:raise ValueError('تعديل صفحة غير صالح')
        if 'fields' in changes:
            fields=changes['fields']
            if not isinstance(fields,list) or not len(page['fields'])<=len(fields)<=len(page['fields'])+50 or len(fields)>600:raise ValueError('خانات الصفحة غير صالحة')
            existing={f['id'] for f in page['fields']}
            for field in fields[len(page['fields']):]:
                if not isinstance(field,dict) or not re.fullmatch(r'renew-custom-[a-zA-Z0-9-]{1,60}',field.get('id','')) or field['id'] in existing:raise ValueError('خانة إضافية غير صالحة')
                label=field.get('label','خانة نقل إضافية')
                if not isinstance(label,str) or len(label)>100:raise ValueError('اسم الخانة أطول من المسموح')
                page['fields'].append({'id':field['id'],'label':label,'kind':'text','fontSize':22,'hasInk':False,'raw':False})
                existing.add(field['id'])
            for old,new in zip(page['fields'],fields):
                if not isinstance(new,dict) or new.get('id')!=old['id']:raise ValueError('معرّف خانة غير مطابق')
                if new.get('mode') not in ['image','text','blank','نعم','لا','لا ينطبق']:raise ValueError('طريقة نقل غير صالحة')
                if new['mode'] in ['نعم','لا','لا ينطبق'] and old['kind']!='check':raise ValueError('هذه الخانة ليست بند فحص')
                if new['mode'] in ['نعم','لا','لا ينطبق'] and old.get('editableValues') is False:raise ValueError('انقل علامة نوع المعدة من الأصل أو اتركها فارغة')
                valid_box(new.get('sourceBox'),page['width'],page['height']);valid_box(new.get('box'),page['width'],page['height'])
                text=new.get('text','')
                if not isinstance(text,str) or len(text)>1000:raise ValueError('النص أطول من المسموح')
                if not isinstance(new.get('raw',False),bool):raise ValueError('خيار النقل غير صالح')
                for k in ['mode','sourceBox','box','text','raw']:old[k]=deepcopy(new.get(k,False if k=='raw' else ''))
            page['confirmed']=False
        for flag in ['confirmed','keepOriginal']:
            if flag in changes:
                if not isinstance(changes[flag],bool):raise ValueError('حدد خيار الصفحة')
                page[flag]=changes[flag]
        if not page['code'] and not page['keepOriginal']:raise ValueError('حدد نموذجًا لهذه الصفحة أو احتفظ بالأصل')
        job['pages'][index]=page
        job['exportState']='idle'
    persist(job)
    return deepcopy(page)


def select_model(key,index,code,rotation=0,layout=None):
    job=ready(key);page_at(job,index)
    if code not in OPTIONS or rotation not in [0,90,180,270] or layout not in [None,'current','legacy'] or layout=='legacy' and code not in LEGACY:raise ValueError('اختر نموذجًا واتجاهًا صحيحين')
    with LOCK:
        if job['exportState']=='working' or job.get('editing'):raise ValueError('انتظر انتهاء معالجة الجلسة')
        job['editing']=True
    try:result=analyze_page(job,index,code,rotation,layout);job['exportState']='idle'
    finally:
        with LOCK:job['editing']=False
    persist(job);return result


def draw_page(job,entry,document):
    if entry['keepOriginal']:
        with fitz.open(job['directory']/'source.pdf') as original:document.insert_pdf(original,from_page=entry['index'],to_page=entry['index'])
        return
    check_version(entry)
    code=entry['code'];path=pdf.template_path('06' if code.startswith('06-') else code)
    with fitz.open(path) as template:
        out=document.new_page(width=template[0].rect.width,height=template[0].rect.height);out.show_pdf_page(out.rect,template,0)
    with Image.open(source_image(job,entry)) as source:
        source=source.convert('RGB');reference=Image.new('RGB',source.size,'white') if entry.get('layout')=='legacy' else target_image(code);checks=_check_regions(code)
        for field in entry['fields']:
            mode=field['mode'];box=field['box']
            if mode=='image':
                ink=patch(source,reference,field['sourceBox'],field.get('raw',False))
                if ink:
                    # A clear small mark can move to the corresponding new cell,
                    # including older forms whose answer columns have shifted.
                    hint=None;bounds=ink.getbbox();positions=field.get('sourcePositions',{})
                    if field['kind']=='check' and bounds and not field.get('raw') and positions and bounds[2]-bounds[0]<=36 and bounds[3]-bounds[1]<=30:
                        center=(field['sourceBox'][0]+(bounds[0]+bounds[2])/(2*ZOOM),field['sourceBox'][1]+(bounds[1]+bounds[3])/(2*ZOOM))
                        nearest=min(positions,key=lambda value:abs(positions[value][0]-center[0])+abs(positions[value][1]-center[1]))
                        if abs(positions[nearest][0]-center[0])<9 and abs(positions[nearest][1]-center[1])<7:hint=nearest
                    if hint and hint in checks[field['id']]['positions']:
                        x,y=checks[field['id']]['positions'][hint];base=field['baseBox'];pdf.tick(out,x+box[0]-base[0],y+box[1]-base[1]);continue
                    if field['kind']!='check' and ink.mode=='RGBA':
                        bounds=ink.getbbox()
                        if bounds:ink=ink.crop((max(0,bounds[0]-1),max(0,bounds[1]-1),min(ink.width,bounds[2]+1),min(ink.height,bounds[3]+1)))
                    stream=io.BytesIO();ink.save(stream,format='PNG');out.insert_image(fitz.Rect(box),stream=stream.getvalue(),keep_proportion=True)
            elif mode=='text':pdf.put(out,field['text'],box,field['fontSize']/22)
            elif mode in ['نعم','لا','لا ينطبق']:
                spec=checks[field['id']];base=field['baseBox'];dx=box[0]-base[0];dy=box[1]-base[1]
                if mode in spec['positions']:
                    x,y=spec['positions'][mode];pdf.tick(out,x+dx,y+dy)
                else:pdf.put(out,mode,box,.65)


def preview(key,index):
    job=ready(key)
    with LOCK:entry=deepcopy(page_at(job,index))
    with job['pdfLock']:
        doc=fitz.open()
        try:draw_page(job,entry,doc);return doc[0].get_pixmap(matrix=fitz.Matrix(1.3,1.3),alpha=False).tobytes('png')
        finally:doc.close()


def export(key):
    job=ready(key)
    with LOCK:entries=deepcopy(job['pages'])
    if any(not p['confirmed'] for p in entries):raise ValueError('راجع كل صفحة وحدد «راجعت نقل بيانات هذه الصفحة» أولًا')
    with job['pdfLock']:
        result=fitz.open()
        try:
            for entry in entries:draw_page(job,entry,result)
            return result.tobytes(deflate=True,garbage=3)
        finally:result.close()


def start_export(key):
    job=ready(key)
    with LOCK:
        if job['exportState']=='working' or job.get('editing'):raise ValueError('انتظر انتهاء معالجة الجلسة')
        if any(not p['confirmed'] for p in job['pages']):raise ValueError('راجع كل صفحة وحدد «راجعت نقل بيانات هذه الصفحة» أولًا')
        entries=deepcopy(job['pages']);job['exportState']='working';job['exportDone']=0
    def run():
        result=fitz.open()
        try:
            for entry in entries:
                with job['pdfLock']:draw_page(job,entry,result)
                with LOCK:job['exportDone']+=1
            with job['pdfLock']:raw=result.tobytes(deflate=True,garbage=3)
            (job['directory']/'renewed.pdf').write_bytes(raw)
            with LOCK:job['exportState']='done'
        except Exception:
            import traceback;traceback.print_exc()
            with LOCK:job['exportState']='error'
        finally:
            with job['pdfLock']:result.close()
    threading.Thread(target=run,daemon=True).start()


def delete(key):
    job=get(key)
    with LOCK:
        if job['state']=='working' or job['exportState']=='working' or job.get('editing'):raise ValueError('انتظر انتهاء المعالجة قبل حذف الجلسة')
        job['editing']=True
    try:
        if DB:
            with DB() as c:c.execute('DELETE FROM renewal_jobs WHERE id=?',(key,))
        with LOCK:JOBS.pop(key,None)
        shutil.rmtree(job['directory'],ignore_errors=True)
    finally:job['editing']=False
