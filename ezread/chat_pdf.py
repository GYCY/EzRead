"""Cache every original PDF page for multimodal paper chat without altering sources."""
from pathlib import Path
import re
import hashlib
import json
import threading

import pypdfium2 as pdfium
from pdf_tools import _PDF_LOCK, _save_jpeg

_CACHE_LOCK = threading.RLock()


def original_pdf(library, pid):
    """Resolve the original inside the library; reject missing or escaped paths."""
    if not re.fullmatch(r'[a-f0-9]{16}', pid):
        raise ValueError('无效的论文编号。')
    root=Path(library).resolve()
    source=(root/pid/'original.pdf').resolve()
    if not source.is_relative_to(root) or not source.is_file():
        raise ValueError('找不到这篇论文的原始 PDF，请重新导入原文件；未改用摘要回答。')
    return source


def prepare_pdf(library, pid, output_dir, *, cancel_event=None, on_progress=None):
    source=original_pdf(library,pid)
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


def cached_pdf(library, pid, *, cancel_event=None, on_progress=None):
    """Render once per PDF content; publishing the manifest commits a complete cache."""
    source = original_pdf(library,pid)
    hasher = hashlib.sha256()
    with source.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            hasher.update(chunk)
    digest = hasher.hexdigest()
    cache = source.parent / '.chat-pdf-cache' / digest
    with _CACHE_LOCK:
        manifest = cache / 'pages.json'
        try:
            pages = json.loads(manifest.read_text(encoding='utf-8'))
            if not isinstance(pages, list) or not pages:
                raise ValueError('Invalid cache')
            images = [str(cache / f'pdf-page-{i+1:04d}.jpg') for i in range(len(pages))]
            if not all(Path(image).is_file() for image in images):
                raise ValueError('Incomplete cache')
        except (OSError, ValueError):
            pages, images = prepare_pdf(library, pid, cache, cancel_event=cancel_event, on_progress=on_progress)
            temporary = cache / 'pages.tmp'
            temporary.write_text(json.dumps(pages, ensure_ascii=False), encoding='utf-8')
            temporary.replace(manifest)
    return pages, images, cache


def context_ready(cache, thread_id):
    if not thread_id:
        return False
    with _CACHE_LOCK:
        try:
            return thread_id in json.loads((cache / 'contexts.json').read_text(encoding='utf-8'))
        except (OSError, ValueError, TypeError):
            return False


def remember_context(cache, thread_id):
    with _CACHE_LOCK:
        path = cache / 'contexts.json'
        try:
            contexts = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(contexts, list):
                contexts = []
        except (OSError, ValueError):
            contexts = []
        contexts = [value for value in contexts if value != thread_id]
        contexts.append(thread_id)
        temporary = cache / 'contexts.tmp'
        temporary.write_text(json.dumps(contexts), encoding='utf-8')
        temporary.replace(path)


def prepare_in_background(library, pid):
    def run():
        try:
            cached_pdf(library, pid)
        except Exception:
            # Preparation is optional; asking retries and reports the actual error.
            import logging
            logging.getLogger(__name__).warning('Local paper chat preparation failed for %s', pid)
    threading.Thread(target=run, name='ezread-chat-pdf', daemon=True).start()
