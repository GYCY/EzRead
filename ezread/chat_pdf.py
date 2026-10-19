"""Prepare every original PDF page for multimodal paper chat, without library writes."""
from pathlib import Path
import re

import pypdfium2 as pdfium
from pdf_tools import _PDF_LOCK, _save_jpeg


def attachment(library, pid, doc):
    """The imported original is implicitly attached across all chat generations."""
    root = Path(library).resolve()
    source = (root / pid / 'original.pdf').resolve()
    available = bool(re.fullmatch(r'[a-f0-9]{16}', pid)) and source.is_relative_to(root) and source.is_file()
    return {'type': 'pdf', 'name': doc.get('filename') or 'original.pdf',
            'available': available}


def prepare_pdf(library, pid, output_dir, *, cancel_event=None, on_progress=None):
    if not re.fullmatch(r'[a-f0-9]{16}', pid):
        raise ValueError('无效的论文编号。')
    root=Path(library).resolve()
    source=(root/pid/'original.pdf').resolve()
    if not source.is_relative_to(root) or not source.is_file():
        raise ValueError('找不到这篇论文的原始 PDF，请重新导入原文件；未改用摘要回答。')
    output=Path(output_dir)
    output.mkdir(parents=True,exist_ok=True)
    pages=[]
    images=[]
    with _PDF_LOCK:
        try:
            document=pdfium.PdfDocument(str(source))
        except Exception as exc:
            raise ValueError('无法打开原始 PDF，请检查文件是否损坏或需要密码。') from exc
        try:
            total=len(document)
            if not total:
                raise ValueError('原始 PDF 没有可读取的页面。')
            for index in range(total):
                if cancel_event is not None and cancel_event.is_set():
                    raise ValueError('PDF 准备已取消，未发送问答请求。')
                if on_progress:
                    on_progress({'type':'status','stage':'preparing_pdf',
                                 'label':f'正在准备原始 PDF：第 {index+1}/{total} 页'})
                page=document[index]
                try:
                    text=''
                    text_available=True
                    try:
                        textpage=page.get_textpage()
                        try:
                            text=textpage.get_text_range()
                        finally:
                            textpage.close()
                    except Exception:
                        text_available=False
                    width,height=page.get_size()
                    bitmap=page.render(scale=min(2.5,2600/max(width,height,1)))
                    path=output/f'pdf-page-{index+1:04d}.jpg'
                    try:
                        _save_jpeg(bitmap.to_pil(),path)
                    finally:
                        bitmap.close()
                    pages.append({'block_id':f'pdf-page-{index+1}','page':index+1,
                                  'original':text,'text_extracted':text_available,
                                  'image_number':index+1})
                    images.append(str(path.resolve()))
                finally:
                    page.close()
        finally:
            document.close()
    return pages,images
