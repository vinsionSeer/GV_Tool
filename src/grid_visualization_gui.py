"""
网格测试点位可视化 - GUI 界面
使用 PySide6 框架，支持拖拽导入表格文件
"""

import sys
import os
import re
from io import BytesIO
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
import seaborn as sns
import numpy as np
import pandas as pd

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QFileDialog, QMessageBox, QSplitter, QFrame, QStatusBar, QGroupBox,
    QSpinBox, QDoubleSpinBox, QDialog, QPlainTextEdit, QLineEdit,
    QComboBox, QInputDialog, QMenu, QColorDialog
)
from PySide6.QtCore import Qt, QMimeData, Signal
from PySide6.QtGui import (
    QDragEnterEvent, QDropEvent, QIcon, QPixmap, QShortcut, QKeySequence,
)


# ────────────────────────────────────────────
# 中文字体配置
# ────────────────────────────────────────────



def setup_chinese_font():
    """尝试设置中文字体（跨平台：Windows 用黑体/雅黑，macOS 用苹方/冬青黑体），
    返回 FontProperties 或 None"""
    cache_dir = matplotlib.get_cachedir()
    for f in os.listdir(cache_dir):
        if 'font' in f.lower():
            try:
                os.remove(os.path.join(cache_dir, f))
            except Exception:
                pass
    fm.fontManager.__init__()
    if sys.platform == "win32":
        font_paths = [
            (r"C:\Windows\Fonts\simhei.ttf", "SimHei"),
            (r"C:\Windows\Fonts\msyh.ttc", "Microsoft YaHei"),
        ]
    else:  # macOS / Linux
        font_paths = [
            ("/System/Library/Fonts/PingFang.ttc", "PingFang SC"),
            ("/System/Library/Fonts/Hiragino Sans GB.ttc", "Hiragino Sans GB"),
            ("/System/Library/Fonts/STHeiti Light.ttc", "STHeiti Light"),
        ]
    for fp, fname in font_paths:
        if os.path.exists(fp):
            font_prop = fm.FontProperties(fname=fp)
            plt.rcParams["font.family"] = font_prop.get_name()
            return font_prop
    return None

CHINESE_FONT = setup_chinese_font()


# ────────────────────────────────────────────
# 网格布局定义（行列数与画布尺寸可配置，中心点随尺寸自动重算）
# ───────────────────────────────────────────



COL_SPACING = 2
ROW_SPACING = 3


class GridConfig:
    """网格画布配置：行数、列数、画布尺寸，以及由此推出的中心点位置。

    - 行数 n_rows：行号按 A、B、C… 自动生成（最多 26 行 A-Z）。
    - 列数 n_cols：列号按 1、2、3… 自动生成。
    - 中心点：行/列的几何中心。奇数尺寸时正好落在某个格子上（如 7×17 → D9），
      偶数尺寸时落在两行/两列之间。
    """

    MAX_ROWS = 26  # 行号用 A-Z 单字母表示

    def __init__(self, n_rows: int = 7, n_cols: int = 17,
                 fig_width: float = 16.0, fig_height: float = 9.0):
        self.n_rows = max(3, min(int(n_rows), self.MAX_ROWS))
        self.n_cols = max(3, int(n_cols))
        self.fig_width = float(fig_width)
        self.fig_height = float(fig_height)

        self.row_letters = [chr(ord("A") + i) for i in range(self.n_rows)]
        self.col_numbers = list(range(1, self.n_cols + 1))

        # 中心点坐标（以 (0,0) 为原点）
        self.center_row_idx = (self.n_rows - 1) / 2.0   # 7 行 → 3.0（D 行）
        self.center_col = (self.n_cols + 1) / 2.0       # 17 列 → 9.0

    @property
    def center_label(self) -> str:
        """中心点的可读描述，奇数尺寸为具体格号，偶数尺寸为两格之间。"""
        row_exact = (self.n_rows % 2 == 1)
        col_exact = (self.n_cols % 2 == 1)
        if row_exact and col_exact:
            return f"{self.row_letters[int(self.center_row_idx)]}{int(self.center_col)}"
        if row_exact:
            row_part = f"行={self.row_letters[int(self.center_row_idx)]}"
        else:
            r1 = self.row_letters[int(self.center_row_idx - 0.5)]
            r2 = self.row_letters[int(self.center_row_idx + 0.5)]
            row_part = f"行={r1}/{r2}之间"
        if col_exact:
            col_part = f"列={int(self.center_col)}"
        else:
            col_part = f"列={int(self.center_col - 0.5)}/{int(self.center_col + 0.5)}之间"
        return f"{row_part}，{col_part}"


def col_num_to_x(col_num: int, cfg: GridConfig) -> float:
    return (col_num - cfg.center_col) * COL_SPACING


def row_index_to_y(row_idx: int, cfg: GridConfig) -> float:
    return (cfg.center_row_idx - row_idx) * ROW_SPACING


def get_point_coord(point_id: str, cfg: GridConfig):
    if not point_id or len(point_id) < 2:
        return None
    letter = point_id[0].upper()
    try:
        col_num = int(point_id[1:])
    except ValueError:
        return None
    if letter not in cfg.row_letters:
        return None
    row_idx = cfg.row_letters.index(letter)
    if col_num < 1 or col_num > cfg.n_cols:
        return None
    x = col_num_to_x(col_num, cfg)
    y = row_index_to_y(row_idx, cfg)
    return (x, y)


def dataframe_to_data(df: pd.DataFrame):
    """从 DataFrame 解析 {点位ID: (成功次数, 失败次数)} 与备注 {点位: 备注}，
    返回 (data, notes, error)。备注列按列名（备注/note/remark/说明等）识别，
    无匹配列名时取第 4 列（若存在）。"""
    col_map = {}
    for col in df.columns:
        col_lower = str(col).strip().lower()
        if col_lower in ("点位", "point", "point_id", "编号", "位置"):
            col_map["point"] = col
        elif col_lower in ("成功次数", "success", "成功", "suc_count", "pass"):
            col_map["success"] = col
        elif col_lower in ("失败次数", "fail", "失败", "fail_count", "error"):
            col_map["fail"] = col

    if "point" not in col_map:
        if len(df.columns) >= 3:
            col_map["point"] = df.columns[0]
            col_map["success"] = df.columns[1]
            col_map["fail"] = df.columns[2]
        else:
            return None, {}, "无法识别表格列，请确保包含: 点位, 成功次数, 失败次数"

    # 备注列：按列名识别，否则取第 4 列
    note_col = None
    for col in df.columns:
        col_lower = str(col).strip().lower()
        if col_lower in ("备注", "note", "remark", "comment", "说明"):
            note_col = col
            break
    if note_col is None and len(df.columns) >= 4:
        note_col = df.columns[3]

    data = {}
    notes = {}
    for _, row in df.iterrows():
        point_id = str(row[col_map["point"]]).strip()
        if not point_id or point_id.lower() in ("nan", "none", ""):
            continue
        success = int(row[col_map.get("success", df.columns[1])]) if col_map.get("success") else 0
        fail = int(row[col_map.get("fail", df.columns[2])]) if col_map.get("fail") else 0
        data[point_id] = (success, fail)
        if note_col is not None:
            v = row[note_col]
            notes[point_id] = "" if pd.isna(v) else str(v).strip()

    if not data:
        return None, {}, "未解析到有效点位数据"
    return data, notes, None


def natural_sort_key(point_id: str):
    """点位 ID 自然排序键：字母按字符串、数字按数值（A2 < A10）"""
    parts = re.split(r"(\d+)", point_id)
    key = []
    for p in parts:
        if not p:
            continue
        if p.isdigit():
            key.append((1, int(p)))
        else:
            key.append((0, p))
    return key


