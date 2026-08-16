import os
import json
import re
import time

from note_scanner import NoteScanner
from deepseek_sender import DeepSeekWebSender

# ========================== 配置 ==========================
CONFIG_FILE = "config.json"
TEMPLATE_FILE = "模板.txt"
MARKER = "&"                     # 已处理标记，需与 NoteScanner.MARKER 一致
MIN_FILE_SIZE = 3 * 1024         # 3 KB，小于此大小的 .md 文件将被直接删除
TEMPLATE_INTERVAL = 10           # 每 10 篇发送一次完整模板


def load_config(path: str) -> dict:
    """加载并返回配置字典"""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_template(path: str) -> str:
    """读取提示词模板"""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def validate_and_extract_from_reply(reply: str):
    """
    校验 AI 回复：
    - 必须包含 **笔记ID** 行，从中提取标题（日期后的部分）。
    - 截取从笔记ID行开始，到 "## 修正对照表" 或 "【输入】" 之前的有效内容。
    返回: (is_valid, title, cleaned_content)
    """
    # 1. 强制检查笔记ID
    match = re.search(r'\*\*笔记ID\*\*[：:]\s*\d{4}-\d{2}-\d{2}-(.+)', reply)
    if not match:
        return False, None, ""
    title = match.group(1).strip()
    if not title:
        return False, None, ""

    # 2. 找到笔记ID行的起始位置
    id_start = reply.index(match.group(0))

    # 3. 确定截断结束位置：遇到 "## 修正对照表" 或 "【输入】" 即停止
    end_pos = len(reply)
    for marker in [r'## 修正对照表', r'【输入】']:
        m = re.search(marker, reply[id_start:])
        if m:
            end_pos = id_start + m.start()
            break

    cleaned = reply[id_start:end_pos].strip()
    return True, title, cleaned

def get_unique_path(dir_path: str, title: str) -> str:
    """
    生成不重复的目标文件名：标题&MARKER.md
    若已存在则在 & 前添加两位数字后缀（01, 02, ...）
    """
    base = f"{title}{MARKER}.md"
    candidate = os.path.join(dir_path, base)
    if not os.path.exists(candidate):
        return candidate

    counter = 1
    while True:
        candidate = os.path.join(dir_path, f"{title}{counter:02d}{MARKER}.md")
        if not os.path.exists(candidate):
            return candidate
        counter += 1


def finalize_note(file_path: str, title: str, clean_content: str) -> str:
    """
    用清洗后的内容覆盖原文件，并重命名为防重复的标题&.md。
    返回新路径。
    """
    dir_path = os.path.dirname(file_path)

    # 覆盖写入清理后的内容
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(clean_content)

    # 重命名
    new_path = get_unique_path(dir_path, title)
    os.rename(file_path, new_path)
    return new_path


def main():
    # 1. 加载配置
    config = load_config(CONFIG_FILE)
    notes_folder = config["notes_folder"]
    switch_every = config.get("switch_every", 100)

    # 2. 初始化组件
    scanner = NoteScanner(notes_folder)
    sender = DeepSeekWebSender(config)

    # 3. 加载模板
    if not os.path.exists(TEMPLATE_FILE):
        print(f"❌ 模板文件 {TEMPLATE_FILE} 不存在，退出。")
        return
    template = load_template(TEMPLATE_FILE)

    # 4. 获取待处理笔记（不检查原笔记内容）
    pending = scanner.get_pending_tasks()
    if not pending:
        print("✅ 没有待处理的笔记。")
        return

    # ---- 发送前过滤：删除小于 3KB 的 .md 文件 ----
    print(f"🔍 扫描待处理笔记，删除小于 {MIN_FILE_SIZE // 1024} KB 的文件...")
    filtered_pending = []
    deleted_count = 0
    for note_path in pending:
        try:
            size = os.path.getsize(note_path)
            if size < MIN_FILE_SIZE:
                os.remove(note_path)
                print(f"  🗑️ 已删除（{size} 字节）：{os.path.basename(note_path)}")
                deleted_count += 1
            else:
                filtered_pending.append(note_path)
        except Exception as e:
            print(f"  ⚠️ 无法处理文件，已跳过：{os.path.basename(note_path)} - {e}")
    pending = filtered_pending
    total = len(pending)
    if deleted_count > 0:
        print(f"📊 共删除 {deleted_count} 个小文件，剩余 {total} 篇待处理笔记。")
    if total == 0:
        print("✅ 过滤后没有待处理的笔记。")
        return

    # 每次启动先新建一个专家模式对话
    print("🆕 初始化：新建对话并开启专家模式...")
    sender.new_chat_with_expert()

    print(f"📋 发现 {total} 篇待处理笔记，开始自动化处理...")

    success_count = 0
    fail_list = []
    notes_since_new_chat = 0          # 本次对话中已处理的笔记数（用于控制模板发送频率）

    # 5. 逐篇处理
    for idx, note_path in enumerate(pending, start=1):
        print(f"\n{'='*40}")
        print(f"[{idx}/{total}] 处理: {os.path.basename(note_path)}")

        # 读取笔记内容
        try:
            content = scanner.safe_read(note_path)
        except Exception as e:
            print(f"❌ 读取文件失败: {e}")
            fail_list.append((note_path, f"读取异常: {e}"))
            continue

        # 拼接提示词：新建对话后的第一篇带完整模板，其余仅发送笔记内容
        if notes_since_new_chat == 0:
            prompt = template + "\n" + content
            print("📄 发送完整模板")
        else:
            prompt = content
            print("📝 仅发送笔记内容")

        try:
            reply = sender.send(prompt)
        except Exception as e:
            print(f"❌ 发送/接收异常: {e}")
            fail_list.append((note_path, f"发送异常: {e}"))
            continue

        if not reply:
            print(f"❌ 未获取到有效回复")
            fail_list.append((note_path, "未获取回复"))
            continue

        # 校验回复并提取标题 + 清洗内容
        is_valid, title, clean = validate_and_extract_from_reply(reply)
        if not is_valid:
            print("❌ 回复中缺少有效的 **笔记ID** 行，无法入库")
            fail_list.append((note_path, "缺少笔记ID"))
            continue

        # 入库：覆盖原文件 + 重命名
        try:
            new_path = finalize_note(note_path, title, clean)
            print(f"✅ 已入库：{os.path.basename(new_path)}")
        except Exception as e:
            print(f"❌ 写入/重命名失败: {e}")
            fail_list.append((note_path, f"入库异常: {e}"))
            continue

        success_count += 1
        notes_since_new_chat += 1
        print(f"✅ 完成 {success_count}/{total}")

        # 批量新建对话（除首次外，按篇数间隔触发）
        if switch_every > 0 and success_count % switch_every == 0:
            print(f"🔄 已处理 {success_count} 篇，新建对话并开启专家模式...")
            sender.new_chat_with_expert()
            notes_since_new_chat = 0   # 重置计数器，下一篇将携带模板

        time.sleep(2)

    # 6. 最终报告
    print("\n" + "=" * 50)
    print(f"🎉 处理完成！成功: {success_count}/{total}")
    if deleted_count > 0:
        print(f"🗑️ 已删除小于 {MIN_FILE_SIZE // 1024} KB 的文件: {deleted_count} 个")
    if fail_list:
        print("❌ 失败列表:")
        for path, reason in fail_list:
            print(f"   - {os.path.basename(path)}: {reason}")
    else:
        print("🏆 全部处理成功！")


if __name__ == "__main__":
    main()