"""Private signature/stamp assets and placements; source image bytes stay unchanged."""
from functools import lru_cache
import hashlib
import io
import unicodedata

import fitz
from PIL import Image, ImageChops
from .consultants import CONSULTANTS

KINDS={'signature','stamp'}|{c['signatureKind'] for c in CONSULTANTS}
DEFAULT_SIGNING={'owner':'علي الشهري','signatureEnabled':True,'stampEnabled':True}
# Source ids identify the printed consultant's name, not the contractor or PDC.
SIGNATURE_BOXES={
 '01':[('inspector',[90,591,258,613],'التوقيع — ممثل الاستشاري')],
 'inspection':[('inspector#2',[75,656,236,672],'التوقيع — ممثل الاستشاري')],
 '22':[('inspector#2',[195,225,310,248],'التوقيع — معد التقرير'),('inspector#3',[55,566,167,584],'التوقيع — ممثل الاستشاري')],
 '03':[('inspector#2',[40,591,232,612],'التوقيع — ممثل الاستشاري'),('inspector#3',[40,662,232,682],'التوقيع — المهندس المختص')],
 '02':[('inspector#2',[75,584,225,609],'التوقيع — ممثل الاستشاري')],
 '26':[('inspector',[105,596,274,621],'التوقيع — ممثل الاستشاري')],
 '24':[('inspector',[53,616,198,638],'التوقيع — ممثل الاستشاري')],
 '25':[('inspector',[130,414,274,441],'التوقيع — ممثل الاستشاري')],
 '16':[('inspector',[155,467,328,494],'التوقيع — ممثل الاستشاري'),('inspector#2',[115,578,285,590],'التوقيع — Inspector sign')],
 '05':[('inspector',[353,641,528,664],'التوقيع — ممثل الاستشاري')],
 '10':[('inspector',[350,616.5,540,632.5],'التوقيع — ممثل الاستشاري')],
 '09':[('inspector',[355,679,540,689],'التوقيع — ممثل الاستشاري')],
 '06-transformer':[('inspector',[150,518,310,545],'التوقيع — ممثل الاستشاري')],
 '06-rmu':[('inspector',[150,518,310,545],'التوقيع — ممثل الاستشاري')],
 '08':[('inspector',[160,565,345,594],'التوقيع — ممثل الاستشاري')],
 '07':[('inspector',[350,582,536,605],'التوقيع — ممثل الاستشاري')],
}


def signing_config(settings=None):
    return dict(DEFAULT_SIGNING,**(settings or {}).get('signing',{}))


def validate_signing_config(config):
    if not isinstance(config,dict) or set(config)-set(DEFAULT_SIGNING):raise ValueError('إعدادات التوقيع والختم غير صالحة')
    owner=config.get('owner',DEFAULT_SIGNING['owner'])
    if not isinstance(owner,str) or not owner.strip() or len(owner)>100:raise ValueError('أدخل اسم صاحب التوقيع')
    for key in ['signatureEnabled','stampEnabled']:
        if not isinstance(config.get(key,True),bool):raise ValueError('حدد تفعيل التوقيع والختم')
    result=dict(DEFAULT_SIGNING,**config);result['owner']=owner.strip();return result


