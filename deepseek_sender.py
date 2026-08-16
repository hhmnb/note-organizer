import time
import pyautogui
import pyperclip
from typing import Optional, Dict, Any

class DeepSeekWebSender:
    """DeepSeek 网页自动化发送器（兼容旧版 config.json 含 coordinates 字典）"""

    def __init__(self, config: Dict[str, Any]):
        """
        从 config 字典直接提取参数，支持嵌套的 "coordinates" 结构。
        保留双输入框切换逻辑，适配 empty_input_box / normal_input_box。
        """
        # ---- 提取坐标（兼容嵌套结构） ----
        coords = config.get("coordinates", {})
        self.coord_empty_input_box = coords.get("empty_input_box")
        self.coord_normal_input_box = coords.get("normal_input_box")
        self.coord_copy_btn = coords.get("copy_btn")
        self.coord_regen_btn = coords.get("regen_btn")          # 保留但未在逻辑中使用
        self.coord_new_chat = coords.get("new_chat_btn")
        self.coord_expert_mode = coords.get("expert_mode")

        # 初始使用空输入框（新对话场景）
        self.input_box = self.coord_empty_input_box
        self._first_send_done = False

        # ---- 运行参数（提供默认值防止缺失） ----
        self.max_retries_per_note = config.get("max_retries_per_note", 3)
        self.poll_interval = config.get("poll_interval", 5)
        self.slow_char_per_sec = config.get("slow_char_per_sec", 20.0)
        self.min_silent = config.get("min_silent", 5)
        # 以下两个参数旧版配置可能没有，使用默认值
        self.min_poll = config.get("min_poll", 5)
        self.max_poll = config.get("max_poll", 60)

        print("⚠️ 请确保 DeepSeek 网页已打开并保持在前台")
        time.sleep(3)
        pyautogui.FAILSAFE = True

    # ========== 基础操作 ==========
    def _click(self, pos):
        pyautogui.click(pos)
        time.sleep(0.5)

    def _get_input_text(self) -> str:
        """全选输入框内容并复制到剪贴板，返回文本（带异常保护）"""
        try:
            pyperclip.copy("")
        except Exception:
            pass
        time.sleep(0.1)
        pyautogui.hotkey('ctrl', 'a')
        time.sleep(0.2)
        pyautogui.hotkey('ctrl', 'c')
        time.sleep(0.3)
        try:
            return pyperclip.paste()
        except Exception as e:
            print(f"    ⚠️ 读取剪贴板失败: {e}")
            return ""

    def _paste_with_fallback(self, text: str):
        """
        尝试用 pyperclip 粘贴，若失败且文本为纯 ASCII，则降级为逐字键入。
        否则抛出异常。
        """
        clipboard_ok = False
        try:
            pyperclip.copy(text)
            clipboard_ok = True
        except Exception as e:
            print(f"    ⚠️ 剪贴板复制异常: {e}")

        if clipboard_ok:
            pyautogui.hotkey('ctrl', 'v')
            return

        if text.isascii():
            print("    降级为 pyautogui.write 逐字键入...")
            pyautogui.write(text, interval=0.01)
        else:
            raise RuntimeError("剪贴板不可用，且文本包含非 ASCII 字符，无法自动键入")

    # --- 发送消息主流程 ---
    def _send_message(self, text: str):
        """清空输入框、粘贴文本、按 Enter 发送"""
        self._click(self.input_box)
        time.sleep(0.3)
        pyautogui.hotkey('ctrl', 'a')
        time.sleep(0.2)
        pyautogui.press('delete')
        time.sleep(0.2)
        self._click(self.input_box)
        time.sleep(0.2)

        self._paste_with_fallback(text)

        pyautogui.press('enter')
        time.sleep(1.5)

    def _try_copy_reply(self) -> str:
        """点击预设的复制按钮坐标，读取剪贴板，返回内容（空字符串表示未获取到有效回复）"""
        try:
            pyperclip.copy("")
        except Exception:
            pass
        time.sleep(0.2)
        self._click(self.coord_copy_btn)
        time.sleep(0.5)
        try:
            text = pyperclip.paste()
        except Exception as e:
            print(f"    ⚠️ 读取回复剪贴板失败: {e}")
            return ""
        if text and len(text.strip()) > 10:
            return text
        return ""

    # ========== 动态等待与轮询 ==========
    def _wait_and_copy(self, text_length: int) -> str:
        """
        1. 静默等待 = min_silent
        2. 总时限 = 字数 / slow_char_per_sec（保守速度）
        3. 轮询超时 = 总时限 - 静默等待，若为负则置0
        """
        silent_wait = self.min_silent

        print(f"    静默等待 {silent_wait}s（由 min_silent 决定）...", end="", flush=True)
        time.sleep(silent_wait)
        print(" 开始轮询")

        total_limit = int(text_length / self.slow_char_per_sec) if self.slow_char_per_sec > 0 else 0

        poll_timeout = total_limit - silent_wait
        if poll_timeout < 0:
            poll_timeout = 0

        print(f"    轮询最多 {poll_timeout}s（总时限 {total_limit}s - 已等 {silent_wait}s）", flush=True)

        start = time.time()
        while time.time() - start < poll_timeout:
            reply = self._try_copy_reply()
            if reply:
                elapsed = silent_wait + int(time.time() - start)
                print(f"    检测到回复（总耗时 {elapsed}s）")
                return reply
            print(f"    未检测到回复，{self.poll_interval}s 后重试...")
            time.sleep(self.poll_interval)

        print("    超过动态轮询超时，未获取回复")
        return ""

    # ========== 核心发送 ==========
    def send(self, content: str) -> str:
        """
        发送消息并获取回复，失败时在输入框重新发送（最多 max_retries_per_note 次）。
        首次发送成功后自动切换到「已有对话」的输入框坐标。
        返回空字符串表示最终失败。
        """
        text_length = len(content)
        for attempt in range(1, self.max_retries_per_note + 1):
            try:
                self._send_message(content)
                print(f"    已发送（第{attempt}次），等待回复...")

                reply = self._wait_and_copy(text_length)
                if reply:
                    # 首次成功回复后，切换到有对话的输入框坐标
                    if not self._first_send_done:
                        self.input_box = self.coord_normal_input_box
                        self._first_send_done = True
                    return reply

                print(f"    第{attempt}次尝试未获取回复", end="")
                if attempt < self.max_retries_per_note:
                    print("，2秒后在输入框重新发送...")
                    time.sleep(2)
                else:
                    print("，已达最大尝试次数")

            except Exception as e:
                print(f"    异常: {e}，等待5秒后重试...")
                time.sleep(5)

        return ""

    # ========== 新建对话 + 专家模式 ==========
    def new_chat_with_expert(self):
        """点击新对话按钮，再点击专家模式按钮，并重置输入框为空对话坐标"""
        if not self.coord_new_chat or not self.coord_expert_mode:
            print("    ⚠️ 未配置新对话/专家模式按钮，跳过新建对话")
            return
        self._click(self.coord_new_chat)
        time.sleep(3)
        self._click(self.coord_expert_mode)
        time.sleep(1)
        # 切换回空输入框坐标
        self.input_box = self.coord_empty_input_box
        self._first_send_done = False
        self._click(self.input_box)
        time.sleep(0.5)
        print("    🆕 已新建对话并开启专家模式")