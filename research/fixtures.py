"""Synthetic images with answers encoded visually, never in request text."""
import base64
import io
import random
import struct
import zlib
from PIL import Image,ImageDraw,ImageFont

FONT='/System/Library/Fonts/Helvetica.ttc'
VALUES=[('731842','1.25','BLUE'),('486219','0.05','RED'),('925673','2.75','GREEN'),('364958','0.15','BLUE'),('852416','3.25','RED'),('219785','0.25','GREEN'),('643197','4.50','BLUE'),('597324','0.75','RED'),('178963','2.05','GREEN')]

def page(index,realistic=False):
    code,dose,color=VALUES[index%len(VALUES)]
    w,h=(1024,1536) if realistic else (640,800)
    if realistic:
        raw=random.Random(index+724).randbytes(w*h*3)
        image=Image.frombytes('RGB',(w,h),raw)
    else:image=Image.new('RGB',(w,h),'white')
    d=ImageDraw.Draw(image)
    d.rectangle((35,35,w-35,h-35),fill='white',outline='black',width=3)
    font=ImageFont.truetype(FONT,44 if realistic else 34)
    small=ImageFont.truetype(FONT,30 if realistic else 25)
    d.text((60,65),'SYNTHETIC TEST FORM',font=small,fill='black')
    d.text((60,160),f'Page ID: {code}',font=font,fill='black')
    d.text((60,260),f'Dose: {dose} mg',font=font,fill='black')
    d.rectangle((70,380,110,420),outline='black',width=3)
    d.line((75,398,88,414,108,384),fill='black',width=4)
    d.text((140,380),'Reviewed',font=small,fill='black')
    d.rectangle((60,500,250,610),fill=color.lower())
    d.text((60,660),'End of synthetic page',font=small,fill='black')
    return image

def encode(im,fmt='PNG',padding=0):
    f=io.BytesIO();im.save(f,fmt,**({'quality':90} if fmt=='JPEG' else {}));raw=f.getvalue()
    if padding:
        assert fmt=='PNG'
        value=b'Q'*padding;kind=b'npAD'
        chunk=struct.pack('>I',len(value))+kind+value+struct.pack('>I',zlib.crc32(kind+value))
        raw=raw[:-12]+chunk+raw[-12:]
    return 'data:image/'+('jpeg' if fmt=='JPEG' else 'png')+';base64,'+base64.b64encode(raw).decode()

def parts(index,encoded_target=None,realistic=False):
    if not realistic:
        im=page(index);url=encode(im)
        if encoded_target:
            padding=max(0,int((encoded_target-len(url))*3/4)-12)
            url=encode(im,padding=padding)
        return [{'type':'input_image','image_url':url}]
    # Deterministic noisy border makes actual visual rasters large enough.
    # Mix JPEG and PNG, and repeat pages to reach controlled initial sizes.
    output=[]
    for i in range(6):
        im=page(index+i,True)
        fmt='JPEG' if i%2 else 'PNG'
        output.append({'type':'input_image','image_url':encode(im,fmt)})
    if encoded_target:
        j=0
        while sum(len(x['image_url']) for x in output)<encoded_target:
            output.append(dict(output[j%6]));j+=1
    return output