def parse_vertical_stacked(text: str):
    """识别纵向堆叠格式（每行一个值），支持带备注或不带备注的两种形态：

    形态一（表头连续，数据按 点位/成功/失败[ /备注] 三或四行一组循环）：
        点位
        成功次数
        失败次数
        备注          <- 可选
        A6
        0
        3
        备注内容

    形态二（段式：点位段 / 成功次数段 / 失败次数段[ /备注段]）：
        点位
        A6
        A7
        成功次数
        0
        3
        失败次数
        3
        0
        备注          <- 可选
        备注内容

    匹配时返回 (data, notes)，不匹配返回 None
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None

    def find_header(header):
        for i, ln in enumerate(lines):
            if ln == header:
                return i
        return -1

    i_point = find_header("点位")
    i_succ = find_header("成功次数")
    i_fail = find_header("失败次数")
    if i_point < 0 or i_succ < 0 or i_fail < 0:
        return None
    if not (i_point < i_succ < i_fail):
        return None

    i_note = find_header("备注")

    # 形态一：表头连续出现，数据行按 点位/成功/失败[ /备注] 循环
    if i_succ == i_point + 1 and i_fail == i_succ + 1 and             (i_note == i_fail + 1 or i_note < 0):
        if i_note == i_fail + 1:
            group = 4
            rows = lines[i_note + 1:]
        else:
            group = 3
            rows = lines[i_fail + 1:]
        if not rows or len(rows) % group != 0:
            return None
        data = {}
        notes = {}
        for k in range(0, len(rows), group):
            pid = rows[k]
            if not pid or pid.lower() in ("nan", "none", ""):
                continue
            try:
                success = int(rows[k + 1])
                fail = int(rows[k + 2])
            except ValueError:
                return None  # 数值行含非整数 → 不是该格式
            data[pid] = (success, fail)
            if group == 4:
                notes[pid] = rows[k + 3]
        return (data, notes) if data else None

    # 形态二：段式（点位段 / 成功段 / 失败段[ /备注段]）
    points = lines[i_point + 1:i_succ]
    succs = lines[i_succ + 1:i_fail]
    if i_note > i_fail:
        fails = lines[i_fail + 1:i_note]
        notes_lines = lines[i_note + 1:]
        has_note = True
    else:
        fails = lines[i_fail + 1:]
        notes_lines = []
        has_note = False
    n = min(len(points), len(succs), len(fails))
    if n == 0:
        return None

    data = {}
    notes = {}
    for k in range(n):
        pid = points[k]
        if not pid or pid.lower() in ("nan", "none", ""):
            continue
        try:
            success = int(succs[k])
            fail = int(fails[k])
        except ValueError:
            return None  # 数值段含非整数 → 不是该格式
        data[pid] = (success, fail)
        if has_note and k < len(notes_lines):
            notes[pid] = notes_lines[k]
    return (data, notes) if data else None


def _wrap_note_text(text: str, max_cw: int = 16) -> list:
    """按显示宽度把长文本切成多行（中文/全角按 2 宽度计），
    用于报告总结表格备注列的自动换行显示"""
    text = str(text)
    lines, cur, w = [], "", 0
    for ch in text:
        cw = 2 if ord(ch) > 0x2E80 else 1
        if w + cw > max_cw and cur:
            lines.append(cur)
            cur, w = ch, cw
        else:
            cur += ch
            w += cw
    if cur:
        lines.append(cur)
    return lines or [""]


def _table_font_path():
    """返回可用的中文字体文件路径（与 setup_chinese_font 同源）"""
    if sys.platform == "win32":
        cands = [r"C:\Windows\Fonts\simhei.ttf",
                 r"C:\Windows\Fonts\msyh.ttc"]
    else:
        cands = ["/System/Library/Fonts/PingFang.ttc",
                 "/System/Library/Fonts/Hiragino Sans GB.ttc",
                 "/System/Library/Fonts/STHeiti Light.ttc"]
    for p in cands:
        if os.path.exists(p):
            return p
    return None


def _compose_compare_image(paths):
    """把 2-4 张图片等比缩放拼接为对比图。

    - 2 张：左右并排；3-4 张：按 2 列网格排列（3 张时最后一行居中）。
    - 每张按统一格宽等比缩放、格内垂直居中，格间留浅灰间隙便于对比。
    返回 PIL Image。
    """
    from PIL import Image
    imgs = [Image.open(p).convert("RGB") for p in paths]
    n = len(imgs)
    cols = 2
    rows = (n + 1) // 2
    pad = 8
    cell_w = max(im.width for im in imgs)
    heights = [int(im.height * cell_w / im.width) for im in imgs]
    cell_h = max(heights)
    scaled = []
    for im, h in zip(imgs, heights):
        im2 = im.resize((cell_w, h), Image.LANCZOS)
        canvas_im = Image.new("RGB", (cell_w, cell_h), "white")
        canvas_im.paste(im2, (0, (cell_h - h) // 2))
        scaled.append(canvas_im)
    W = cell_w * cols + pad * (cols + 1)
    H = cell_h * rows + pad * (rows + 1)
    canvas = Image.new("RGB", (W, H), "#DDDDDD")
    for k, im in enumerate(scaled):
        r, c = divmod(k, cols)
        x = pad + c * (cell_w + pad)
        y = pad + r * (cell_h + pad)
        canvas.paste(im, (x, y))
    return canvas


def _render_table_image(headers, wrapped_rows, cell_style, chart_title,
                        dpi=200):
    """PIL 手绘报告表格：表头蓝底加粗、隔行浅灰、备注自动换行、
    行高按文本行数自适应、单元格文字/背景颜色同步、左上角标题。

    不使用 matplotlib ax.table（其行高缩放 set_height 在多行场景下
    会导致部分行文字丢失），改用 PIL 逐格绘制，输出与拼接链路一致。
    """
    from PIL import Image, ImageDraw, ImageFont
    fp = _table_font_path()
    try:
        f_title = ImageFont.truetype(fp, 30) if fp else None
        f_head = ImageFont.truetype(fp, 24) if fp else None
        f_body = ImageFont.truetype(fp, 24) if fp else None
    except Exception:
        f_title = f_head = f_body = None

    W = int(8.5 * dpi)
    ratios = [1.15, 1.55, 1.55, 4.75]
    total_r = float(sum(ratios))
    cols = []
    x = 0
    for r in ratios:
        cols.append((x, x + int(W * r / total_r)))
        x += int(W * r / total_r)
    pad = int(dpi * 0.05)
    cell_pad = int(dpi * 0.035)
    line_h = int(dpi * 0.17)   # 文本行高
    title_h = int(dpi * 0.24)
    head_h = int(dpi * 0.22)

    img = Image.new("RGB", (W, 10), "white")
    draw = ImageDraw.Draw(img)

    def _cell_lines(text, col_w, font):
        """按列宽测量逐字符换行（中文等宽测量）"""
        text = str(text) if text is not None else ""
        if not text:
            return [""]
        max_w = col_w - 2 * cell_pad
        lines, cur, w = [], "", 0.0
        for ch in text:
            cw = draw.textlength(ch, font=font)
            if w + cw > max_w and cur:
                lines.append(cur)
                cur, w = ch, cw
            else:
                cur += ch
                w += cw
        if cur:
            lines.append(cur)
        return lines or [""]

    def _center(x0, y0, x1, y1, text, font, fill):
        bb = draw.textbbox((0, 0), text, font=font)
        tw = bb[2] - bb[0]
        th = bb[3] - bb[1]
        draw.text((x0 + (x1 - x0 - tw) / 2, y0 + (y1 - y0 - th) / 2),
                  text, font=font, fill=fill)

    # 预计算各行行数与总高
    prepared = []
    total_h = title_h + head_h
    for row_cells in wrapped_rows:
        row, note = row_cells
        cell_lines = []
        max_nl = 1
        for j in range(len(headers)):
            if j == 3 and len(row) > 3:
                text = note
            else:
                text = row[j] if j < len(row) else ""
            ls = _cell_lines(text, cols[j][1] - cols[j][0], f_body)
            cell_lines.append(ls)
            max_nl = max(max_nl, len(ls))
        prepared.append((row, cell_lines, max_nl))
        total_h += max_nl * line_h
    total_h += int(dpi * 0.12)
    img = Image.new("RGB", (W, total_h), "white")
    draw = ImageDraw.Draw(img)

    # 标题（左上，同步网格坐标图标题）
    if chart_title:
        draw.text((pad, int(dpi * 0.05)), chart_title, font=f_title,
                  fill="#000000")

    y = title_h
    # 表头
    for j, htext in enumerate(headers):
        x0, x1 = cols[j]
        draw.rectangle([x0, y, x1, y + head_h], fill="#D9E2F3",
                       outline="#333333", width=1)
        if f_head:
            _center(x0, y, x1, y + head_h, htext, f_head, "#000000")
    y += head_h

    # 数据行（隔行底色 + 单元格颜色高亮同步）
    for idx, (row, cell_lines, max_nl) in enumerate(prepared, start=1):
        row_h = max_nl * line_h
        point_id = row[0] if row else ""
        base_bg = "#F5F5F5" if idx % 2 == 0 else "#FFFFFF"
        for j in range(len(headers)):
            x0, x1 = cols[j]
            bg, fg = base_bg, "#000000"
            style = cell_style.get((point_id, j))
            if style:
                if style.get("bg"):
                    bg = style["bg"]
                if style.get("fg"):
                    fg = style["fg"]
            draw.rectangle([x0, y, x1, y + row_h], fill=bg,
                           outline="#333333", width=1)
            lines = cell_lines[j]
            block_h = len(lines) * line_h
            ty = y + (row_h - block_h) / 2
            for ln in lines:
                _center(x0, ty, x1, ty + line_h, ln, f_body, fg)
                ty += line_h
        y += row_h
    return img


_POINT_PAT = re.compile(r"^[A-Za-z]{1,2}\d{1,3}$")


def _split_into_point_rows(fields: list) -> list:
    """把字段序列按点位模式（字母+数字）分割成行。

    点位字段标志着新一行开始；其后字段依次为成功次数/失败次数/备注，
    备注中若含空格则继续追加。支持空备注与长备注混合的场景，例如：
    'A6 3 0  A7 3 0  A8 ... B6 2 1 对不准 ...'
    只保留 >= 3 字段的完整行（点位+成功次数+失败次数），末尾不完整组丢弃。"""
    rows = []
    cur = None
    for f in fields:
        if _POINT_PAT.match(f):
            if cur and len(cur) >= 3:
                rows.append(cur)
            cur = [f]
        elif cur is not None:
            if len(cur) < 3:
                cur.append(f)
            elif len(cur) == 3:
                cur.append(f)
            else:
                cur[3] = cur[3] + " " + f
    if cur and len(cur) >= 3:
        rows.append(cur)
    return rows


def _split_paste_row(line: str):
    """单行智能分隔：Tab > 逗号 > ≥2 空格 > 单空格。返回 cell 列表或 None。

    备注列若混入空格分隔的冗余内容（含表头词"点位"）则截断，避免框选复制
    叠加粘贴时污染备注（如 "失败   点位 成功次数 ..." → "失败"）。"""
    line = line.strip()
    if not line:
        return None
    if "\t" in line:
        cells = [c.strip() for c in line.split("\t")]
        if len(cells) >= 4 and "点位" in cells[3] and re.search(r"\s{2,}", cells[3]):
            cells[3] = cells[3][:cells[3].find("点位")].strip()
            cells = cells[:4]
    elif "," in line:
        cells = [c.strip() for c in line.split(",")]
    else:
        wide = re.split(r"\s{2,}", line)
        if len(wide) >= 2:
            cells = [c.strip() for c in wide]
        else:
            cells = line.split()
    cells = [c for c in cells if c != ""]
    return cells if len(cells) >= 2 else None


def _align_row_widths(rows: list) -> list:
    """把数据行对齐到表头列数：短行补空列（备注），长行多余字段拼入末列。

    处理 Excel/飞书复制后空备注格无内容的场景（表头 4 列 + 数据行 3/4 列混合）。"""
    n = len(rows[0])
    out = [rows[0]]
    for r in rows[1:]:
        if len(r) == n:
            out.append(list(r))
        elif len(r) < n:
            out.append(list(r) + [""] * (n - len(r)))
        else:
            out.append(list(r[:n - 1]) + [" ".join(r[n - 1:])])
    return out


def _parse_paste_rows(text: str):
    """解析粘贴文本为表格行：逐行智能分隔 + 单行 4 字段循环 + 点位模式分割。

    返回 [[表头...], [行...], ...] 或 None。点位模式分割用于框选复制粘贴到
    单行输入框且备注含空格 / 空备注混合的场景：
    '点位 成功次数 失败次数 备注 A6 3 0  A7 3 0  ... B6 2 1 对不准 ...'
    数据行无表头时自动补标准表头。"""
    rows = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        words = line.split()
        # 严格 4 字段循环（全部数据字段不含空格且数量对齐）
        if len(words) >= 8 and (len(words) - 4) % 4 == 0                 and words[0] == "点位" and words[1] == "成功次数" and words[2] == "失败次数":
            head = words[:4]
            body = words[4:]
            blocks = [head] + [body[i:i + 4] for i in range(0, len(body), 4)]
            for b in blocks:
                if b not in rows:
                    rows.append(b)
            continue
        # 点位模式分割（支持空备注/长备注混合、表头可省略）
        if len(words) >= 3 and any(_POINT_PAT.match(w) for w in words):
            point_rows = _split_into_point_rows(words)
            if point_rows:
                rows.extend(point_rows)
                continue
        cells = _split_paste_row(line)
        if cells is not None:
            rows.append(cells)
        elif len(words) == 1:
            # 收集单字段行（每行一个单元格的竖排复制格式），供后续循环重建
            rows.append(words)
    if not rows:
        return None
    # 数据行全部为单字段 → Excel/飞书竖排复制（每行一个单元格），
    # 按 点位/成功次数/失败次数[/备注] 循环重建，修复列数不匹配报错
    has_wide_header = bool(rows[0]) and len(rows[0]) > 1 and rows[0][0] == "点位"
    body = rows[1:] if has_wide_header else rows
    if body and all(len(r) == 1 for r in body) and len(body) >= 3:
        flat = [r[0] for r in body]
        # 竖排表头识别：开头为 点位/成功次数/失败次数[/备注]（表头词也是单字段行）
        hdr_n = 0
        if flat[:4] == ["点位", "成功次数", "失败次数", "备注"]:
            hdr_n = 4
        elif flat[:3] == ["点位", "成功次数", "失败次数"]:
            hdr_n = 3
        rebuilt = _split_into_point_rows(flat[hdr_n:] if hdr_n else flat)
        if rebuilt:
            if hdr_n:
                aligned = [r + [""] * (hdr_n - len(r)) for r in rebuilt]
                return [["点位", "成功次数", "失败次数", "备注"][:hdr_n]] + aligned
            if has_wide_header:
                n_h = len(rows[0])
                aligned = [r + [""] * (n_h - len(r)) for r in rebuilt]
                return [rows[0]] + aligned
            # 无表头：表头列数跟随数据行字段数（3 列无备注 / 4 列含备注）
            head = ["点位", "成功次数", "失败次数"]
            if len(rebuilt[0]) >= 4:
                head = ["点位", "成功次数", "失败次数", "备注"]
            return [head] + rebuilt
    if rows[0] and rows[0][0] == "点位":
        return _align_row_widths(rows)
    # 数据行无表头时自动补标准表头（列数跟随最宽数据行，短行补空备注）
    max_w = max(len(r) for r in rows)
    head = ["点位", "成功次数", "失败次数"]
    if max_w >= 4:
        head = head + ["备注"]
    return _align_row_widths([head] + rows)


def _build_cf_html(fragment: str) -> bytes:
    """构造标准 Windows CF_HTML 剪贴板数据（UTF-8 字节）。

    Qt 的 QMimeData.setHtml() 在 Windows 上生成的 CF_HTML 头不规范，
    粘贴到 WPS/CSV 等应用时会把 "Version:1.0 StartHTML:..." 头当文本显示。
    这里按 Windows CF_HTML 规范手工构造：头部各偏移值指向真实片段位置，
    目标应用（Excel/WPS/飞书）粘贴时只取 StartFragment..EndFragment 之间内容。
    """
    doc = ("<html><body><!--StartFragment-->" + fragment +
           "<!--EndFragment--></body></html>")
    prefix = ("Version:1.0\r\n"
              "StartHTML:{:010d}\r\n"
              "EndHTML:{:010d}\r\n"
              "StartFragment:{:010d}\r\n"
              "EndFragment:{:010d}\r\n")
    header_len = len(prefix.format(0, 0, 0, 0).encode("utf-8"))
    start_html = header_len
    start_frag = start_html + len("<html><body><!--StartFragment-->".encode("utf-8"))
    end_frag = start_frag + len(fragment.encode("utf-8"))
    end_html = end_frag + len("<!--EndFragment--></body></html>".encode("utf-8"))
    header = prefix.format(start_html, end_html, start_frag, end_frag)
    return (header + doc).encode("utf-8")


def _set_clipboard_win32(data_map: dict):
    """用 Win32 API 原生写入系统剪贴板。

    data_map: {格式名或格式id: bytes 或 str}。str 按 UTF-16LE 写入（CF_UNICODETEXT）。
    绕过 Qt 的 text/html→CF_HTML 转换层（其生成的 CF_HTML 头不规范，
    粘贴到 WPS/CSV 时会把 "Version:1.0 StartHTML:..." 头当文本显示）。
    """
    import ctypes
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    # 显式声明参数/返回值类型：HGLOBAL 句柄是 64 位指针，
    # 默认 c_int(32位) 传参会把句柄截断导致 GlobalLock 失败。
    user32.OpenClipboard.argtypes = [ctypes.c_void_p]
    user32.RegisterClipboardFormatW.argtypes = [ctypes.c_wchar_p]
    user32.RegisterClipboardFormatW.restype = ctypes.c_uint
    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    user32.SetClipboardData.restype = ctypes.c_void_p
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]

    formats = {}
    for name, payload in data_map.items():
        if isinstance(name, str):
            fid = user32.RegisterClipboardFormatW(name)
        else:
            fid = name
        if isinstance(payload, str):
            payload = payload.encode("utf-16-le") + b"\x00\x00"
        formats[fid] = payload

    if not user32.OpenClipboard(None):
        return
    try:
        user32.EmptyClipboard()
        for fid, payload in formats.items():
            size = len(payload)
            h = kernel32.GlobalAlloc(0x0042, size)  # GMEM_MOVEABLE | GMEM_ZEROINIT
            if not h:
                continue
            p = kernel32.GlobalLock(h)
            if not p:
                continue
            try:
                buf = ctypes.create_string_buffer(payload, size)
                ctypes.memmove(ctypes.c_void_p(p), buf, size)
            finally:
                kernel32.GlobalUnlock(h)
            user32.SetClipboardData(fid, h)
    finally:
        user32.CloseClipboard()


def _copy_to_clipboard(html_fragment: str, plain_text: str):
    """写剪贴板表格内容（HTML + 纯文本双通道）：

    - Windows：原生写入 "HTML Format"（标准 CF_HTML，Excel/WPS 用）、
      "text/html"（网页应用/飞书云文档用）、CF_UNICODETEXT（记事本/CSV 用）；
    - 其他平台：回退 Qt MIME（setHtml + setText）。
    """
    if sys.platform == "win32":
        _set_clipboard_win32({
            "HTML Format": _build_cf_html(html_fragment),
            "text/html": html_fragment.encode("utf-8"),
            13: plain_text,  # CF_UNICODETEXT
        })
    else:
        mime = QMimeData()
        mime.setHtml(html_fragment)
        mime.setText(plain_text)
        QApplication.clipboard().setMimeData(mime)


def read_csv_any_encoding(file_path: str):
    """读取 CSV 并自动识别编码：

    依次尝试 UTF-8 BOM / UTF-8 / GB18030（含 GBK，Windows 中文 Excel 另存的 CSV 常用）/ Latin-1。
    解决中文 CSV 因 UTF-8 硬编码解码失败（invalid start byte）的问题。
    """
    from io import StringIO
    with open(file_path, "rb") as f:
        raw = f.read()
    last_err = None
    for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk", "latin-1"):
        try:
            text = raw.decode(enc)
            return pd.read_csv(StringIO(text))
        except UnicodeDecodeError as e:
            last_err = e
            continue
        except Exception:
            raise  # 解码成功但 pandas 解析失败：属格式问题，向上抛出
    raise last_err if last_err else UnicodeDecodeError("csv decode failed")


def read_table_data(file_path: str):
    """从表格文件读取测试数据"""
    try:
        ext = os.path.splitext(file_path)[1].lower()
        if ext in (".xlsx", ".xls"):
            df = pd.read_excel(file_path, engine="openpyxl" if ext == ".xlsx" else None)
        elif ext == ".csv":
            df = read_csv_any_encoding(file_path)
        else:
            return None, f"不支持的文件格式: {ext}"
        return dataframe_to_data(df)
    except Exception as e:
        return None, {}, str(e)


def draw_grid_visualization(data: dict, cfg: GridConfig,
                            show_other_names: bool = False,
                            title: str = None,
                            dpi: int = 100) -> Figure:
    """绘制网格坐标图，返回 Figure 对象

    data: {点位ID: (成功次数, 失败次数)}
    cfg:  网格画布配置（行列数、画布尺寸、中心点）
    show_other_names: 是否在灰色未测试点位旁标注点位名称
    title: 自定义标题；为 None 时使用默认标题（含中心点）
    dpi:  渲染分辨率；输出报告时传 200 保证清晰度
    """
    if title is None:
        title = f"网格测试点位可视化 (中心点: {cfg.center_label})"
    fig = Figure(figsize=(cfg.fig_width, cfg.fig_height), dpi=dpi)
    ax = fig.add_subplot(111)

    # 绘制网格线
    for col in cfg.col_numbers:
        x = col_num_to_x(col, cfg)
        ax.axvline(x=x, color="#E0E0E0", linewidth=0.5, zorder=1)
    for i in range(cfg.n_rows):
        y = row_index_to_y(i, cfg)
        ax.axhline(y=y, color="#E0E0E0", linewidth=0.5, zorder=1)

    # 中心十字线
    ax.axhline(y=0, color="#333333", linewidth=1.5, zorder=2)
    ax.axvline(x=0, color="#333333", linewidth=1.5, zorder=2)

    # 绘制所有网格点（灰色背景点）
    for row_idx, letter in enumerate(cfg.row_letters):
        for col_num in cfg.col_numbers:
            x = col_num_to_x(col_num, cfg)
            y = row_index_to_y(row_idx, cfg)
            ax.scatter(x, y, s=80, facecolors="#D3D3D3",
                       edgecolors="#808080", linewidths=0.8, zorder=3)
            # 一键标注其余（未测试）点位名称：标在灰点下方，与上一行测试点的标签错开
            point_id = f"{letter}{col_num}"
            if show_other_names and point_id not in data:
                _ot = ax.annotate(
                    point_id,
                    xy=(x, y),
                    xytext=(x, y - 0.9),
                    color="#9E9E9E",
                    ha="center",
                    va="top",
                    fontproperties=CHINESE_FONT,
                    zorder=4,
                )
                _ot.set_fontsize(9)

    # 绘制测试点位
    for point_id, (success, fail) in data.items():
        coord = get_point_coord(point_id, cfg)
        if coord is None:
            continue
        x, y = coord

        dot_color = "#FF8C00" if fail > 0 else "#4169E1"
        ax.scatter(x, y, s=200, facecolors=dot_color,
                   edgecolors="black", linewidths=1.8, zorder=5)

        # 点位名称（紧贴点位下方）
        _nm = ax.annotate(
            f"{point_id}",
            xy=(x, y),
            xytext=(x, y - 0.5),
            color="#333333",
            fontweight="bold",
            ha="center",
            va="top",
            fontproperties=CHINESE_FONT,
            zorder=6,
        )
        _nm.set_fontsize(12)
        _nm.set_fontweight("bold")

        # (成功/失败)（紧贴点位名称下方）
        label_y = y - 1.25
        _t1 = ax.annotate("(", xy=(x, y), xytext=(x - 0.55, label_y),
                          color="#666666", ha="center", va="center",
                          fontproperties=CHINESE_FONT, zorder=6)
        _t2 = ax.annotate(f"{success}", xy=(x, y), xytext=(x - 0.15, label_y),
                          fontweight="bold", color="#228B22",
                          ha="center", va="center",
                          fontproperties=CHINESE_FONT, zorder=6)
        _t3 = ax.annotate("/", xy=(x, y), xytext=(x + 0.15, label_y),
                          color="#666666", ha="center", va="center",
                          fontproperties=CHINESE_FONT, zorder=6)
        fail_weight = "bold" if fail > 0 else "normal"
        _t4 = ax.annotate(f"{fail}", xy=(x, y), xytext=(x + 0.50, label_y),
                          fontweight=fail_weight, color="#DC143C",
                          ha="center", va="center",
                          fontproperties=CHINESE_FONT, zorder=6)
        _t5 = ax.annotate(")", xy=(x, y), xytext=(x + 0.85, label_y),
                          color="#666666", ha="center", va="center",
                          fontproperties=CHINESE_FONT, zorder=6)
        _t1.set_fontsize(13)
        _t2.set_fontsize(13)
        _t2.set_fontweight("bold")
        _t3.set_fontsize(13)
        _t4.set_fontsize(13)
        _t4.set_fontweight(fail_weight)
        _t5.set_fontsize(13)

    # 坐标轴
    x_tick_positions = [col_num_to_x(col, cfg) for col in cfg.col_numbers]
    x_tick_labels = [str(col) for col in cfg.col_numbers]
    y_tick_positions = [row_index_to_y(i, cfg) for i in range(cfg.n_rows)]
    y_tick_labels = cfg.row_letters

    ax.set_xticks(x_tick_positions)
    ax.set_xticklabels(x_tick_labels, fontsize=12)
    ax.set_yticks(y_tick_positions)
    _ylabels = ax.set_yticklabels(y_tick_labels,
                                  fontproperties=CHINESE_FONT)
    for _l in _ylabels:
        _l.set_fontsize(14)

    # 坐标范围随行列数自适应，四周留一个间距的边距
    x_left = col_num_to_x(1, cfg) - COL_SPACING
    x_right = col_num_to_x(cfg.n_cols, cfg) + COL_SPACING
    y_top = row_index_to_y(0, cfg) + ROW_SPACING
    y_bottom = row_index_to_y(cfg.n_rows - 1, cfg) - ROW_SPACING
    ax.set_xlim(x_left, x_right)
    ax.set_ylim(y_bottom, y_top)

    ax.set_title(title, pad=18, fontproperties=CHINESE_FONT)
    ax.title.set_fontsize(17)
    ax.title.set_fontweight("bold")

    fig.tight_layout()

    # 统计信息框：左上角显示 总点位/成功点位/失败点位，右上角显示 成功次数/失败次数/成功率
    total_success = sum(s for s, _ in data.values())
    total_fail = sum(f for _, f in data.values())
    success_points = sum(1 for s, _ in data.values() if s > 0)
    fail_points = sum(1 for _, f in data.values() if f > 0)
    total = total_success + total_fail
    rate = total_success / total * 100 if total > 0 else 0
    left_stats = (f"总点位：{len(data)}    成功点位：{success_points}    "
                  f"失败点位：{fail_points}")
    right_stats = (f"成功次数：{total_success}    失败次数：{total_fail}    "
                   f"成功率：{rate:.1f}%")

    # 先渲染一次，取标题的实际中心高度，把统计框对齐到同一水平线
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    fig.set_canvas(FigureCanvasAgg(fig))
    fig.canvas.draw()
    title_bbox = ax.title.get_window_extent(fig.canvas.get_renderer())
    title_cy_fig = (title_bbox.y0 + title_bbox.y1) / 2.0 / fig.bbox.height
    _lt = fig.text(0.012, title_cy_fig, left_stats, ha="left", va="center",
                   fontproperties=CHINESE_FONT, zorder=7,
                   bbox=dict(boxstyle="round,pad=0.4", facecolor="white",
                             edgecolor="#AAAAAA", alpha=0.92))
    _lt.set_fontsize(12)
    _rt = fig.text(0.988, title_cy_fig, right_stats, ha="right", va="center",
                   fontproperties=CHINESE_FONT, zorder=7,
                   bbox=dict(boxstyle="round,pad=0.4", facecolor="white",
                             edgecolor="#AAAAAA", alpha=0.92))
    _rt.set_fontsize(12)

    return fig


def draw_heatmap_table(data: dict, cfg: GridConfig) -> Figure:
    """绘制热力图，返回 Figure 对象（标注格式：点位(数量)，如 A2(2)）"""
    row_labels = cfg.row_letters
    col_labels = cfg.col_numbers

    success_matrix = np.zeros((len(row_labels), len(col_labels)))
    fail_matrix = np.zeros((len(row_labels), len(col_labels)))

    for point_id, (success, fail) in data.items():
        coord = get_point_coord(point_id, cfg)
        if coord is None:
            continue
        letter = point_id[0].upper()
        col_num = int(point_id[1:])
        row_idx = cfg.row_letters.index(letter)
        col_idx = col_num - 1
        if 0 <= row_idx < len(row_labels) and 0 <= col_idx < len(col_labels):
            success_matrix[row_idx][col_idx] = success
            fail_matrix[row_idx][col_idx] = fail

    def build_annot(matrix):
        """生成两行标注矩阵：第一行点位，第二行次数（如 A2\\n(2)）；数量为 0 留空"""
        annot = np.empty(matrix.shape, dtype=object)
        for i, rl in enumerate(row_labels):
            for j, cl in enumerate(col_labels):
                v = int(matrix[i][j])
                annot[i][j] = f"{rl}{cl}\n({v})" if v > 0 else ""
        return annot

    # 列数越多格子越窄，标注字号相应缩小；分行后字号略加大以便阅读
    annot_fontsize = 10 if cfg.n_cols <= 12 else 9

    fig, (ax1, ax2) = plt.subplots(1, 2,
                                   figsize=(cfg.fig_width, max(4.5, cfg.fig_height * 0.55)))

    sns.heatmap(success_matrix, annot=build_annot(success_matrix), fmt="",
                cmap="Blues", xticklabels=col_labels, yticklabels=row_labels,
                ax=ax1, annot_kws={"fontsize": annot_fontsize},
                linewidths=0.8, linecolor="white",
                cbar_kws={"label": "成功次数"})
    ax1.set_title("成功次数热力图（点位+次数）", fontsize=13, fontweight="bold",
                  fontproperties=CHINESE_FONT)

    sns.heatmap(fail_matrix, annot=build_annot(fail_matrix), fmt="",
                cmap="Reds", xticklabels=col_labels, yticklabels=row_labels,
                ax=ax2, annot_kws={"fontsize": annot_fontsize},
                linewidths=0.8, linecolor="white",
                cbar_kws={"label": "失败次数"})
    ax2.set_title("失败次数热力图（点位+次数）", fontsize=13, fontweight="bold",
                  fontproperties=CHINESE_FONT)

    fig.tight_layout()
    return fig


# ────────────────────────────────────────────
# 拖拽区域组件
# ────────────────────────────────────────────



class DropZone(QFrame):
    """支持拖拽文件的区域"""

    file_dropped = Signal(str)  # 拖拽文件信号

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setMinimumHeight(150)
        self.setStyleSheet("""
            QFrame {
                border: 2px dashed #AAAAAA;
                border-radius: 10px;
                background-color: #F5F5F5;
            }
            QFrame:hover {
                border-color: #4169E1;
                background-color: #E8F0FE;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)

        self.icon_label = QLabel("\U0001F4C1")
        self.icon_label.setStyleSheet("font-size: 48px;")
        self.icon_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.icon_label)

        self.text_label = QLabel("拖拽表格文件到此处\n支持 .xlsx / .xls / .csv 格式")
        self.text_label.setStyleSheet("font-size: 14px; color: #666666;")
        self.text_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.text_label)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent):
        urls = event.mimeData().urls()
        if urls:
            file_path = urls[0].toLocalFile()
            self.file_dropped.emit(file_path)


