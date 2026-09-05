"""CI-only document dependencies. No font is committed or uploaded as an artifact."""
import hashlib
from pathlib import Path
import shutil
import urllib.request
import matplotlib
REVISION='f8d157532fbfaeda587e826d4cd5b21a49186f7c'
BLOB_SHA='cba8a4783cc38574ac7cda52cae7d9b4241c07a5'
URL=f'https://raw.githubusercontent.com/notofonts/noto-cjk/{REVISION}/Serif/OTF/SimplifiedChinese/NotoSerifCJKsc-Regular.otf'
with urllib.request.urlopen(URL,timeout=60) as response:
    content=response.read(32*1024*1024)
if hashlib.sha1(f'blob {len(content)}\0'.encode()+content).hexdigest()!=BLOB_SHA:
    raise RuntimeError('Upstream open font content hash mismatch')
font=Path(matplotlib.get_data_path())/'fonts'/'ttf'/'NotoSerifCJKsc-Regular.otf'
font.write_bytes(content)
for cache in Path(matplotlib.get_cachedir()).glob('fontlist-v*.json'): cache.unlink()
from matplotlib import font_manager
font_manager.fontManager.addfont(str(font))
assert font_manager.FontProperties(fname=font).get_name()=='Noto Serif CJK SC'
for executable in ('xelatex','pdfinfo','pdftoppm','bash'):
    if not shutil.which(executable): raise RuntimeError(f'Missing validation executable: {executable}')
print('Document validation dependencies available; pinned font verified.')
