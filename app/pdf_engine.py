"""Write only into reviewed blank rectangles on the supplied original PDFs."""
import html
import io
import json
import os
import re
from datetime import date
from functools import lru_cache
from pathlib import Path

import fitz
from PIL import Image, ImageChops

from .schema import selected_forms, lv_checks
from .storage import read_template
from .form_editor import ACTIVE, Overlay
from .form_settings import form_config, check_override
from .signing import draw_signing

ASSETS=Path(__file__).parent/'assets'
TEMPLATES=Path(os.getenv('TEMPLATE_DIR',str(ASSETS/'templates')))
LAYOUT=json.loads((ASSETS/'reviewed-layouts.json').read_text())
BLUE=(.09,.21,.36)

def template_path(code):
    path=TEMPLATES/(code+'.pdf')
    if path.is_file():return path
    raw=read_template(code)
    if raw:
        TEMPLATES.mkdir(parents=True,exist_ok=True)
        temporary=path.with_suffix('.cache');temporary.write_bytes(raw);temporary.replace(path)
    return path
KEYS={
 '01':['division','region','contractor','feeder','location','dateText','id','shortDate','timeText','handoverNotes','consultantShort','representative','inspector'],
 'inspection':['department','inspector','id','time','dateText','description','contractorLabel','note1','inspector'],
 '22':['region','division','safetyArea','dateText','reportNumber','inspector','inspector','safetyRecipient','safety.0.0','safety.0.1','safety.0.2','safety.1.0','safety.1.1','safety.1.2','safety.2.0','safety.2.1','safety.2.2','inspector'],
 '03':['division','region','weekday','dateText','attendance','inspector','workCode','contractor','feeder','location','id','inspector','inspector'],
 '02':['region','division','contractor','representative','weekday','dateText','description','workCode','time','inspector','feeder','location','id','customer','inspector'],
 '26':['region','division','contractor','id','inspector'],
 '24':['id','location','contractor','feeder','representative','inspector'],
 '16':['division','region','id','location','contractor','inspector','inspector'],
}
NAME_SIGNATURE_BOXES={
 '01':{'representative':[85,553,260,573]},
 '02':{'representative':[402,280,525,295]},
 '24':{'representative':[53,589,198,611]},
 '25':{'representative':[130,383,274,409],'operator':[140,505,233,537]},
 'LV':{'representative':[108,420.5,225,432.2]},
}

@lru_cache(maxsize=4096)
def text_image(text, size):
    """Shape Arabic, measure actual ink, retain vector glyphs and original form."""
    doc=fitz.open();p=doc.new_page(width=680,height=200)
    css='@font-face{font-family:Hand;src:url(haya-regular.otf)}div{font-family:Hand;color:#17365d;text-align:center;margin:0;padding:0}'
    direction='rtl' if re.search('[\u0600-\u06ff]',text) else 'ltr'
    p.insert_htmlbox(fitz.Rect(15,20,665,185),f'<div dir="{direction}" style="font-size:{size}pt;line-height:1.1">{html.escape(text)}</div>',css=css,archive=fitz.Archive(str(ASSETS)))
    pix=p.get_pixmap(matrix=fitz.Matrix(2,2),alpha=False)
    im=Image.frombytes('RGB',(pix.width,pix.height),pix.samples)
    bb=ImageChops.difference(im,Image.new('RGB',im.size,'white')).getbbox()
    if not bb:return None
    clip=fitz.Rect(bb[0]/2-.4,bb[1]/2-.4,bb[2]/2+.4,bb[3]/2+.4)
    raw=doc.tobytes(deflate=True);doc.close()
    return raw,tuple(clip)