class PasteZone(QFrame):
    """直接粘贴表格内容导入：在 Excel 复制后 Ctrl+V 到文本框，点按钮解析"""

    import_requested = Signal()  # 请求导入文本框中的内容

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(150)
        self.setStyleSheet("""
            QFrame {
                border: 2px dashed #AAAAAA;
                border-radius: 10px;
                background-color: #F5F5F5;
            }
            QFrame:hover {
                border-color: #FF8C00;
                background-color: #FFF3E6;
            }
            QPlainTextEdit {
                border: 1px solid #CCCCCC;
                border-radius: 4px;
                background-color: white;
                font-size: 12px;
            }
        """)

        layout = QVBoxLayout(self)

        # 标题行
        header = QHBoxLayout()
        icon_label = QLabel("\U0001F4CB")
        icon_label.setStyleSheet("font-size: 28px;")
        header.addWidget(icon_label)
        text_label = QLabel("粘贴表格内容到此处\n在 Excel 复制后按 Ctrl+V，再点下方按钮导入")
        text_label.setStyleSheet("font-size: 13px; color: #666666;")
        header.addWidget(text_label)
        header.addStretch()
        layout.addLayout(header)

        # 粘贴文本框
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText(
            "从 Excel 复制整列后粘贴到这里（支持 Tab 或逗号分隔），例如：\n"
            "点位\t成功次数\t失败次数\n"
            "D9\t2\t0\n"
            "C8\t2\t0")
        layout.addWidget(self.editor)

        # 导入按钮
        self.import_btn = QPushButton("解析并导入")
        self.import_btn.setStyleSheet("""
            QPushButton {
                background-color: #FF8C00;
                color: white;
                border: none;
                padding: 6px 16px;
                border-radius: 4px;
                font-size: 13px;
            }
            QPushButton:hover { background-color: #E07B00; }
        """)
        self.import_btn.clicked.connect(self.import_requested.emit)
        layout.addWidget(self.import_btn, alignment=Qt.AlignRight)


