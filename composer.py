"""쉬운 한국어 작곡 프로그램 (첫 버전)
- 칸을 클릭해서 음을 찍고, 재생 버튼으로 들어보세요.
- 필요한 설치: pip install pygame numpy
"""
import json
import tkinter as tk
from tkinter import filedialog, messagebox

import numpy as np
import pygame

# ---------- 설정 ----------
ROWS = 24            # 음 높이 칸 수 (2옥타브)
COLS = 32            # 시간 칸 수 (한 칸 = 8분음표)
CELL_W = 30
CELL_H = 20
LABEL_W = 60
HIGHEST_MIDI = 83    # 맨 위 음 (시5)
SAMPLE_RATE = 44100
NOTE_NAMES = ["도", "도#", "레", "레#", "미", "파", "파#", "솔", "솔#", "라", "라#", "시"]
BLACK_KEYS = {1, 3, 6, 8, 10}


def midi_to_freq(midi):
    return 440.0 * 2 ** ((midi - 69) / 12)


def make_tone(freq, seconds=0.6):
    """사인파에 서서히 작아지는 소리(감쇠)를 입혀 음 하나를 만든다."""
    t = np.linspace(0, seconds, int(SAMPLE_RATE * seconds), False)
    wave = np.sin(2 * np.pi * freq * t)
    wave += 0.3 * np.sin(2 * np.pi * freq * 2 * t)  # 배음을 살짝 더해 덜 삐-하게
    envelope = np.exp(-4 * t)
    attack = np.minimum(t / 0.01, 1.0)
    wave = wave * envelope * attack
    wave = wave / np.max(np.abs(wave)) * 0.4
    data = (wave * 32767).astype(np.int16)
    return pygame.sndarray.make_sound(data)