def put(p,text,box,scale=1,key=None):
    overlay=ACTIVE.get()
    if overlay:
        text,box,scale=overlay.text(text,box,scale,key)
        if overlay.capture:return
    if text is None or str(text).strip()=='':return
    text=str(text).strip();b=fitz.Rect(box)
    b=fitz.Rect(b.x0+1,b.y0+.6,b.x1-1,b.y1-.6)
    shaped=text_image(text,round(22*scale,1))
    if not shaped:return
    raw,coords=shaped;clip=fitz.Rect(coords)
    factor=min(1,b.width/clip.width,b.height/clip.height)
    w,h=clip.width*factor,clip.height*factor
    target=fitz.Rect(b.x0+(b.width-w)/2,b.y0+(b.height-h)/2,b.x0+(b.width+w)/2,b.y0+(b.height+h)/2)
    with fitz.open(stream=raw,filetype='pdf') as doc:p.show_pdf_page(target,doc,0,clip=clip)

def tick(p,x,y,scale=1):
    p.draw_polyline([fitz.Point(x-3*scale,y),fitz.Point(x-scale,y+3*scale),fitz.Point(x+5*scale,y-4*scale)],color=BLUE,width=max(.5,scale))

def row_numbers(src,xmin,ymin,ymax):
    return {int(w[4]):(w[1]+w[3])/2 for w in src.get_text('words') if w[0]>xmin and ymin<w[1]<ymax and w[4].isdigit()}

def date_text(value):
    if not value:return ''
    try:return date.fromisoformat(value).strftime('%d/%m/%Y')
    except ValueError:return str(value)

def data_values(o):
    v=dict(o)
    v.update(dateText=date_text(o.get('date')),
      location=' — '.join(filter(None,[o.get('city',''),o.get('site','')])),department=' — '.join(filter(None,[o.get('region',''),o.get('division','')])),
      contractorLabel='المقاول: '+o.get('contractor',''),reportNumber='1',timeText=o.get('time','08:00')+' AM',note1=o.get('inspectionNotes',{}).get('1',''))
    try:
        dt=date.fromisoformat(o.get('date',''));v['weekday']=['الاثنين','الثلاثاء','الأربعاء','الخميس','الجمعة','السبت','الأحد'][dt.weekday()];v['shortDate']=dt.strftime('%d/%m')
    except ValueError:v['weekday']=v['shortDate']=''
    for i,row in enumerate(o.get('safetyRows',[])):
        for j,text in enumerate(row):v[f'safety.{i}.{j}']=text
    return v

def assessment(o,key,index):
    if f'assessments.{key}.{index}' in o.get('skipped',[]):return ''
    vals=o.get('assessments',{}).get(key,[])
    return vals[index] if index<len(vals) else ''

def draw_checks(p,src,code,o):
    from .check_layouts import check_specs
    overlay=ACTIVE.get();config=overlay.config if overlay else {}
    for spec in check_specs(code,src,o):
        condition=spec['condition'];applicable=True
        if condition=='ct':applicable=any(m.get('ct') for m in o.get('meters',[]))
        elif condition=='temporaryMeter':applicable=bool(o.get('temporaryMeter'))
        elif condition:applicable=condition in o.get('works',[])
        value=spec['default']
        if value is None:
            value=lv_checks(o)[spec['index']] if code=='LV' else assessment(o,spec['tag'],spec['index'])
        override=check_override(config,spec['id'],o)
        if override is not None:value=override
        if not applicable:value=''
        dx,dy,scale=overlay.check(spec,value) if overlay else (0,0,1)
        if overlay and overlay.capture:continue
        if spec['textBox']:
            box=spec['textBox'];put(p,value,[box[0]+dx,box[1]+dy,box[2]+dx,box[3]+dy],scale)
        elif value in spec['positions']:
            x,y=spec['positions'][value];tick(p,x+dx,y+dy,scale)
        elif value:
            box=spec.get('naBox',spec['box']);put(p,value,[box[0]+dx,box[1]+dy,box[2]+dx,box[3]+dy],scale)
    if code=='inspection':
        for n,y in row_numbers(src,565,170,650).items():
            if n!=1:put(p,o.get('inspectionNotes',{}).get(str(n),''),[37,y-5.5,169,y+5.5],key=f'inspectionNote.{n}')

