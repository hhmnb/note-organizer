import os
import re
from typing import List, Tuple, Optional

# 编码检测为可选功能，未安装 chardet 时自动降级
try:
    import chardet

    _HAS_CHARDET = True
except ImportError:
    _HAS_CHARDET = False


class NoteScanner:
    """笔记扫描器：通过文件名末尾的 '&' 标记判断已处理状态，支持安全读取及编码问题检测。

    注意：文件的“已处理”标记（添加 '&' 后缀）统一由外部后处理模块负责，
    本类不提供 mark_done 方法，以避免逻辑分散。
    """

    MARKER = "&"  # 已处理标记符

    def __init__(self, notes_folder: str):
        self.notes_folder = os.path.abspath(notes_folder)
        if not os.path.isdir(self.notes_folder):
            raise FileNotFoundError(f"笔记文件夹不存在: {self.notes_folder}")

        # 仅初始化时提示一次，避免重复刷屏
        if not _HAS_CHARDET:
            print("⚠️ 未安装 chardet，编码检测及安全读取功能将受限。如需完整支持，请执行: pip install chardet")

    # ---------- 标记检测 ----------
    def _is_processed(self, file_path: str) -> bool:
        """文件是否已处理：检查文件名（不含扩展名）是否以标记符结尾"""
        base = os.path.basename(file_path)
        name_without_ext = os.path.splitext(base)[0]
        return name_without_ext.endswith(self.MARKER)

    # ---------- 编码工具 ----------
    def _detect_encoding(self, file_path: str) -> Tuple[Optional[str], float]:
        """返回 (编码, 置信度)；若无法检测则返回 (None, 0.0)"""
        if not _HAS_CHARDET:
            return None, 0.0
        try:
            with open(file_path, 'rb') as f:
                raw = f.read()
            result = chardet.detect(raw)
            return result.get('encoding'), result.get('confidence', 0.0)
        except Exception:
            return None, 0.0

    def safe_read(self, file_path: str, fallback_encodings=None) -> str:
        """
        安全读取文件内容（自动转码为 UTF-8 字符串）。
        优先使用 chardet 检测编码，失败时尝试 fallback_encodings，
        最终回退到 utf-8（errors='replace' 保证不崩）。
        """
        if fallback_encodings is None:
            fallback_encodings = ['utf-8', 'gbk', 'gb2312', 'latin-1']

        # 1. 尝试 chardet 检测
        if _HAS_CHARDET:
            enc, conf = self._detect_encoding(file_path)
            if enc and conf > 0.7:
                try:
                    with open(file_path, 'r', encoding=enc) as f:
                        return f.read()
                except (UnicodeDecodeError, LookupError):
                    pass  # 检测不准，继续尝试

        # 2. 使用预设编码列表尝试
        for enc in fallback_encodings:
            try:
                with open(file_path, 'r', encoding=enc) as f:
                    content = f.read()
                if enc != 'utf-8':
                    print(f"    ℹ️  文件 {os.path.basename(file_path)} 使用编码 {enc}，已转为 UTF-8 处理")
                return content
            except (UnicodeDecodeError, LookupError):
                continue

        # 3. 最终回退：二进制读取 + 强制 decode
        with open(file_path, 'rb') as f:
            raw = f.read()
        print(f"    ⚠️  文件 {os.path.basename(file_path)} 编码未知，使用 utf-8 (replace) 强制读取")
        return raw.decode('utf-8', errors='replace')

    def check_encoding_issues(self, min_confidence: float = 0.8) -> List[Tuple[str, str, float]]:
        """
        扫描所有符合条件的 .md 笔记，返回可能存在编码问题的文件列表。
        返回格式: [(文件路径, 检测到的编码, 置信度), ...]
        """
        if not _HAS_CHARDET:
            return []  # 初始化时已提示，不再重复
        issues = []
        for file_path in self._scan_all_notes():
            enc, conf = self._detect_encoding(file_path)
            if enc and enc.lower() not in ('utf-8', 'ascii') and conf >= min_confidence:
                issues.append((file_path, enc, conf))
        return issues

    # ---------- 文件扫描 ----------
    def _scan_all_notes(self) -> List[str]:
        """递归扫描笔记文件夹，返回所有 .md 文件的绝对路径（不再过滤日期开头的文件）"""
        results = []
        for root, dirs, files in os.walk(self.notes_folder):
            # 忽略隐藏文件夹
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            for file in files:
                if file.lower().endswith('.md'):
                    results.append(os.path.join(root, file))
        return results

    # ---------- 获取待处理任务 ----------
    def get_pending_tasks(self, skip_encoding_issues: bool = False,
                          target_encoding: str = 'utf-8') -> List[str]:
        """
        返回所有尚未处理的 .md 笔记文件路径列表。
        通过文件名末尾是否包含 '&' 判断是否已处理。
        若 skip_encoding_issues=True，则跳过可能存在编码问题的文件；
        对低置信度的非目标编码文件会发出警告但仍保留。
        """
        all_notes = self._scan_all_notes()
        pending = []
        for file_path in all_notes:
            if self._is_processed(file_path):
                continue

            if skip_encoding_issues and _HAS_CHARDET:
                enc, conf = self._detect_encoding(file_path)
                if enc and enc.lower() not in (target_encoding, 'ascii'):
                    if conf >= 0.8:
                        # 高置信度异种编码，直接跳过
                        continue
                    else:
                        # 低置信度，保留但警告
                        print(f"    ⚠️  低置信度编码 ({enc}, {conf:.2f})，仍保留待处理: {os.path.basename(file_path)}")

            pending.append(file_path)

        pending.sort()
        return pending