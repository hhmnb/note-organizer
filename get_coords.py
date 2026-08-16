import pyautogui, time
print("3秒后开始显示鼠标坐标，Ctrl+C 停止")
time.sleep(3)
try:
    while True:
        x, y = pyautogui.position()
        print(f"({x:4d}, {y:4d})", end='\r')
except KeyboardInterrupt:
    print("\n结束。")