METER_COLS={'kind':(666,712),'subscription':(622,663),'place':(587,619),'previous':(547,584),'breaker':(505,544),
 'serial':(406,502),'rating':(369,404),'digits':(330,366),'reading':(289,327),'removedReading':(258,286),
 'voltage':(226,254),'ctRatio':(185,222),'multiplier':(147,181),'maker':(107,143),'factory':(67,104),'year':(33,64)}

def meter_values(m,order_voltage=''):
    new=m.get('kind','جديد')!='إزالة';ct=bool(m.get('ct'));s=str(m.get('serial',''))
    prefix=re.match(r'([A-Za-z]+)(\d{2})',s)
    try:rating='160' if float(m.get('breaker',0))>100 else '100'
    except (ValueError,TypeError):rating=''
    return dict(m,kind=m.get('kind','جديد'),place=m.get('place','سكني'),rating=m.get('rating','') if ct else rating,
      digits=m.get('digits','8'),reading=m.get('reading','0' if new else '-'),removedReading=m.get('removedReading','-' if new else ''),
      voltage=str(m.get('voltage') or order_voltage or ''),ctRatio=m.get('ctRatio','') if ct else '-',multiplier=m.get('multiplier','') if ct else '1',
      maker=m.get('maker') or ('SMC' if s.upper().startswith('SMR') else prefix[1] if prefix else ''),
      factory=m.get('factory','-'),year=m.get('year') or ('20'+prefix[2] if prefix else ''),breaker=m.get('breaker','') if new else '',
      previous=m.get('previous',m.get('breaker','') if not new else ''))

def fill_meter(p,o,meters,v):
    for key,box in [('region',[400,113,491,126]),('city',[182,113,300,126]),('id',[400,128,491,140]),('feeder',[182,128,300,140])]:put(p,v.get(key,''),box,key=key)
    put(p,sum(m.get('kind','جديد')!='إزالة' for m in o.get('meters',[])),[400,142,491,154],key='meterCount');put(p,(str(o.get('mainBreaker'))+' A') if o.get('mainBreaker') else '',[182,142,300,154],key='mainBreaker')
    for i in range(10):
        y=215+i*15.16;values=meter_values(meters[i],o.get('voltage','')) if i<len(meters) else {}
        for key,(x,x1) in METER_COLS.items():put(p,values.get(key,''),[x,y,x1,y+13.5],key=f'meters.{i}.{key}')
    for key,box in [('representative',[326,383,506,409]),('inspector',[326,414,506,441]),('operator',[436,505,543,537]),('operatorId',[329,505,352,537])]:put(p,v.get(key,''),box,key=key)