def inspect_asset(raw,kind):
    if kind not in KINDS:raise ValueError('اختر التوقيع أو الختم')
    if not raw or len(raw)>6_000_000:raise ValueError('ارفع صورة لا يتجاوز حجمها 6 ميغابايت')
    try:
        with Image.open(io.BytesIO(raw)) as im:
            if im.format not in ['PNG','JPEG']:raise ValueError('الصورة يجب أن تكون PNG أو JPEG')
            if im.width*im.height>8_000_000:raise ValueError('دقة الصورة كبيرة؛ الحد 8 ملايين بكسل')
            mime='image/png' if im.format=='PNG' else 'image/jpeg'
            rgba=im.convert('RGBA');width,height=im.size
    except (OSError,Image.DecompressionBombError) as e:raise ValueError('تعذر قراءة الصورة') from e
    if all(lo==hi for lo,hi in rgba.getextrema()):raise ValueError('الصورة بلون واحد؛ ارفع توقيعًا أو ختمًا واضحًا')
    # Measure visible ink for PDF clipping only. Do not edit or regenerate the asset.
    red,green,blue,alpha=rgba.split()
    ink=ImageChops.multiply(ImageChops.darker(ImageChops.darker(red,green),blue).point(lambda v:255 if v<245 else 0),alpha.point(lambda v:255 if v>24 else 0))
    bbox=ink.getbbox()
    if not bbox:raise ValueError('لم يظهر توقيع أو ختم واضح داخل الصورة')
    if kind=='stamp':
        # Isolated scan specks outside the rectangular seal do not set its PDF margins.
        rows=list(ink.resize((1,height),Image.Resampling.BOX).tobytes())
        cols=list(ink.resize((width,1),Image.Resampling.BOX).tobytes())
        row_cut=max(2,max(rows)*.02);col_cut=max(2,max(cols)*.02)
        ys=[i for i,v in enumerate(rows) if v>=row_cut]
        xs=[i for i,v in enumerate(cols) if v>=col_cut]
        if xs and ys:bbox=(max(0,min(xs)-2),max(0,min(ys)-2),min(width,max(xs)+3),min(height,max(ys)+3))
    return {'width':width,'height':height,'bbox':list(bbox),'mime':mime,'version':hashlib.sha256(raw).hexdigest()[:16]}


def same_owner(name,owner):
    def normalized(value):
        value=unicodedata.normalize('NFKC',str(value or ''))
        return ''.join(c for c in value if not c.isspace() and c!='ـ' and not unicodedata.combining(c))
    return bool(normalized(name)) and normalized(name)==normalized(owner)


def signature_asset_kind(name,assets,settings=None):
    """Resolve separate originals, retaining the previous single-owner upload."""
    for consultant in CONSULTANTS:
        if same_owner(name,consultant['name']) and assets.get(consultant['signatureKind']):
            return consultant['signatureKind']
    # The legacy owner is never changed when choosing another consultant in the UI.
    if assets.get('signature') and same_owner(name,signing_config(settings)['owner']):return 'signature'
    return None


def stamp_box(code,width,height):
    if code=='25':return [24,559,160,608]
    if code=='LV':return [24,497,184,570]
    return [24,height-86,184,height-32]


@lru_cache(maxsize=4)
def asset_pdf(raw,width,height):
    doc=fitz.open();page=doc.new_page(width=width,height=height)
    page.insert_image(page.rect,stream=raw)
    result=doc.tobytes(deflate=True);doc.close();return result


def draw_asset(page,asset,box):
    meta=asset['metadata']
    raw=asset_pdf(asset['raw'],meta['width'],meta['height'])
    with fitz.open(stream=raw,filetype='pdf') as image_doc:
        page.show_pdf_page(fitz.Rect(box),image_doc,0,clip=fitz.Rect(meta['bbox']),keep_proportion=True)


def draw_signing(page,code,order,overlay,assets,settings=None):
    config=signing_config(settings);image_rules=overlay.config.get('images',{})
    for i,(source,box,label) in enumerate(SIGNATURE_BOXES.get(code,[])):
        key='consultantSignature'+(f'#{i+1}' if i else '')
        field=next((f for f in overlay.fields if f['kind']=='text' and f['id']==source),None)
        name=field['example'] if field else order.get('inspector','')
        kind=signature_asset_kind(name,assets,settings) if same_owner(name,order.get('inspector','')) else None
        allowed=config['signatureEnabled'] and kind is not None
        actual_box=overlay.image(key,box,label,'signature',allowed)
        if not overlay.capture and allowed and image_rules.get(key,{}).get('mode','auto')!='blank':draw_asset(page,assets[kind],actual_box)
    box=stamp_box(code,page.rect.width,page.rect.height)
    actual_box=overlay.image('consultantStamp',box,'الختم — أسفل يسار الصفحة','stamp',config['stampEnabled'] and bool(assets.get('stamp')))
    if not overlay.capture and config['stampEnabled'] and assets.get('stamp') and image_rules.get('consultantStamp',{}).get('mode','auto')!='blank':draw_asset(page,assets['stamp'],actual_box)