# ────────────────────────────────────────────
# 全屏图表弹窗
# ────────────────────────────────────────────



class FullscreenChartDialog(QDialog):
    """把当前图表（网格坐标图 / 热力图）以全屏方式弹出，便于查看细节

    图表先渲染为位图再显示（保持原比例缩放），完全绕开 matplotlib 的
    Qt 后端窗口管理，避免不同 matplotlib/PySide6 版本组合下
    showEvent 访问 windowHandle() 时崩溃的问题。
    """

    def __init__(self, fig: Figure, title: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        # 顶部栏：右侧退出按钮
        top_bar = QHBoxLayout()
        top_bar.addStretch()
        close_btn = QPushButton("退出全屏 (Esc)")
        close_btn.setStyleSheet("""
            QPushButton {
                background-color: #DC143C;
                color: white;
                border: none;
                padding: 8px 20px;
                border-radius: 4px;
                font-size: 14px;
            }
            QPushButton:hover { background-color: #B01030; }
        """)
        close_btn.clicked.connect(self.close)
        top_bar.addWidget(close_btn)
        root.addLayout(top_bar)

        # 图表渲染为位图（Agg 后端，与 Qt 窗口无关）
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        import numpy as _np
        from PIL import Image as _PILImage
        fig.set_canvas(FigureCanvasAgg(fig))
        fig.canvas.draw()
        _buf = _np.asarray(fig.canvas.buffer_rgba())[:, :, :3]
        _pil = _PILImage.fromarray(_buf).convert("RGB")
        _w, _h = _pil.size
        _qimg = QImage(_pil.tobytes(), _w, _h, _w * 3, QImage.Format_RGB888)
        self._pixmap = QPixmap.fromImage(_qimg)

        # Esc 退出全屏
        QShortcut(QKeySequence("Escape"), self, self.close)

    def paintEvent(self, event):
        """保持原比例缩放，整图居中完整显示"""
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#FFFFFF"))
        if getattr(self, "_pixmap", None) and not self._pixmap.isNull():
            scaled = self._pixmap.scaled(
                self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            x = (self.width() - scaled.width()) // 2
            y = (self.height() - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
        painter.end()


class CompareResultDialog(QDialog):
    """图片对比结果对话框（正式界面）

    选择 2-4 张图片拼接后弹出：标题栏（可关闭）+ 拼接图预览 +
    底部操作按钮（⛶ 全屏预览 / ⧉ 复制到剪贴板 / 💾 另存为）。
    """

    def __init__(self, pil_img, n, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"图片对比（{n} 张）")
        self.resize(900, 660)
        self.setMinimumSize(640, 480)
        self._img = pil_img.convert("RGB")
        w, h = self._img.size
        qimg = QImage(self._img.tobytes(), w, h, w * 3, QImage.Format_RGB888)
        self._pixmap = QPixmap.fromImage(qimg)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # 顶部栏：标题 + 关闭按钮
        top_bar = QHBoxLayout()
        title_lbl = QLabel(f"图片对比结果（{n} 张）")
        title_lbl.setStyleSheet("font-size: 16px; font-weight: bold; color: #333333;")
        top_bar.addWidget(title_lbl)
        top_bar.addStretch()
        close_btn = QPushButton("✕ 关闭")
        close_btn.setStyleSheet("""
            QPushButton {
                background-color: #9E9E9E;
                color: white;
                border: none;
                padding: 6px 16px;
                border-radius: 4px;
                font-size: 12px;
            }
            QPushButton:hover { background-color: #757575; }
        """)
        close_btn.clicked.connect(self.close)
        top_bar.addWidget(close_btn)
        root.addLayout(top_bar)

        # 中间：拼接图预览（保持比例缩放居中）
        self._preview = QLabel(self)
        self._preview.setAlignment(Qt.AlignCenter)
        self._preview.setStyleSheet(
            "background-color: #F5F5F5; border: 1px solid #E0E0E0; border-radius: 6px;")
        self._preview.setMinimumHeight(360)
        root.addWidget(self._preview, 1)
        self._update_preview()

        # 底部：三个操作按钮
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        fs_btn = QPushButton("⛶ 全屏预览")
        fs_btn.setStyleSheet("""
            QPushButton {
                background-color: #4169E1; color: white; border: none;
                padding: 8px 22px; border-radius: 4px; font-size: 13px;
            }
            QPushButton:hover { background-color: #3155B5; }
        """)
        fs_btn.clicked.connect(self._open_fullscreen)
        btn_row.addWidget(fs_btn)
        copy_btn = QPushButton("⧉ 复制到剪贴板")
        copy_btn.setStyleSheet("""
            QPushButton {
                background-color: #FF8C00; color: white; border: none;
                padding: 8px 22px; border-radius: 4px; font-size: 13px;
            }
            QPushButton:hover { background-color: #E07B00; }
        """)
        copy_btn.clicked.connect(self._copy)
        btn_row.addWidget(copy_btn)
        save_btn = QPushButton("💾 另存为")
        save_btn.setStyleSheet("""
            QPushButton {
                background-color: #228B22; color: white; border: none;
                padding: 8px 22px; border-radius: 4px; font-size: 13px;
            }
            QPushButton:hover { background-color: #1A6B1A; }
        """)
        save_btn.clicked.connect(self._save)
        btn_row.addWidget(save_btn)
        root.addLayout(btn_row)

    def _update_preview(self):
        if self._pixmap and not self._pixmap.isNull():
            self._preview.setPixmap(self._pixmap.scaled(
                self._preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_preview()

    def showEvent(self, event):
        super().showEvent(event)
        self._update_preview()

    def _copy(self):
        try:
            QApplication.clipboard().setImage(self._pixmap.toImage())
            QMessageBox.information(self, "图片对比", "对比图片已复制到剪贴板")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"复制对比图片失败:\n{str(e)}")

    def _save(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "保存对比图片", "图片对比.png", "PNG 图片 (*.png)")
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        try:
            self._img.save(path, dpi=(200, 200))
            QMessageBox.information(self, "图片对比", f"对比图片已保存:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"保存对比图片失败:\n{str(e)}")

    def _open_fullscreen(self):
        dlg = CompareFullscreenDialog(self._img, self)
        dlg.showFullScreen()
        dlg.exec()


class CompareFullscreenDialog(QDialog):
    """图片对比全屏预览：保持原比例整图居中显示，右上角提供
    「⬅ 返回对比」回到上一级对话框，Esc 或红色按钮退出全屏。"""

    def __init__(self, pil_img, parent=None):
        super().__init__(parent)
        self.setWindowTitle("图片对比 - 全屏预览")
        img = pil_img.convert("RGB")
        w, h = img.size
        qimg = QImage(img.tobytes(), w, h, w * 3, QImage.Format_RGB888)
        self._pixmap = QPixmap.fromImage(qimg)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        top = QHBoxLayout()
        back_btn = QPushButton("⬅ 返回对比")
        back_btn.setStyleSheet("""
            QPushButton {
                background-color: #607D8B; color: white; border: none;
                padding: 8px 20px; border-radius: 4px; font-size: 14px;
            }
            QPushButton:hover { background-color: #455A64; }
        """)
        back_btn.clicked.connect(self.close)
        top.addWidget(back_btn)
        top.addStretch()
        close_btn = QPushButton("退出全屏 (Esc)")
        close_btn.setStyleSheet("""
            QPushButton {
                background-color: #DC143C; color: white; border: none;
                padding: 8px 20px; border-radius: 4px; font-size: 14px;
            }
            QPushButton:hover { background-color: #B01030; }
        """)
        close_btn.clicked.connect(self.close)
        top.addWidget(close_btn)
        root.addLayout(top)
        QShortcut(QKeySequence("Escape"), self, self.close)

    def paintEvent(self, event):
        """顶部预留操作栏，图像在剩余区域保持比例缩放、整图居中"""
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#FFFFFF"))
        if self._pixmap and not self._pixmap.isNull():
            top_h = 60
            area_w = self.width()
            area_h = max(1, self.height() - top_h)
            scaled = self._pixmap.scaled(
                area_w, area_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            x = (area_w - scaled.width()) // 2
            y = top_h + (area_h - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
        painter.end()


# ────────────────────────────────────────────
# 点位成功率全屏明细
# ────────────────────────────────────────────



def point_rate_text(success: int, fail: int) -> str:
    """单个点位的成功率文本；总次数为 0 时显示 —"""
    total = success + fail
    if total <= 0:
        return "—"
    return f"{success / total * 100:.1f}%"


def point_rate_value(success: int, fail: int) -> float:
    """成功率数值（用于排序），总次数为 0 时按最低处理"""
    total = success + fail
    if total <= 0:
        return -1.0
    return success / total


class ReportPreviewDialog(QDialog):
    """报告总结拼接图全屏预览（保持原比例缩放到整图可见，Esc / Ctrl+W 关闭）"""

    def __init__(self, pil_img, parent=None, title="报告总结 - 全屏预览"):
        super().__init__(parent)
        self.setWindowTitle(title)
        img = pil_img.convert("RGB")
        w, h = img.size
        qimg = QImage(img.tobytes(), w, h, w * 3, QImage.Format_RGB888)
        self._pixmap = QPixmap.fromImage(qimg)

    def paintEvent(self, event):
        """按窗口尺寸保持原比例缩放，整图居中显示，不留裁切"""
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#2B2B2B"))
        if not self._pixmap.isNull():
            scaled = self._pixmap.scaled(
                self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            x = (self.width() - scaled.width()) // 2
            y = (self.height() - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
        painter.end()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Escape, Qt.Key_W):
            self.close()
        else:
            super().keyPressEvent(event)


class FullscreenRateDialog(QDialog):
    """全屏查看各点位成功率明细（点位 / 成功次数 / 失败次数 / 成功率）"""

    def __init__(self, order, data, notes=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("点位成功率 - 全屏")

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        # 顶部栏：标题 + 右侧退出按钮
        top_bar = QHBoxLayout()
        title_label = QLabel(f"点位成功率明细（共 {len(order)} 个点位）")
        title_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #333333;")
        top_bar.addWidget(title_label)
        top_bar.addStretch()
        export_btn = QPushButton("导出数据")
        export_btn.setStyleSheet("""
            QPushButton {
                background-color: #4169E1;
                color: white;
                border: none;
                padding: 8px 20px;
                border-radius: 4px;
                font-size: 14px;
            }
            QPushButton:hover { background-color: #3155B5; }
        """)
        export_btn.clicked.connect(lambda: self._export_data(order, data, notes))
        top_bar.addWidget(export_btn)
        close_btn = QPushButton("退出全屏 (Esc)")
        close_btn.setStyleSheet("""
            QPushButton {
                background-color: #DC143C;
                color: white;
                border: none;
                padding: 8px 20px;
                border-radius: 4px;
                font-size: 14px;
            }
            QPushButton:hover { background-color: #B01030; }
        """)
        close_btn.clicked.connect(self.close)
        top_bar.addWidget(close_btn)
        root.addLayout(top_bar)

        # 全屏明细表格（点位/成功次数/失败次数/备注/点位成功率）
        table = QTableWidget(len(order), 5)
        table.setHorizontalHeaderLabels(["点位", "成功次数", "失败次数", "备注", "点位成功率"])
        table.setStyleSheet("""
            QTableWidget {
                gridline-color: #E0E0E0;
                font-size: 14px;
            }
            QTableWidget::item:selected {
                background-color: #E8F0FE;
            }
            QHeaderView::section {
                background-color: #F0F0F0;
                padding: 6px;
                border: 1px solid #E0E0E0;
                font-weight: bold;
                font-size: 14px;
            }
        """)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        for row, point_id in enumerate(order):
            success, fail = data[point_id]
            table.setItem(row, 0, QTableWidgetItem(point_id))
            table.setItem(row, 1, QTableWidgetItem(str(success)))
            table.setItem(row, 2, QTableWidgetItem(str(fail)))
            table.setItem(row, 3, QTableWidgetItem(str((notes or {}).get(point_id, ""))))
            table.setItem(row, 4, QTableWidgetItem(point_rate_text(success, fail)))
        root.addWidget(table)

        # Esc 退出全屏
        QShortcut(QKeySequence("Escape"), self, self.close)

    def _export_data(self, order, data, notes=None):
        """把全屏明细导出为 CSV"""
        path, _ = QFileDialog.getSaveFileName(
            self, "导出数据", "点位测试数据.csv", "CSV 文件 (*.csv)")
        if not path:
            return
        try:
            write_data_csv(path, order, data, notes)
        except Exception as e:
            QMessageBox.critical(self, "错误", f"导出失败:\n{str(e)}")


# ────────────────────────────────────────────
# 支持批量复制粘贴的数据表
# ────────────────────────────────────────────



class EditableTableWidget(QTableWidget):
    """数据预览表格：支持 Ctrl+C / Ctrl+V 批量复制粘贴。

    - Ctrl+C：把选中区域复制为 Tab/换行分隔文本（可直接粘贴回 Excel）。
    - Ctrl+V：把剪贴板中的多行多列表格数据粘贴到当前单元格起始位置，
      自动扩展行数，并通过 paste_started / paste_committed 信号让主窗口
      批量同步（避免每个单元格触发一次全量刷新）。
    """

    paste_started = Signal()    # 批量写入前发出（主窗口置防递归标志）
    paste_committed = Signal()  # 批量写入完成后发出（主窗口同步数据与图表）

    def __init__(self, parent=None):
        super().__init__(parent)

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.Copy):
            self._copy_selection()
            event.accept()
            return
        if event.matches(QKeySequence.Paste):
            self._paste_from_clipboard()
            event.accept()
            return
        super().keyPressEvent(event)

    def _copy_selection(self):
        """把选中区域（首个连续选区）复制为 Tab 分隔文本"""
        ranges = self.selectedRanges()
        if not ranges:
            return
        rng = ranges[0]
        lines = []
        for r in range(rng.topRow(), rng.bottomRow() + 1):
            cells = []
            for c in range(rng.leftColumn(), rng.rightColumn() + 1):
                it = self.item(r, c)
                cells.append(it.text() if it else "")
            lines.append("\t".join(cells))
        QApplication.clipboard().setText("\n".join(lines))

    def _paste_from_clipboard(self):
        """把剪贴板中的表格文本粘贴到当前单元格，自动扩展行数"""
        text = QApplication.clipboard().text()
        if not text:
            return
        rows_text = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        while rows_text and rows_text[-1].strip() == "":
            rows_text.pop()
        if not rows_text:
            return
        grid = [line.split("\t") for line in rows_text]

        idx = self.currentIndex()
        start_row = idx.row() if idx.isValid() else 0
        start_col = idx.column() if idx.isValid() else 0

        need_rows = start_row + len(grid)
        if need_rows > self.rowCount():
            self.setRowCount(need_rows)

        # 批量写入：用信号包裹，主窗口只同步一次
        self.paste_started.emit()
        try:
            for i, row_vals in enumerate(grid):
                for j, val in enumerate(row_vals):
                    self.setItem(start_row + i, start_col + j,
                                 QTableWidgetItem(val))
        finally:
            self.paste_committed.emit()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.current_data = None
        self.current_file = None
        self.grid_cfg = GridConfig()          # 当前网格画布配置
        self.current_chart_type = "grid"      # 当前预览的图类型
        self.show_other_names = True          # 默认标注其余（未测试）点位名称
        self.chart_title = None               # 自定义网格图标题（None=默认标题）
        self._original_order = []             # 数据原始排列顺序
        self._sort_field = "original"         # 排序字段: original/point/success/fail
        self._sort_state = "original"         # 排序方向: original/asc/desc
        self._notes = {}                      # 各点位备注 {点位: 备注文本}
        self._updating = False                # 表格回填防护标志（避免 itemChanged 递归）
        self._undo_stack = []                 # 撤销栈：每项 (current_data, _notes, _original_order)
        self._cell_style = {}                # 单元格颜色样式：{(点位ID, 列号): {"fg": "#rrggbb", "bg": "#rrggbb"}}
        self.init_ui()

    def init_ui(self):
        self.setWindowTitle("网格点位可视化工具")
        self.setMinimumSize(1200, 800)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # ── 顶部：左侧拖拽文件 / 右侧粘贴内容，左右对称 ──
        top_import = QHBoxLayout()
        top_import.setSpacing(10)
        self.drop_zone = DropZone(self)
        self.drop_zone.file_dropped.connect(self.load_file)
        top_import.addWidget(self.drop_zone, 1)

        self.paste_zone = PasteZone(self)
        self.paste_zone.import_requested.connect(self.import_pasted_data)
        top_import.addWidget(self.paste_zone, 1)
        main_layout.addLayout(top_import)

        # ── 文件信息栏 ──
        file_info_layout = QHBoxLayout()
        self.file_label = QLabel("未选择文件")
        self.file_label.setStyleSheet("color: #666666; font-size: 12px;")
        file_info_layout.addWidget(self.file_label)
        file_info_layout.addStretch()

        self.browse_btn = QPushButton("浏览文件...")
        self.browse_btn.setStyleSheet("""
            QPushButton {
                background-color: #4169E1;
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #3155B5;
            }
        """)
        self.browse_btn.clicked.connect(self.browse_file)
        file_info_layout.addWidget(self.browse_btn)
        main_layout.addLayout(file_info_layout)

        # ── 画布设置栏：行列数、画布尺寸、中心点提示、其余点位名称开关 ──
        settings_group = QGroupBox("画布设置")
        settings_layout = QHBoxLayout(settings_group)

        settings_layout.addWidget(QLabel("行数:"))
        self.rows_spin = QSpinBox()
        self.rows_spin.setRange(3, GridConfig.MAX_ROWS)
        self.rows_spin.setValue(self.grid_cfg.n_rows)
        self.rows_spin.setToolTip("网格行数（A、B、C…，最多 26 行）")
        settings_layout.addWidget(self.rows_spin)

        settings_layout.addWidget(QLabel("列数:"))
        self.cols_spin = QSpinBox()
        self.cols_spin.setRange(3, 50)
        self.cols_spin.setValue(self.grid_cfg.n_cols)
        self.cols_spin.setToolTip("网格列数（1、2、3…，最多 50 列）")
        settings_layout.addWidget(self.cols_spin)

        settings_layout.addWidget(QLabel("画布宽(英寸):"))
        self.width_spin = QDoubleSpinBox()
        self.width_spin.setRange(6, 40)
        self.width_spin.setSingleStep(0.5)
        self.width_spin.setValue(self.grid_cfg.fig_width)
        settings_layout.addWidget(self.width_spin)

        settings_layout.addWidget(QLabel("高(英寸):"))
        self.height_spin = QDoubleSpinBox()
        self.height_spin.setRange(4, 30)
        self.height_spin.setSingleStep(0.5)
        self.height_spin.setValue(self.grid_cfg.fig_height)
        settings_layout.addWidget(self.height_spin)

        self.center_label = QLabel(f"中心点: {self.grid_cfg.center_label}")
        self.center_label.setStyleSheet(
            "color: #4169E1; font-weight: bold; padding: 0 10px;")
        settings_layout.addWidget(self.center_label)

        settings_layout.addStretch()

        self.names_btn = QPushButton("显示全部点位")
        self.names_btn.setCheckable(True)
        self.names_btn.setChecked(True)   # 默认开启：网格图直接标注全部点位名称
        self.names_btn.setToolTip("一键在全部（含灰色未测试）点位旁标注点位名称（默认开启，可点击关闭）")
        self.names_btn.setStyleSheet("""
            QPushButton {
                background-color: #F0F0F0;
                border: 1px solid #CCCCCC;
                padding: 6px 12px;
                border-radius: 4px;
                font-size: 12px;
            }
            QPushButton:checked {
                background-color: #FF8C00;
                color: white;
                border-color: #FF8C00;
            }
        """)
        self.names_btn.toggled.connect(self.toggle_other_names)
        settings_layout.addWidget(self.names_btn)

        main_layout.addWidget(settings_group)

        # 画布参数变化 → 重建配置并重绘
        for spin in (self.rows_spin, self.cols_spin,
                     self.width_spin, self.height_spin):
            spin.valueChanged.connect(self.apply_grid_settings)

        # ── 分割器：左侧数据表，右侧图表 ──
        splitter = QSplitter(Qt.Horizontal)
        main_layout.addWidget(splitter)

        # 左侧：数据预览
        data_group = QGroupBox("数据预览")
        data_layout = QVBoxLayout(data_group)
        self.data_table = EditableTableWidget()
        self.data_table.setStyleSheet("""
            QTableWidget {
                gridline-color: #E0E0E0;
                font-size: 12px;
            }
            QTableWidget::item:selected {
                background-color: #E8F0FE;
                color: #1976D2;
                font-weight: bold;
            }
            QHeaderView::section {
                background-color: #F0F0F0;
                padding: 4px;
                border: 1px solid #E0E0E0;
                font-weight: bold;
            }
        """)
        # 数据表创建/编辑按钮栏（新建空表、追加点位行）
        edit_bar = QHBoxLayout()
        self.new_table_btn = QPushButton("新建数据表")
        self.new_table_btn.setToolTip("清空当前数据，从空白表格开始录入新数据")
        self.add_row_btn = QPushButton("添加点位行")
        self.add_row_btn.setToolTip("批量添加点位行：点击后输入行数，一次追加多行空行")
        self.copy_table_btn = QPushButton("⧉ 复制表格")
        self.copy_table_btn.setToolTip("一键复制整个数据表（含列名与单元格内容）到剪贴板，"
                                       "可直接粘贴到 Excel 等其他表格软件并保留格式")
        self.report_btn = QPushButton("🖼 报告总结")
        self.report_btn.setToolTip("点击弹出功能菜单：全屏预览 / 复制到剪贴板 / 另存为")
        self.report_btn.setEnabled(False)
        self.highlight_btn = QPushButton("🎨 高亮显示设置")
        self.highlight_btn.setToolTip("下拉菜单：文字颜色 / 背景颜色 / 清除所有高亮显示（按点位名绑定，排序后跟随点位）")
        self.highlight_menu = QMenu(self)
        self.highlight_menu.addAction("🅰 文字颜色", self.apply_cell_text_color)
        self.highlight_menu.addAction("⬜ 背景颜色", self.apply_cell_bg_color)
        self.highlight_menu.addAction("🧹 清除所有高亮显示", self.clear_all_highlights)
        self.highlight_btn.setMenu(self.highlight_menu)
        self.paint_btn = QPushButton("🖌 格式刷")
        self.paint_btn.setCheckable(True)
        self.paint_btn.setToolTip("先选中带有文字/背景颜色的源单元格，点击本按钮激活格式刷，"
                                  "再点击目标单元格即可复制格式（可连续刷，再次点击按钮取消）")
        self.paint_btn.toggled.connect(self.toggle_format_painter)
        for btn in [self.new_table_btn, self.add_row_btn, self.copy_table_btn,
                    self.report_btn, self.highlight_btn, self.paint_btn]:
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #F0F0F0;
                    border: 1px solid #CCCCCC;
                    padding: 5px 12px;
                    border-radius: 4px;
                    font-size: 12px;
                }
                QPushButton:hover {
                    background-color: #E0E0E0;
                }
            """)
        self.new_table_btn.clicked.connect(self.new_data_table)
        self.add_row_btn.clicked.connect(self.add_data_row)
        self.copy_table_btn.clicked.connect(self.copy_table)
        # 报告总结功能菜单：全屏预览 / 复制到剪贴板 / 另存为
        self.report_menu = QMenu(self)
        self.report_menu.addAction("⛶ 全屏预览", self.report_preview)
        self.report_menu.addAction("⧉ 复制到剪贴板", self.report_copy)
        self.report_menu.addAction("💾 另存为", self.report_save_as)
        self.report_btn.setMenu(self.report_menu)
        edit_bar.addWidget(self.new_table_btn)
        edit_bar.addWidget(self.add_row_btn)
        edit_bar.addWidget(self.copy_table_btn)
        edit_bar.addWidget(self.report_btn)
        edit_bar.addWidget(self.highlight_btn)
        edit_bar.addWidget(self.paint_btn)
        edit_bar.addStretch()
        data_layout.addLayout(edit_bar)

        # 排序栏：字段下拉（点数/成功次数/失败次数/原始顺序）+ 升序/降序
        sort_bar = QHBoxLayout()
        sort_bar.addWidget(QLabel("排序:"))
        self.sort_field_combo = QComboBox()
        self.sort_field_combo.addItem("原始顺序", "original")
        self.sort_field_combo.addItem("点位", "point")
        self.sort_field_combo.addItem("成功次数", "success")
        self.sort_field_combo.addItem("失败次数", "fail")
        self.sort_field_combo.addItem("点位成功率", "rate")
        self.sort_field_combo.setToolTip(
            "选择排序字段：点位（自然排序 A2<A10）、成功次数、失败次数、点位成功率；"
            "选择原始顺序则恢复导入时的排列")
        self.sort_field_combo.currentIndexChanged.connect(self.on_sort_field_changed)
        sort_bar.addWidget(self.sort_field_combo)

        self.sort_asc_btn = QPushButton("升序")
        self.sort_desc_btn = QPushButton("降序")
        for btn in [self.sort_asc_btn, self.sort_desc_btn]:
            btn.setCheckable(True)
            btn.setToolTip("排序方向；再点一次取消，恢复原始顺序")
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #F0F0F0;
                    border: 1px solid #CCCCCC;
                    padding: 5px 14px;
                    border-radius: 4px;
                    font-size: 12px;
                }
                QPushButton:checked {
                    background-color: #4169E1;
                    color: white;
                    border-color: #4169E1;
                }
            """)
        self.sort_asc_btn.clicked.connect(
            lambda checked: self.on_sort_clicked("asc", checked))
        self.sort_desc_btn.clicked.connect(
            lambda checked: self.on_sort_clicked("desc", checked))
        sort_bar.addWidget(self.sort_asc_btn)
        sort_bar.addWidget(self.sort_desc_btn)
        sort_bar.addStretch()

        self.rate_fullscreen_btn = QPushButton("⛶ 全屏显示")
        self.rate_fullscreen_btn.setToolTip("全屏查看各点位成功率明细，可导出数据")
        self.rate_fullscreen_btn.setStyleSheet("""
            QPushButton {
                background-color: #4169E1;
                color: white;
                border: none;
                padding: 6px 14px;
                border-radius: 4px;
                font-size: 12px;
            }
            QPushButton:hover { background-color: #3155B5; }
            QPushButton:disabled { background-color: #CCCCCC; }
        """)
        self.rate_fullscreen_btn.setEnabled(False)
        self.rate_fullscreen_btn.clicked.connect(self.open_rate_fullscreen)
        sort_bar.addWidget(self.rate_fullscreen_btn)

        self.export_btn = QPushButton("导出数据")
        self.export_btn.setToolTip("导出当前点位数据（含备注列）为 CSV")
        self.export_btn.setStyleSheet("""
            QPushButton {
                background-color: #228B22;
                color: white;
                border: none;
                padding: 6px 14px;
                border-radius: 4px;
                font-size: 12px;
            }
            QPushButton:hover { background-color: #1A6B1A; }
            QPushButton:disabled { background-color: #CCCCCC; }
        """)
        self.export_btn.setEnabled(False)
        self.report_btn.setEnabled(False)
        self.export_btn.clicked.connect(self.export_data)
        sort_bar.addWidget(self.export_btn)
        data_layout.addLayout(sort_bar)

        # 表格单元格编辑 → 实时同步数据与图表；批量粘贴 → 一次性同步
        self.data_table.itemChanged.connect(self.on_table_item_changed)
        self.data_table.paste_started.connect(self._on_paste_started)
        self.data_table.paste_committed.connect(self._on_paste_committed)
        self.data_table.cellClicked.connect(self.on_cell_clicked_format_painter)

        data_layout.addWidget(self.data_table)
        splitter.addWidget(data_group)

        # 右侧：图表预览
        chart_group = QGroupBox("图表预览")
        chart_layout = QVBoxLayout(chart_group)

        self.chart_tabs = QWidget()
        tab_layout = QHBoxLayout(self.chart_tabs)
        tab_layout.setContentsMargins(0, 0, 0, 0)

        self.grid_btn = QPushButton("网格坐标图")
        self.heatmap_btn = QPushButton("热力图")
        for btn in [self.grid_btn, self.heatmap_btn]:
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #F0F0F0;
                    border: 1px solid #CCCCCC;
                    padding: 6px 12px;
                    border-radius: 4px;
                    font-size: 12px;
                }
                QPushButton:checked {
                    background-color: #4169E1;
                    color: white;
                    border-color: #4169E1;
                }
            """)
            btn.setCheckable(True)
        self.grid_btn.setChecked(True)
        self.grid_btn.clicked.connect(lambda: self.show_chart("grid"))
        self.heatmap_btn.clicked.connect(lambda: self.show_chart("heatmap"))
        tab_layout.addWidget(self.grid_btn)
        tab_layout.addWidget(self.heatmap_btn)

        # 图表标题编辑（应用于网格坐标图的全局显示）
        tab_layout.addSpacing(8)
        tab_layout.addWidget(QLabel("标题:"))
        self.title_edit = QLineEdit("网格测试点位可视化")
        self.title_edit.setMaximumWidth(240)
        self.title_edit.setToolTip("编辑网格坐标图标题；清空或改回默认值则使用默认标题（含中心点）")
        self.title_edit.textChanged.connect(self.on_title_changed)
        tab_layout.addWidget(self.title_edit)

        tab_layout.addStretch()

        self.copy_btn = QPushButton("保存图片")
        self.copy_btn.setToolTip("保存当前图表：复制到剪贴板或另存为 PNG（默认文件名同步标题）")
        self.copy_btn.setStyleSheet("""
            QPushButton {
                background-color: #FF8C00;
                color: white;
                border: none;
                padding: 6px 14px;
                border-radius: 4px;
                font-size: 12px;
            }
            QPushButton:hover { background-color: #E07B00; }
            QPushButton:disabled { background-color: #CCCCCC; }
        """)
        self.save_menu = QMenu(self)
        self.copy_action = self.save_menu.addAction("复制到剪贴板")
        self.copy_action.triggered.connect(self.copy_image)
        self.save_action = self.save_menu.addAction("另存为")
        self.save_action.triggered.connect(self.save_image_as)
        self.copy_btn.setMenu(self.save_menu)
        self.copy_btn.setEnabled(False)
        tab_layout.addWidget(self.copy_btn)

        self.fullscreen_btn = QPushButton("预览对比")
        self.fullscreen_btn.setToolTip("全屏预览当前图表，或选择多张网格坐标图图片拼接对比")
        self.fullscreen_btn.setStyleSheet("""
            QPushButton {
                background-color: #4169E1;
                color: white;
                border: none;
                padding: 6px 14px;
                border-radius: 4px;
                font-size: 12px;
            }
            QPushButton:hover { background-color: #3155B5; }
            QPushButton:disabled { background-color: #CCCCCC; }
        """)
        self.compare_menu = QMenu(self)
        self.preview_action = self.compare_menu.addAction("⛶ 全屏预览")
        self.preview_action.triggered.connect(self.open_fullscreen_chart)
        self.compare_action = self.compare_menu.addAction("🖼 图片对比")
        self.compare_action.triggered.connect(self.compare_images)
        self.fullscreen_btn.setMenu(self.compare_menu)
        self.fullscreen_btn.setEnabled(False)
        tab_layout.addWidget(self.fullscreen_btn)
        chart_layout.addWidget(self.chart_tabs)

        self.chart_frame = QFrame()
        self.chart_frame.setStyleSheet("border: 1px solid #E0E0E0; background-color: white;")
        self.chart_layout_inner = QVBoxLayout(self.chart_frame)
        self.chart_layout_inner.setAlignment(Qt.AlignCenter)
        self.placeholder_label = QLabel("请先导入数据文件")
        self.placeholder_label.setStyleSheet("color: #999999; font-size: 14px;")
        self.chart_layout_inner.addWidget(self.placeholder_label)
        chart_layout.addWidget(self.chart_frame)

        splitter.addWidget(chart_group)
        splitter.setSizes([400, 800])

        # ── 状态栏 ──
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("就绪 - 请拖拽或选择表格文件")

        # Ctrl+Z 撤销上一次表格编辑/粘贴（编辑器中输入文本的撤销不受影响）
        QShortcut(QKeySequence("Ctrl+Z"), self, self.undo_last_edit)

    def browse_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择表格文件", "",
            "Excel 文件 (*.xlsx *.xls);;CSV 文件 (*.csv);;所有文件 (*)"
        )
        if file_path:
            self.load_file(file_path)

    def load_file(self, file_path: str):
        """加载表格文件（含备注列）"""
        data, notes, error = read_table_data(file_path)
        if error:
            QMessageBox.warning(self, "错误", f"读取文件失败:\n{error}")
            return

        self.current_file = file_path
        self._apply_data(data, f"当前文件: {os.path.basename(file_path)}", notes)

    def import_pasted_data(self):
        """解析粘贴的表格文本并导入：
        优先识别纵向堆叠格式（点位/成功次数/失败次数三列竖排），
        其次支持 Excel 复制的 Tab 或逗号分隔格式"""
        text = self.paste_zone.editor.toPlainText()
        if not text.strip():
            QMessageBox.warning(self, "提示", "请先在右侧粘贴表格内容")
            return

        # 1) 纵向堆叠格式自动识别（含备注）
        result = parse_vertical_stacked(text)
        if result is not None:
            data, notes = result
            self.current_file = None
            self._apply_data(data, "数据来源: 剪贴板粘贴", notes)
            return

        # 2) 常规表格格式：Tab / 逗号 / 空格（含框选复制粘贴的单行循环格式）
        try:
            rows = _parse_paste_rows(text)
            if rows is None:
                QMessageBox.warning(self, "错误", "解析粘贴内容失败: 无法识别表格格式")
                return
            header_n = len(rows[0])
            body = rows[1:]
            if body and any(len(r) != header_n for r in body):
                # 列数不匹配：尝试单字段纵向循环重建（竖排复制每行一个单元格）
                if all(len(r) == 1 for r in body) and len(body) >= 3:
                    rebuilt = _split_into_point_rows([r[0] for r in body])
                    if rebuilt:
                        body = rebuilt
                # 混合宽度对齐：短行补空备注，长行多余字段拼入末列
                body = _align_row_widths([rows[0]] + body)[1:]
                if any(len(r) != header_n for r in body):
                    QMessageBox.warning(
                        self, "错误",
                        "解析粘贴内容失败: 数据列数（%d 列）与表头列数（%d 列）不匹配"
                        % (len(body[0]) if body else 0, header_n))
                    return
            df = pd.DataFrame(body, columns=rows[0])
            data, notes, error = dataframe_to_data(df)
        except Exception as e:
            QMessageBox.warning(self, "错误", f"解析粘贴内容失败:\n{e}")
            return
        if error:
            QMessageBox.warning(self, "错误", f"解析粘贴内容失败:\n{error}")
            return

        self.current_file = None
        self._apply_data(data, "数据来源: 剪贴板粘贴", notes)

    def _apply_data(self, data: dict, source_label: str, notes: dict = None):
        """数据加载成功后的统一处理：标签、数据表、按钮、图表、状态栏"""
        self.current_data = data
        self._notes = dict(notes or {})        # 导入数据备注（第 4 列）
        self._cell_style = {}                  # 新数据集清空单元格颜色
        self._original_order = list(data.keys())   # 保存原始排列顺序
        self._sort_field = "original"              # 默认按原始数据排列
        self._sort_state = "original"
        self.sort_field_combo.setCurrentIndex(0)
        self.sort_asc_btn.setChecked(False)
        self.sort_desc_btn.setChecked(False)

        # 更新文件标签
        self.file_label.setText(f"{source_label}  |  共 {len(data)} 个点位")
        self.file_label.setStyleSheet("color: #333333; font-size: 12px; font-weight: bold;")

        # 更新数据表（点位/成功次数/失败次数/备注 四列）
        self.data_table.setRowCount(len(data))
        self.data_table.setColumnCount(4)
        self.data_table.setHorizontalHeaderLabels(["点位", "成功次数", "失败次数", "备注"])
        self._refresh_table_rows()
        self.data_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

        # 启用生成按钮与全屏按钮
        self.fullscreen_btn.setEnabled(True)
        self.copy_btn.setEnabled(True)
        self.rate_fullscreen_btn.setEnabled(True)
        self.export_btn.setEnabled(True)
        self.report_btn.setEnabled(True)

        # 显示图表
        self.show_chart("grid")

        # 更新状态栏
        self._update_status_bar()
        self._undo_stack = []
        self._push_undo_state()   # 导入数据基线

    def on_sort_field_changed(self):
        """排序字段下拉变化：更新排序字段并刷新表格"""
        self._sort_field = self.sort_field_combo.currentData()
        if self._sort_field == "original":
            # 切回原始顺序时同时取消方向选中
            self._sort_state = "original"
            self.sort_asc_btn.setChecked(False)
            self.sort_desc_btn.setChecked(False)
        self._refresh_table_rows()

    def on_sort_clicked(self, direction: str, checked: bool):
        """升序/降序排序（可点选按钮互斥）；取消选中恢复原始顺序"""
        other = self.sort_desc_btn if direction == "asc" else self.sort_asc_btn
        if checked:
            other.setChecked(False)
            self._sort_state = direction
        else:
            self._sort_state = "original"
        self._refresh_table_rows()

    def toggle_format_painter(self, checked: bool):
        """格式刷开关：激活时读取当前选中单元格样式，点击目标单元格即复制"""
        if checked:
            src = self.data_table.currentItem()
            if src is None:
                self.paint_btn.setChecked(False)
                QMessageBox.warning(self, "格式刷", "请先选中要复制格式的源单元格")
                return
            point_item = self.data_table.item(src.row(), 0)
            point_id = point_item.text().strip() if point_item else ""
            if not point_id:
                self.paint_btn.setChecked(False)
                QMessageBox.warning(self, "格式刷", "请先设置源行的点位名称")
                return
            src_style = self._cell_style.get((point_id, src.column()), {})
            self._format_source = {
                "fg": src_style.get("fg"),
                "bg": src_style.get("bg"),
                "src": (point_id, src.column()),
            }
            self.status_bar.showMessage(
                f"格式刷已激活：复制 {point_id} 该单元格样式到点击的单元格"
                f"（再次点击「🖌 格式刷」取消）")
        else:
            self._format_source = None
            self._update_status_bar()

    def on_cell_clicked_format_painter(self, row: int, col: int):
        """格式刷激活时，点击单元格应用源样式（可连续刷）"""
        if not getattr(self, "_format_source", None):
            return
        point_item = self.data_table.item(row, 0)
        point_id = point_item.text().strip() if point_item else ""
        if not point_id:
            return
        src = self._format_source
        key = (point_id, col)
        style = dict(self._cell_style.get(key) or {})
        # 完全覆盖语义：源有 fg/bg 则复制，源无则清除目标对应属性
        if src.get("fg"):
            style["fg"] = src["fg"]
        else:
            style.pop("fg", None)
        if src.get("bg"):
            style["bg"] = src["bg"]
        else:
            style.pop("bg", None)
        if style:
            self._cell_style[key] = style
        else:
            self._cell_style.pop(key, None)   # 源无样式 → 清除目标样式
        self._apply_cell_styles_to_row(row, point_id)

    def clear_all_highlights(self):
        """清除所有单元格的文字/背景颜色高亮"""
        if not self._cell_style:
            QMessageBox.information(self, "高亮显示设置", "当前没有高亮颜色需要清除")
            return
        self._cell_style = {}
        for r in range(self.data_table.rowCount()):
            for c in range(self.data_table.columnCount()):
                it = self.data_table.item(r, c)
                if it is not None:
                    it.setForeground(QBrush())
                    it.setBackground(QBrush())

    def apply_cell_text_color(self):
        """设置选中单元格文字颜色（按点位名绑定）"""
        self._apply_cell_color(is_text=True)

    def apply_cell_bg_color(self):
        """设置选中单元格背景高亮颜色（按点位名绑定）"""
        self._apply_cell_color(is_text=False)

    def _apply_cell_color(self, is_text: bool):
        item = self.data_table.currentItem()
        if item is None:
            QMessageBox.warning(self, "提示", "请先选中要设置颜色的单元格")
            return
        point_item = self.data_table.item(item.row(), 0)
        point_id = point_item.text().strip() if point_item else ""
        if not point_id:
            QMessageBox.warning(self, "提示", "请先设置该行的点位名称")
            return
        initial = QColor("#000000") if is_text else QColor("#FFFFFF")
        color = QColorDialog.getColor(initial, self,
                                      "选择文字颜色" if is_text else "选择背景颜色")
        if not color.isValid():
            return
        key = (point_id, item.column())
        style = dict(self._cell_style.get(key) or {})
        style["fg" if is_text else "bg"] = color.name()
        self._cell_style[key] = style
        self._apply_cell_styles_to_row(item.row(), point_id)

    def _apply_cell_styles_to_row(self, row: int, point_id: str):
        """把 (点位,列) 的颜色样式全量同步到表格指定行；无样式的单元格恢复默认"""
        for c in range(self.data_table.columnCount()):
            it = self.data_table.item(row, c)
            if it is None:
                continue
            style = self._cell_style.get((point_id, c))
            if style:
                if style.get("fg"):
                    it.setForeground(QBrush(QColor(style["fg"])))
                else:
                    it.setForeground(QBrush())
                if style.get("bg"):
                    it.setBackground(QBrush(QColor(style["bg"])))
                else:
                    it.setBackground(QBrush())
            else:
                it.setForeground(QBrush())
                it.setBackground(QBrush())

    def _refresh_table_rows(self):
        """按当前排序字段与方向填充数据表行（点数/成功次数/失败次数/备注）"""
        if not self.current_data:
            return
        order = list(self._original_order)
        field = self._sort_field
        state = self._sort_state

        if field != "original" and state != "original":
            if field == "point":
                key = natural_sort_key
            elif field == "rate":
                # 成功率为数值主键，次数为 0 的点位按最低处理，相同再按点位自然序
                key = lambda pid: (point_rate_value(*self.current_data[pid]),
                                   natural_sort_key(pid))
            else:
                col = 0 if field == "success" else 1
                # 数值为主键，数值相同再按点位自然序，保证排序稳定可预期
                key = lambda pid: (self.current_data[pid][col], natural_sort_key(pid))
            order = sorted(order, key=key, reverse=(state == "desc"))

        self._updating = True
        try:
            for row, point_id in enumerate(order):
                success, fail = self.current_data[point_id]
                self.data_table.setItem(row, 0, QTableWidgetItem(point_id))
                self.data_table.setItem(row, 1, QTableWidgetItem(str(success)))
                self.data_table.setItem(row, 2, QTableWidgetItem(str(fail)))
                self.data_table.setItem(row, 3, QTableWidgetItem(
                    str(self._notes.get(point_id, ""))))
                self._apply_cell_styles_to_row(row, point_id)
        finally:
            self._updating = False

    def _update_status_bar(self):
        """按当前数据刷新主界面左下角状态栏"""
        data = self.current_data or {}
        total_success = sum(s for s, f in data.values())
        total_fail = sum(f for s, f in data.values())
        total = total_success + total_fail
        rate = total_success / total * 100 if total > 0 else 0
        success_points = sum(1 for s, f in data.values() if s > 0)
        fail_points = sum(1 for s, f in data.values() if f > 0)
        self.status_bar.showMessage(
            f"共 {len(data)} 个点位 | 成功点位: {success_points} | 失败点位: {fail_points} | "
            f"成功: {total_success} | 失败: {total_fail} | 成功率: {rate:.1f}%"
        )

    def _clear_chart_placeholder(self):
        """清空图表预览区并显示占位提示"""
        for i in reversed(range(self.chart_layout_inner.count())):
            widget = self.chart_layout_inner.itemAt(i).widget()
            if widget:
                widget.setParent(None)
        self.chart_layout_inner.addWidget(self.placeholder_label)

    def new_data_table(self):
        """新建空数据表：清空当前数据，从空白表格开始录入"""
        self.current_data = {}
        self._notes = {}
        self._original_order = []
        self._cell_style = {}
        self._sort_field = "original"
        self._sort_state = "original"
        self.sort_field_combo.setCurrentIndex(0)
        self.sort_asc_btn.setChecked(False)
        self.sort_desc_btn.setChecked(False)
        self.current_file = None
        self._undo_stack = []

        self._updating = True
        try:
            self.data_table.setRowCount(20)   # 默认 20 行空行
            self.data_table.setColumnCount(4)
            self.data_table.setHorizontalHeaderLabels(["点位", "成功次数", "失败次数", "备注"])
            for r in range(20):
                for c in range(4):
                    self.data_table.setItem(r, c, QTableWidgetItem(""))
            self.data_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        finally:
            self._updating = False

        self.file_label.setText("新建数据表（空白，可直接在表格中录入）")
        self.file_label.setStyleSheet("color: #333333; font-size: 12px; font-weight: bold;")
        self._clear_chart_placeholder()
        self.fullscreen_btn.setEnabled(False)
        self.copy_btn.setEnabled(False)
        self.rate_fullscreen_btn.setEnabled(False)
        self.export_btn.setEnabled(False)
        self.report_btn.setEnabled(False)
        self._update_status_bar()
        self._undo_stack = []
        self._push_undo_state()   # 空表基线

    def add_data_row(self):
        """批量添加点位行：弹出对话框一次输入行数，一次性追加多行"""
        count, ok = QInputDialog.getInt(
            self, "添加点位行", "请输入要添加的行数（1-100）:", 5, 1, 100)
        if not ok or count <= 0:
            return
        r = self.data_table.rowCount()
        self._updating = True
        try:
            self.data_table.setRowCount(r + count)
            for rr in range(r, r + count):
                for c in range(4):
                    self.data_table.setItem(rr, c, QTableWidgetItem(""))
        finally:
            self._updating = False
        self.data_table.scrollToBottom()

    def _on_paste_started(self):
        """批量粘贴开始：置防递归标志，避免每个单元格触发同步"""
        self._updating = True

    def _on_paste_committed(self):
        """批量粘贴完成：解除标志并同步一次数据与图表"""
        self._updating = False
        self._sync_from_table()

    def _push_undo_state(self):
        """把当前数据状态压入撤销栈（相邻相同状态去重，最多保留 50 步）"""
        if self.current_data is None:
            return
        state = (dict(self.current_data), dict(self._notes),
                 list(self._original_order))
        if self._undo_stack and self._undo_stack[-1] == state:
            return
        self._undo_stack.append(state)
        if len(self._undo_stack) > 50:
            self._undo_stack.pop(0)

    def undo_last_edit(self):
        """Ctrl+Z：撤销上一次表格编辑或粘贴，恢复到上一个数据状态"""
        if len(self._undo_stack) <= 1:
            self.status_bar.showMessage("没有可撤销的操作")
            return
        self._undo_stack.pop()   # 丢弃当前状态
        data, notes, order = self._undo_stack[-1]
        self.current_data = dict(data)
        self._notes = dict(notes)
        self._original_order = list(order)
        if self.current_data:
            self._refresh_table_rows()
            self.fullscreen_btn.setEnabled(True)
            self.copy_btn.setEnabled(True)
            self.rate_fullscreen_btn.setEnabled(True)
            self.export_btn.setEnabled(True)
            self.report_btn.setEnabled(True)
            self.show_chart(self.current_chart_type)
        else:
            # 恢复到无数据状态：重建空白表格，避免残留旧行
            self._updating = True
            try:
                self.data_table.setRowCount(20)
                self.data_table.setColumnCount(4)
                self.data_table.setHorizontalHeaderLabels(
                    ["点位", "成功次数", "失败次数", "备注"])
                for r in range(20):
                    for c in range(4):
                        self.data_table.setItem(r, c, QTableWidgetItem(""))
                self.data_table.horizontalHeader().setSectionResizeMode(
                    QHeaderView.Stretch)
            finally:
                self._updating = False
            self._clear_chart_placeholder()
            self.fullscreen_btn.setEnabled(False)
            self.copy_btn.setEnabled(False)
            self.rate_fullscreen_btn.setEnabled(False)
            self.export_btn.setEnabled(False)
        self.report_btn.setEnabled(False)
        self._update_status_bar()
        self.status_bar.showMessage("已撤销上一步编辑")

    def on_table_item_changed(self, item):
        """表格单元格编辑后的处理：校验次数、实时同步数据与图表"""
        if self._updating:
            return
        col = item.column()
        row = item.row()
        if col in (1, 2):
            text = item.text().strip()
            if text == "":
                text = "0"
            try:
                v = int(text)
            except ValueError:
                QMessageBox.warning(self, "提示", "成功/失败次数必须是非负整数")
                pid_item = self.data_table.item(row, 0)
                pid = pid_item.text().strip() if pid_item else ""
                old = self.current_data.get(pid, (0, 0)) if pid else (0, 0)
                self._updating = True
                try:
                    item.setText(str(old[0] if col == 1 else old[1]))
                finally:
                    self._updating = False
                return
            if v < 0:
                QMessageBox.warning(self, "提示", "成功/失败次数不能为负数")
                self._updating = True
                try:
                    item.setText("0")
                finally:
                    self._updating = False
                self._sync_from_table()
                return
        self._sync_from_table()

    def _sync_from_table(self):
        """以数据表为数据源回读：更新 current_data 与备注，保留原始顺序，实时刷新图表"""
        rows = self.data_table.rowCount()
        new_data = {}
        new_notes = {}
        seen = []
        dup_lines = []
        for r in range(rows):
            pid_item = self.data_table.item(r, 0)
            pid = pid_item.text().strip() if pid_item else ""
            if not pid:
                continue
            if pid in new_data:
                dup_lines.append(pid)
            s_item = self.data_table.item(r, 1)
            f_item = self.data_table.item(r, 2)
            n_item = self.data_table.item(r, 3)
            s_text = (s_item.text().strip() if s_item else "") or "0"
            f_text = (f_item.text().strip() if f_item else "") or "0"
            if not s_text.lstrip("-").isdigit() or not f_text.lstrip("-").isdigit():
                continue
            s = int(s_text)
            f = int(f_text)
            if s < 0 or f < 0:
                continue
            new_data[pid] = (s, f)
            new_notes[pid] = n_item.text() if n_item else ""
            seen.append(pid)

        if dup_lines:
            QMessageBox.warning(
                self, "提示",
                "与上述单元行点位名重复，重复输入会导致网格图生成结果覆盖")

        self.current_data = new_data
        self._notes = new_notes
        # 原始顺序：保留原有点位顺序，新增点位追加到末尾，被删除/改名的点位移除
        self._original_order = [p for p in self._original_order if p in new_data] + \
                               [p for p in seen if p not in self._original_order]

        if self.current_data:
            self.file_label.setText(f"数据表编辑  |  共 {len(self.current_data)} 个点位")
            self.fullscreen_btn.setEnabled(True)
            self.copy_btn.setEnabled(True)
            self.rate_fullscreen_btn.setEnabled(True)
            self.export_btn.setEnabled(True)
            self.report_btn.setEnabled(True)
            # 排序模式下重填表格保持排序；非排序模式保持用户当前编辑内容
            if self._sort_field != "original" and self._sort_state != "original":
                self._refresh_table_rows()
            self.show_chart(self.current_chart_type)
        else:
            self.file_label.setText("数据表编辑（当前无有效点位，请录入点数）")
            self._clear_chart_placeholder()
            self.fullscreen_btn.setEnabled(False)
            self.copy_btn.setEnabled(False)
            self.rate_fullscreen_btn.setEnabled(False)
            self.export_btn.setEnabled(False)
            self.report_btn.setEnabled(False)
        self._push_undo_state()   # 编辑/粘贴完成后快照（供 Ctrl+Z 撤销）
        self._update_status_bar()

    def open_rate_fullscreen(self):
        """全屏查看各点位成功率明细（沿用当前排序顺序）"""
        if not self.current_data:
            return
        order = self._sorted_order()
        dlg = FullscreenRateDialog(order, self.current_data, self._notes, self)
        dlg.showFullScreen()
        dlg.exec()

    def copy_table(self):
        """一键复制整个数据表到剪贴板：包含列名行与全部单元格内容。

        同时写入 HTML 表格（带边框/表头底色，粘贴到 Excel 保留格式）
        与 Tab 分隔纯文本（含列名行），两种格式均可直接粘贴。
        """
        rows = self.data_table.rowCount()
        cols = self.data_table.columnCount()
        headers = []
        for c in range(cols):
            it = self.data_table.horizontalHeaderItem(c)
            headers.append(it.text() if it else "")

        def esc(v):
            from xml.sax.saxutils import escape
            return escape(str(v)).replace("\n", "<br>")

        # HTML 表格：带列名、边框与表头底色，粘贴到 Excel 保留表格样式
        html_parts = ["<table border='1' cellspacing='0' cellpadding='4' "
                      "style='border-collapse:collapse;font-size:12px;'>"]
        html_parts.append("<tr style='background-color:#F0F0F0;font-weight:bold;'>")
        for h in headers:
            html_parts.append(
                f"<th style='border:1px solid #E0E0E0;padding:4px;'>{esc(h)}</th>")
        html_parts.append("</tr>")
        for r in range(rows):
            html_parts.append("<tr>")
            for c in range(cols):
                it = self.data_table.item(r, c)
                val = it.text() if it else ""
                html_parts.append(
                    f"<td style='border:1px solid #E0E0E0;padding:4px;'>{esc(val)}</td>")
            html_parts.append("</tr>")
        html_parts.append("</table>")

        # Tab 分隔纯文本（含列名行）
        text_lines = ["\t".join(headers)]
        for r in range(rows):
            cells = []
            for c in range(cols):
                it = self.data_table.item(r, c)
                cells.append(it.text() if it else "")
            text_lines.append("\t".join(cells))

        html_fragment = "".join(html_parts)
        # Windows 原生写剪贴板：标准 CF_HTML（Excel/WPS）+ text/html（网页/飞书云文档）
        # + Unicode 纯文本（记事本/CSV）。绕开 Qt text/html→CF_HTML 转换（头不规范）。
        _copy_to_clipboard(html_fragment, "\n".join(text_lines))
        self.status_bar.showMessage(
            f"已复制数据表（{rows} 行 × {cols} 列，含列名）到剪贴板，可直接粘贴到 Excel")

    def _build_report_or_warn(self):
        """生成报告总结拼接图；空数据时提示并返回 None"""
        if not self.current_data:
            QMessageBox.warning(self, "提示", "请先导入或录入点位数据")
            return None
        try:
            return self._build_report_image()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"报告总结失败:\n{str(e)}")
            return None

    def report_preview(self):
        """报告总结 - 全屏预览"""
        img = self._build_report_or_warn()
        if img is None:
            return
        dlg = ReportPreviewDialog(img, self)
        dlg.showFullScreen()
        dlg.exec()

    def report_copy(self):
        """报告总结 - 一键复制到剪贴板"""
        img = self._build_report_or_warn()
        if img is None:
            return
        try:
            self._copy_image_to_clipboard(img)
            QMessageBox.information(self, "报告总结", "报告总结图片已复制到剪贴板")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"复制报告总结图片失败:\n{str(e)}")

    def report_save_as(self):
        """报告总结 - 另存为 PNG"""
        img = self._build_report_or_warn()
        if img is None:
            return
        from datetime import datetime
        default_name = f"点位报告总结_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        path, _ = QFileDialog.getSaveFileName(
            self, "报告总结 - 另存为", default_name, "PNG 图片 (*.png)")
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        try:
            img.save(path, dpi=(200, 200))
            QMessageBox.information(self, "报告总结", f"报告总结已保存:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"保存报告总结失败:\n{str(e)}")

    def _copy_image_to_clipboard(self, pil_img):
        """将 PIL 图像写入系统剪贴板（跨平台：Qt 写标准 CF_DIB / NSPasteboard）"""
        from PySide6.QtGui import QImage
        img = pil_img.convert("RGB")
        w, h = img.size
        qimg = QImage(img.tobytes(), w, h, w * 3, QImage.Format_RGB888)
        QApplication.clipboard().setImage(qimg)

    def _build_report_image(self):
        """构建 表格(左) + 网格坐标图(右) 左右拼接对比图，返回 PIL Image。

        两个图都按原分辨率渲染（表格 200dpi、网格 200dpi），高度不一致时
        矮图垂直居中填充空白而非拉伸缩放，保证文字与点位标注清晰可对比。
        表格左上角同步显示网格坐标图标题。
        """
        from PIL import Image
        from matplotlib.backends.backend_agg import FigureCanvasAgg

        # 1) 当前数据表渲染为图片
        headers = []
        for c in range(self.data_table.columnCount()):
            it = self.data_table.horizontalHeaderItem(c)
            headers.append(it.text() if it else "")
        rows_text = []
        for r in range(self.data_table.rowCount()):
            cells = []
            for c in range(self.data_table.columnCount()):
                it = self.data_table.item(r, c)
                cells.append(it.text() if it else "")
            if any(cells):
                rows_text.append(cells)
        if not rows_text:
            rows_text = [[""] * len(headers)]
        n_rows = len(rows_text)

        # 备注列保留原文（PIL 渲染时按列宽自动换行，行高按文本行数自适应）
        wrapped_rows = []
        for row_cells in rows_text:
            note = row_cells[3] if len(row_cells) > 3 else ""
            new_row = list(row_cells[:3]) + [note]
            wrapped_rows.append((new_row, note))

        # 表格图：PIL 手绘（避免 matplotlib ax.table 多行行高缩放丢文字），
        # 200dpi 保证文字清晰，左上角同步网格坐标图标题
        t_img = _render_table_image(headers, wrapped_rows,
                                    self._cell_style, self.chart_title)
        tbuf = np.asarray(t_img.convert("RGB"))

        # 2) 网格坐标图（200dpi，复用现有绘制逻辑与标题/全部点位标注）
        gfig = draw_grid_visualization(
            self.current_data, self.grid_cfg, self.show_other_names,
            self.chart_title, dpi=200)
        gfig.set_canvas(FigureCanvasAgg(gfig))
        gfig.canvas.draw()
        gbuf = np.asarray(gfig.canvas.buffer_rgba())[:, :, :3]

        # 3) 左右拼接：原分辨率垂直居中，不拉伸不变形
        img_t = Image.fromarray(tbuf)
        img_g = Image.fromarray(gbuf)
        h = max(img_t.height, img_g.height)
        canvas_img = Image.new("RGB", (img_t.width + img_g.width, h), "white")
        canvas_img.paste(img_t, (0, (h - img_t.height) // 2))
        canvas_img.paste(img_g, (img_t.width, (h - img_g.height) // 2))
        return canvas_img

    def export_data(self):
        """导出当前点位数据（按当前排序顺序，含备注列）为 CSV"""
        if not self.current_data:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出数据", "点位测试数据.csv", "CSV 文件 (*.csv)")
        if not path:
            return
        try:
            write_data_csv(path, self._sorted_order(), self.current_data, self._notes)
            self.status_bar.showMessage(f"数据已导出: {path}")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"导出失败:\n{str(e)}")

    def _sorted_order(self):
        """按当前排序字段与方向返回点位顺序（供全屏明细复用）"""
        if not self.current_data:
            return []
        order = list(self._original_order)
        field = self._sort_field
        state = self._sort_state
        if field != "original" and state != "original":
            if field == "point":
                key = natural_sort_key
            elif field == "rate":
                key = lambda pid: (point_rate_value(*self.current_data[pid]),
                                   natural_sort_key(pid))
            else:
                col = 0 if field == "success" else 1
                key = lambda pid: (self.current_data[pid][col], natural_sort_key(pid))
            order = sorted(order, key=key, reverse=(state == "desc"))
        return order

    def apply_grid_settings(self):
        """根据画布设置控件重建 GridConfig，并刷新中心点提示与图表"""
        self.grid_cfg = GridConfig(
            n_rows=self.rows_spin.value(),
            n_cols=self.cols_spin.value(),
            fig_width=self.width_spin.value(),
            fig_height=self.height_spin.value(),
        )
        self.center_label.setText(f"中心点: {self.grid_cfg.center_label}")
        if self.current_data:
            self.show_chart(self.current_chart_type)

    def on_title_changed(self, text: str):
        """标题编辑：非空且非默认值则用作自定义标题，否则回退默认标题"""
        stripped = text.strip()
        if stripped and stripped != "网格测试点位可视化":
            self.chart_title = stripped
        else:
            self.chart_title = None
        if self.current_data and self.current_chart_type == "grid":
            self.show_chart("grid")

    def copy_image(self):
        """一键复制当前图表到剪贴板"""
        if not self.current_data:
            return
        if self.current_chart_type == "heatmap":
            fig = draw_heatmap_table(self.current_data, self.grid_cfg)
        else:
            fig = draw_grid_visualization(
                self.current_data, self.grid_cfg, self.show_other_names,
                self.chart_title)
        try:
            buf = BytesIO()
            fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
            buf.seek(0)
            img = QImage.fromData(buf.getvalue())
            QApplication.clipboard().setImage(img)
            self.status_bar.showMessage("当前图表已复制到剪贴板")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"复制图片失败:\n{str(e)}")

    def save_image_as(self):
        """另存为当前图表 PNG，默认文件名同步为网格坐标图标题"""
        if not self.current_data:
            return
        base = (self.chart_title or "网格点位可视化")
        base = re.sub(r'[\\/:*?"<>|]', "_", base).strip() or "网格点位可视化"
        if self.current_chart_type == "heatmap":
            fig = draw_heatmap_table(self.current_data, self.grid_cfg)
            default_name = f"{base}_热力图.png"
        else:
            fig = draw_grid_visualization(
                self.current_data, self.grid_cfg, self.show_other_names,
                self.chart_title)
            default_name = f"{base}.png"
        path, _ = QFileDialog.getSaveFileName(
            self, "保存图片", default_name, "PNG 图片 (*.png)")
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        try:
            from PIL import Image
            from matplotlib.backends.backend_agg import FigureCanvasAgg
            fig.set_canvas(FigureCanvasAgg(fig))
            fig.canvas.draw()
            buf = np.asarray(fig.canvas.buffer_rgba())[:, :, :3]
            Image.fromarray(buf).save(path, dpi=(200, 200))
            self.status_bar.showMessage(f"图片已保存: {path}")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"保存图片失败:\n{str(e)}")

    def toggle_other_names(self, checked: bool):
        """一键显示/隐藏其余（未测试）点位的名称"""
        self.show_other_names = checked
        if self.current_data and self.current_chart_type == "grid":
            self.show_chart("grid")

    def open_fullscreen_chart(self):
        """全屏弹出当前图表（网格坐标图 / 热力图），沿用当前画布设置与名称开关"""
        if not self.current_data:
            return
        if self.current_chart_type == "heatmap":
            fig = draw_heatmap_table(self.current_data, self.grid_cfg)
            title = "热力图 - 全屏"
        else:
            fig = draw_grid_visualization(
                self.current_data, self.grid_cfg, self.show_other_names,
                self.chart_title)
            title = "网格坐标图 - 全屏"
        dlg = FullscreenChartDialog(fig, title, self)
        dlg.showFullScreen()
        dlg.exec()

    def compare_images(self):
        """图片对比：选择 2-4 张网格坐标图 PNG 拼接，支持全屏预览/复制/另存为"""
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择 2-4 张网格坐标图图片",
            "", "PNG 图片 (*.png);;图片文件 (*.png *.jpg *.jpeg)")
        if not paths:
            return
        if len(paths) < 2:
            QMessageBox.warning(self, "提示", "请至少选择 2 张图片进行对比")
            return
        if len(paths) > 4:
            QMessageBox.warning(self, "提示", "最多支持 4 张图片进行对比")
            return
        try:
            img = _compose_compare_image(paths)
        except Exception as e:
            QMessageBox.critical(self, "错误", f"图片对比失败:\n{str(e)}")
            return
        # 正式对话框展示对比结果（全屏预览 / 复制到剪贴板 / 另存为）
        dlg = CompareResultDialog(img, len(paths), self)
        dlg.exec()

    def show_chart(self, chart_type: str):
        """显示图表预览"""
        if not self.current_data:
            return
        self.current_chart_type = chart_type

        # 清除旧图表
        for i in reversed(range(self.chart_layout_inner.count())):
            widget = self.chart_layout_inner.itemAt(i).widget()
            if widget:
                widget.setParent(None)

        if chart_type == "grid":
            self.grid_btn.setChecked(True)
            self.heatmap_btn.setChecked(False)
            fig = draw_grid_visualization(
                self.current_data, self.grid_cfg, self.show_other_names,
                self.chart_title)
        else:
            self.grid_btn.setChecked(False)
            self.heatmap_btn.setChecked(True)
            fig = draw_heatmap_table(self.current_data, self.grid_cfg)

        canvas = FigureCanvas(fig)
        canvas.setStyleSheet("background-color: white;")
        self.chart_layout_inner.addWidget(canvas)

"""全局应用样式（原 main() 中的 setStyleSheet 内容原样提取）"""


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # 设置全局样式
    app.setStyleSheet("""
        QMainWindow {
            background-color: #FAFAFA;
        }
        QGroupBox {
            font-weight: bold;
            font-size: 13px;
            border: 1px solid #E0E0E0;
            border-radius: 6px;
            margin-top: 10px;
            padding-top: 10px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 5px;
        }
    """)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