def extras(p,code,o,v):
    # These rectangles use the current originals (including the added footer).
    def f(k,b):
        dy=0 if code=='05' or code=='08' and b[1]>500 else 13.8
        put(p,v.get(k,''),[b[0],b[1]+dy,b[2],b[3]+dy],key=k)
    if code in ['06-transformer','06-rmu']:
        for k,b in [('division',[181,141,270,151]),('region',[373,139,479,152]),('id',[180,152,270,164]),('location',[399,153,478,163]),('contractor',[413,165,478,175]),('feeder',[180,165,270,175]),('inspector',[342,498,474,514])]:f(k,b)
        label='قاعدة المحول '+str(o.get('transformerKva',''))+' kVA' if code.endswith('transformer') else 'قاعدة الوحدة الحلقية '+str(o.get('rmuCells',''))+' خلايا'
        put(p,label,[160,474,457,497],key='foundationLabel')
    elif code=='07':
        for k,box in [('division',[110,124,295,137]),('region',[390,124,522,137]),('location',[130,144,295,157]),('id',[415,144,520,157]),('contractor',[140,164,295,177]),('feeder',[423,164,525,177])]:put(p,v.get(k,''),box,key=k)
        put(p,o.get('inspector',''),[350,565,506,580],key='inspector')
    elif code in ['08','09']:
        boxes=[('division',[151,108,285,121]),('region',[359,108,500,121]),('id',[138,130,231,146]),('id',[158,152,231,169]),('location',[400,128,491,144]),('contractor',[410,153,491,168]),('inspector',[370,563,516,581])]
        if code=='09':boxes=[('division',[139,93,275,102]),('region',[340,91,479,105]),('id',[168,110,274,124]),('id',[168,132,274,146]),('location',[420,110,525,124]),('contractor',[420,132,525,146]),('inspector',[353,656,539,665])]
        for k,b in boxes:f(k,b)
    elif code=='05':
        for k,b in [('division',[89,111,172,127]),('region',[482,111,528,127]),('id',[115,146,239,159]),('location',[405,132,542,143]),('contractor',[408,146,541,157]),('feeder',[115,132,239,143]),('inspector',[350,617,508,632])]:f(k,b)
        text='؛ '.join(filter(None,[f"حفر {o['excavationLength']} م" if o.get('excavationLength') else '',f"متوسط {o.get('mvSize','')}: {o['mvLength']} م" if o.get('mvLength') else '',f"منخفض {o.get('lvSize','')}: {o['lvLength']} م" if o.get('lvLength') else '']))
        put(p,text,[68,593,480,601],key='excavationSummary')
    elif code=='10':
        for k,b in [('division',[198,92,284,103]),('region',[410,90,491,102]),('contractor',[174,103,299,115]),('id',[368,103,491,115]),('equipmentNumber',[400,365,495,377]),('inspector',[352,588,521,602])]:f(k,b)
        put(p,o.get('terminationNotes',''),[83,560,555,578],key='terminationNotes')
    elif code=='LV':
        put(p,o.get('contractor',''),[140,103.5,475,116.7],key='contractor');put(p,o.get('id',''),[159,119.1,477,131.5],key='id');put(p,v['dateText'],[92,435.3,225,447.5],key='dateText')
        put(p,o.get('representative',''),[108,405.6,225,418.6],float(o.get('fontScale',1)),key='representative')

def repeat_names_in_signature_boxes(page,code,o):
    overlay=ACTIVE.get()
    for source,box in NAME_SIGNATURE_BOXES.get(code,{}).items():
        # Follow the already-resolved printed name, including per-form fixed/blank rules.
        field=next((f for f in overlay.fields if f['kind']=='text' and f['id']==source),None) if overlay else None
        name=field['example'] if field is not None else o.get(source,'')
        put(page,name,box,float(o.get('fontScale',1)),key=source+'Signature')

def draw_form(page,src,code,o,settings=None,assets=None):
    v=data_values(o)
    if code in KEYS:
        for entry,key in zip(LAYOUT[code],KEYS[code]):
            box=entry['box'].copy()
            if code=='22' and box[0]==327:box[0]=333;box[2]=492
            if code=='03' and key=='contractor':box[2]=349
            if code=='02' and key=='location':box[2]=391
            if code=='24' and key=='location':box[2]=202
            put(page,v.get(key,''),box,float(o.get('fontScale',1)),key=key)
        if code=='02':put(page,o.get('excavationLength',''),[254,398,301,414],key='excavationLength')
    elif code=='25':fill_meter(page,o,o.get('_meterGroup',o.get('meters',[])[:10]),v)
    else:extras(page,code,o,v)
    repeat_names_in_signature_boxes(page,code,o)
    draw_checks(page,src,code,o)
    if code=='03':page.draw_oval(fitz.Rect(485,327.8,555,343.8),color=BLUE,width=1)
    elif code=='02':page.draw_oval(fitz.Rect(120,272.8,191,289.8),color=BLUE,width=1)
    overlay=ACTIVE.get()
    if overlay:
        for key,rule in overlay.config.get('fields',{}).items():
            if key.startswith('custom-'):
                text=rule.get('text','')
                if rule.get('mode')=='input':
                    text=o.get('customFields',{}).get(code,{}).get(key,'')
                    if rule.get('inputType')=='date':text=date_text(text)
                put(page,text,rule['box'],key=key)
        draw_signing(page,code,o,overlay,assets or {},settings)