class ComposerApp:
    def __init__(self, root):
        self.root = root
        root.title("쉬운 작곡 프로그램")

        pygame.mixer.pre_init(SAMPLE_RATE, -16, 1, 512)
        pygame.mixer.init()
        pygame.mixer.set_num_channels(32)

        self.sounds = {}
        for r in range(ROWS):
            midi = HIGHEST_MIDI - r
            self.sounds[r] = make_tone(midi_to_freq(midi))

        self.grid = [[False] * COLS for _ in range(ROWS)]
        self.playing = False
        self.current_col = 0
        self.after_id = None

        self.build_controls()
        self.build_canvas()
        self.draw_grid()

    # ---------- 화면 만들기 ----------
    def build_controls(self):
        bar = tk.Frame(self.root, padx=8, pady=8)
        bar.pack(fill="x")

        tk.Button(bar, text="▶ 재생", width=8, command=self.play).pack(side="left", padx=2)
        tk.Button(bar, text="■ 정지", width=8, command=self.stop).pack(side="left", padx=2)
        tk.Button(bar, text="모두 지우기", command=self.clear_all).pack(side="left", padx=2)
        tk.Button(bar, text="저장", width=6, command=self.save_song).pack(side="left", padx=(16, 2))
        tk.Button(bar, text="불러오기", command=self.load_song).pack(side="left", padx=2)

        self.loop_var = tk.BooleanVar(value=True)
        tk.Checkbutton(bar, text="반복", variable=self.loop_var).pack(side="left", padx=(16, 2))

        tk.Label(bar, text="빠르기").pack(side="left", padx=(16, 2))
        self.bpm_var = tk.IntVar(value=120)
        tk.Scale(bar, from_=60, to=240, orient="horizontal", length=160,
                 variable=self.bpm_var).pack(side="left")

    def build_canvas(self):
        width = LABEL_W + COLS * CELL_W
        height = ROWS * CELL_H
        self.canvas = tk.Canvas(self.root, width=width, height=height, bg="white",
                                highlightthickness=0)
        self.canvas.pack(padx=8, pady=(0, 8))
        self.canvas.bind("<Button-1>", self.on_click)

    def draw_grid(self):
        self.canvas.delete("all")
        for r in range(ROWS):
            midi = HIGHEST_MIDI - r
            name = NOTE_NAMES[midi % 12]
            octave = midi // 12 - 1
            is_black = (midi % 12) in BLACK_KEYS
            y = r * CELL_H
            # 왼쪽 음 이름
            self.canvas.create_rectangle(0, y, LABEL_W, y + CELL_H,
                                         fill="#333" if is_black else "#f4f4f4", outline="#bbb")
            self.canvas.create_text(LABEL_W / 2, y + CELL_H / 2, text=f"{name}{octave}",
                                    fill="white" if is_black else "black", font=("맑은 고딕", 9))
            for c in range(COLS):
                x = LABEL_W + c * CELL_W
                if self.grid[r][c]:
                    fill = "#3b82f6"
                elif is_black:
                    fill = "#ececec"
                else:
                    fill = "white"
                # 4칸(한 박)마다 구분을 더 진하게
                outline = "#999" if c % 4 == 0 else "#ddd"
                self.canvas.create_rectangle(x, y, x + CELL_W, y + CELL_H,
                                             fill=fill, outline=outline)
        self.draw_playhead()

    def draw_playhead(self):
        self.canvas.delete("playhead")
        if self.playing:
            x = LABEL_W + self.current_col * CELL_W
            self.canvas.create_rectangle(x, 0, x + CELL_W, ROWS * CELL_H,
                                         outline="#ef4444", width=2, tags="playhead")

    # ---------- 동작 ----------
    def on_click(self, event):
        if event.x < LABEL_W:
            # 음 이름을 누르면 그 음을 들려준다
            r = event.y // CELL_H
            if 0 <= r < ROWS:
                self.sounds[r].play()
            return
        c = (event.x - LABEL_W) // CELL_W
        r = event.y // CELL_H
        if 0 <= r < ROWS and 0 <= c < COLS:
            self.grid[r][c] = not self.grid[r][c]
            if self.grid[r][c]:
                self.sounds[r].play()  # 찍는 순간 소리 미리듣기
            self.draw_grid()

    def play(self):
        if self.playing:
            return
        self.playing = True
        self.current_col = 0
        self.step()

    def stop(self):
        self.playing = False
        if self.after_id is not None:
            self.root.after_cancel(self.after_id)
            self.after_id = None
        pygame.mixer.stop()
        self.draw_playhead()

    def step(self):
        if not self.playing:
            return
        for r in range(ROWS):
            if self.grid[r][self.current_col]:
                self.sounds[r].play()
        self.draw_playhead()

        delay = int(60000 / self.bpm_var.get() / 2)  # 한 칸 = 8분음표
        self.current_col += 1
        if self.current_col >= COLS:
            if self.loop_var.get():
                self.current_col = 0
            else:
                self.after_id = self.root.after(delay, self.stop)
                return
        self.after_id = self.root.after(delay, self.step)

    def clear_all(self):
        self.stop()
        self.grid = [[False] * COLS for _ in range(ROWS)]
        self.draw_grid()

    # ---------- 저장 / 불러오기 ----------
    def save_song(self):
        path = filedialog.asksaveasfilename(defaultextension=".json",
                                            filetypes=[("곡 파일", "*.json")])
        if not path:
            return
        notes = [[r, c] for r in range(ROWS) for c in range(COLS) if self.grid[r][c]]
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"bpm": self.bpm_var.get(), "notes": notes}, f, ensure_ascii=False)
        messagebox.showinfo("저장", "곡을 저장했어요.")

    def load_song(self):
        path = filedialog.askopenfilename(filetypes=[("곡 파일", "*.json")])
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            self.stop()
            self.grid = [[False] * COLS for _ in range(ROWS)]
            for r, c in data["notes"]:
                if 0 <= r < ROWS and 0 <= c < COLS:
                    self.grid[r][c] = True
            self.bpm_var.set(data.get("bpm", 120))
            self.draw_grid()
        except Exception:
            messagebox.showerror("오류", "곡 파일을 불러올 수 없어요.")


if __name__ == "__main__":
    root = tk.Tk()
    app = ComposerApp(root)
    root.protocol("WM_DELETE_WINDOW", lambda: (app.stop(), root.destroy()))
    root.mainloop()
