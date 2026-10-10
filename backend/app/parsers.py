from __future__ import annotations
import re
from pathlib import Path
import fitz
from docx import Document
from charset_normalizer import from_bytes
from .core import AppError


def parse_file(path,format):
    spans=[];warnings=[]
    try:
        if format=='pdf':
            with fitz.open(path) as pdf:
                if pdf.needs_pass: raise AppError('PDF_PASSWORD','PDF 已加密，请先解密后上传。')
                if len(pdf)>3000: raise AppError('DOCUMENT_LIMIT','PDF 超过 3000 页限制。')
                for i,page in enumerate(pdf):
                    text=page.get_text('text').strip()
                    if not text:warnings.append(f'第 {i+1} 页没有可提取文字，未解析该页。')
                    else:spans.append({'text':text,'location':{'type':'page','page':i+1}})
        elif format=='docx':
            doc=Document(path)
            index=0
            # Iterate XML order so basic table content retains its place between paragraphs.
            for block in doc.element.body:
                name=block.tag.rsplit('}',1)[-1]
                if name=='p':
                    text=''.join(n.text or '' for n in block.iter() if n.tag.endswith('}t')).strip()
                    index+=1
                    if text:spans.append({'text':text,'location':{'type':'paragraph','paragraph':index}})
                elif name=='tbl':
                    rows=[]
                    for row in block:
                        if row.tag.endswith('}tr'):
                            rows.append(' | '.join(''.join(n.text or '' for n in cell.iter() if n.tag.endswith('}t')) for cell in row if cell.tag.endswith('}tc')))
                    index+=1
                    if rows:spans.append({'text':'\n'.join(rows),'location':{'type':'paragraph','paragraph':index,'table':True}})
        else:
            raw=Path(path).read_bytes()
            if b'\x00' in raw[:4096]:raise AppError('ENCODING','文本包含二进制内容，无法解析。')
            try:text=raw.decode('utf-8-sig')
            except UnicodeDecodeError:
                best=from_bytes(raw).best()
                if not best:raise AppError('ENCODING','无法识别文本编码，请转换为 UTF-8。')
                text=str(best)
            lines=text.splitlines();start=None;buffer=[]
            for i,line in enumerate(lines+[""]):
                if line.strip():
                    if start is None:start=i+1
                    buffer.append(line)
                elif buffer:
                    spans.append({'text':'\n'.join(buffer),'location':{'type':'lines','start_line':start,'end_line':i}});buffer=[];start=None
    except AppError:raise
    except Exception:raise AppError('PARSE_FAILED','文件损坏或格式与扩展名不符，无法解析。')
    if not spans:raise AppError('NO_TEXT','没有可提取文字；扫描 PDF 暂不支持 OCR。')
    if sum(len(s['text']) for s in spans)>5_000_000:raise AppError('DOCUMENT_LIMIT','提取文字超过 500 万字符限制。')
    return {'spans':spans,'warnings':warnings}


def chunk_spans(spans,length,overlap):
    if not (1<=length<=12000 and 0<=overlap<length):raise AppError('CHUNK_SETTINGS','切片参数不合法。')
    # Prefer paragraph endings while preserving deterministic character offsets and cross-page spans.
    source=[];parts=[];offset=0
    for span in spans:
        if parts:parts.append('\n\n');offset+=2
        start=offset;parts.append(span['text']);offset+=len(span['text'])
        source.append({'start':start,'end':offset,**span['location']})
    text=''.join(parts);chunks=[];start=0
    while start<len(text):
        end=min(start+length,len(text))
        if end<len(text):
            boundary=text.rfind('\n\n',start+max(overlap+1,length*4//5),end)
            if boundary>start+overlap:end=boundary+2
        content=text[start:end]
        if content.strip():
            locations=[]
            for span in source:
                if span['start']<end and span['end']>start:
                    locations.append({**span,'offset_start':max(span['start'],start)-span['start'],'offset_end':min(span['end'],end)-span['start']})
            chunks.append({'ordinal':len(chunks)+1,'text':content,'source_spans':locations,'char_start':start,'char_end':end})
        if end==len(text):break
        start=max(start+1,end-overlap)
    return chunks