def example_order(settings=None):
    from .schema import new_order
    o=new_order(settings)
    o.update(id='264000001',date='2026-10-21',site='موقع للمعاينة',feeder='000000',
             description='أعمال للمعاينة',permit='0000',mainBreaker='400',
             operator='مدخل البيانات',operatorId='000000',excavationLength='100',
             mvSize='3×500',mvLength='100',lvSize='4×300',lvLength='120',
             transformerKva='500',rmuCells='3',terminationNotes='نهايات طرفية',
             equipmentNumber='000000',meters=[{'serial':'ECC2520842100000','breaker':'70','subscription':'000000'}])
    return o


def describe_form(code, settings=None):
    config=form_config(settings,code)
    path=template_path('06' if code.startswith('06-') else code)
    if not path.is_file():raise ValueError('النموذج غير متوفر؛ ارفع حزمة النماذج من «نقل الموقع» أولًا')
    with fitz.open(path) as original:
        doc=fitz.open();page=doc.new_page(width=original[0].rect.width,height=original[0].rect.height)
        overlay=Overlay(code,config,capture=True);token=ACTIVE.set(overlay)
        try:draw_form(page,original[0],code,example_order(settings),settings)
        finally:ACTIVE.reset(token);doc.close()
        return {'width':original[0].rect.width,'height':original[0].rect.height,'fields':overlay.fields}


def form_image(code, config=None, order=None, settings=None,assets=None):
    order=order or example_order(settings)
    with fitz.open(template_path('06' if code.startswith('06-') else code)) as original:
        doc=fitz.open();page=doc.new_page(width=original[0].rect.width,height=original[0].rect.height)
        page.show_pdf_page(page.rect,original,0)
        overlay=Overlay(code,config or {});token=ACTIVE.set(overlay)
        try:draw_form(page,original[0],code,order,settings,assets)
        finally:ACTIVE.reset(token)
        raw=page.get_pixmap(matrix=fitz.Matrix(1.5,1.5),alpha=False).tobytes('png')
        doc.close();return raw


def generate(o, only_form=None, settings=None,assets=None):
    result=fitz.open();codes=[only_form] if only_form else selected_forms(o,settings)
    if not codes:raise ValueError('لا توجد نماذج ظاهرة لهذا الأمر؛ راجع قواعد الظهور في إدارة النماذج')
    for code in codes:
        base='06' if code.startswith('06-') else code
        path=template_path(base)
        if not path.exists():raise ValueError('النموذج غير متوفر: '+code)
        groups=[o.get('meters',[])[i:i+10] for i in range(0,max(1,len(o.get('meters',[]))),10)] if code=='25' else [None]
        for group in groups:
            with fitz.open(path) as original:
                src=original[0];page=result.new_page(width=src.rect.width,height=src.rect.height);page.show_pdf_page(page.rect,original,0)
                order=dict(o,_meterGroup=group) if group is not None else o
                token=ACTIVE.set(Overlay(code,form_config(settings,code)))
                try:draw_form(page,src,code,order,settings,assets)
                finally:ACTIVE.reset(token)
    # Flatten only the finished copies for broad compatibility and bounded file size.
    flattened=fitz.open()
    for page in result:
        q=flattened.new_page(width=page.rect.width,height=page.rect.height)
        q.insert_image(q.rect,stream=page.get_pixmap(matrix=fitz.Matrix(1.6,1.6),alpha=False).tobytes('jpeg',jpg_quality=92))
    raw=flattened.tobytes(deflate=True);result.close();flattened.close()
    return raw
