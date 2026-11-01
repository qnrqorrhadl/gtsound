"""쉬운 한국어 작곡 프로그램
- 빈 칸을 클릭하면 1칸짜리 음, 오른쪽으로 끌면 길게 이어진 음이 돼요.
- 긴 음의 오른쪽 끝을 잡고 끌면 길이를 늘리거나 줄일 수 있어요.
- 이미 있는 음의 몸통을 클릭하면 지워져요.
- 필요한 설치: pip install pygame numpy
"""
import hashlib
import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import urllib.request
from tkinter import filedialog, messagebox

import numpy as np
import pygame

# ---------- 업데이트 설정 ----------
# ↓ 본인의 'GitHub아이디/저장소이름'으로 바꿔주세요. (공개 저장소여야 해요)
#   바꾸기 전에는 업데이트 확인을 하지 않아요.
GITHUB_REPO = "qnrqorrhadl/gtsound"
USER_AGENT = "composer-updater"
BACKUP_NAME = "composer_backup.exe"   # 업데이트 때 이전 버전이 이 이름으로 남아요
NEW_NAME = "composer_new.exe"         # 받는 중인 새 버전의 임시 이름

# ---------- 설정 ----------
ROWS = 24            # 음 높이 칸 수 (2옥타브)
COLS = 32            # 시간 칸 수 (한 칸 = 8분음표)
CELL_W = 30
CELL_H = 20
LABEL_W = 60
HANDLE_W = 8         # 음의 오른쪽 끝에서 길이 조절용으로 잡히는 폭(픽셀)
HIGHEST_MIDI = 83    # 맨 위 음 (시5)
SAMPLE_RATE = 44100
NOTE_NAMES = ["도", "도#", "레", "레#", "미", "파", "파#", "솔", "솔#", "라", "라#", "시"]
BLACK_KEYS = {1, 3, 6, 8, 10}


def midi_to_freq(midi):
    return 440.0 * 2 ** ((midi - 69) / 12)


def make_tone(freq, seconds):
    """정해진 길이만큼 이어지는 음을 만든다. 끝에서 부드럽게 줄어들어 '틱' 소리가 안 나요."""
    seconds = max(seconds, 0.05)
    n = int(SAMPLE_RATE * seconds)
    t = np.arange(n) / SAMPLE_RATE
    wave = np.sin(2 * np.pi * freq * t)
    wave += 0.3 * np.sin(2 * np.pi * freq * 2 * t)  # 배음을 살짝 더해 덜 삐-하게
    envelope = np.exp(-1.0 * t)                      # 길어도 천천히만 줄어듦
    attack = np.minimum(t / 0.01, 1.0)
    release_len = min(0.05, seconds * 0.3)
    release = np.clip((seconds - t) / release_len, 0.0, 1.0)
    wave = wave * envelope * attack * release
    peak = np.max(np.abs(wave))
    if peak > 0:
        wave = wave / peak * 0.4
    data = (wave * 32767).astype(np.int16)
    init = pygame.mixer.get_init()
    if init and init[2] == 2:                        # 스테레오 장치면 양쪽에 같은 소리
        data = np.column_stack((data, data))
    return pygame.sndarray.make_sound(data)


# ---------- 업데이트 도우미 ----------
def get_version():
    """exe를 만들 때 자동으로 들어가는 버전 번호. 파이썬으로 직접 실행하면 'dev'."""
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        return "dev"
    try:
        with open(os.path.join(base, "version.txt"), encoding="utf-8-sig") as f:
            text = f.read().strip()
    except OSError:
        return "dev"
    return text if text.isdigit() else "dev"


def fetch_latest_release(repo):
    """내 저장소의 최신 Release 정보를 가져온다. 주소가 내 저장소가 아니면 거부한다."""
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.load(resp)
    tag = str(data.get("tag_name", ""))
    if not (tag.startswith("v") and tag[1:].isdigit()):
        return None
    prefix = f"https://github.com/{repo}/releases/download/"
    assets = {a.get("name"): a.get("browser_download_url", "") for a in data.get("assets", [])}
    exe_url = assets.get("composer.exe", "")
    sha_url = assets.get("composer.exe.sha256", "")
    if not (exe_url.startswith(prefix) and sha_url.startswith(prefix)):
        return None
    return {"version": int(tag[1:]), "exe_url": exe_url, "sha_url": sha_url}


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_exe(path, expected):
    """받은 파일이 정상인지 확인: 체크섬 형식, 크기, 윈도우 실행 파일 표시(MZ), 체크섬 일치"""
    if len(expected) != 64 or any(ch not in "0123456789abcdef" for ch in expected):
        return False
    if os.path.getsize(path) < 1_000_000:
        return False
    with open(path, "rb") as f:
        if f.read(2) != b"MZ":
            return False
    return sha256_of(path) == expected


def download_file(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        if not resp.geturl().startswith("https://"):
            raise ValueError("안전하지 않은 주소예요")
        with open(dest, "wb") as f:
            while True:
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)


def update_worker(info, new_path, q):
    """(별도 작업 줄에서) 체크섬과 새 exe를 받아 확인한다. 결과는 q로 알려준다."""
    try:
        req = urllib.request.Request(info["sha_url"], headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=10) as resp:
            if not resp.geturl().startswith("https://"):
                raise ValueError("안전하지 않은 주소예요")
            expected = resp.read(200).decode("ascii", "ignore").split()[0].lower()
        download_file(info["exe_url"], new_path)
        if not verify_exe(new_path, expected):
            raise ValueError("받은 파일이 올바르지 않아요 (체크섬 불일치)")
        q.put(("ok", new_path))
    except Exception as e:
        try:
            os.remove(new_path)
        except OSError:
            pass
        q.put(("error", str(e)))


def apply_update(new_path):
    """실행 중인 exe를 백업해 두고 새 exe로 바꾼 뒤 다시 실행한다. 실패하면 되돌린다.
    (윈도우는 실행 중인 exe를 지울 수는 없어도 이름은 바꿀 수 있어요)"""
    current = sys.executable
    backup = os.path.join(os.path.dirname(current), BACKUP_NAME)
    os.replace(current, backup)
    try:
        os.replace(new_path, current)
    except Exception:
        os.replace(backup, current)   # 되돌리기
        raise
    env = dict(os.environ)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"   # 새 exe가 이전 실행 흔적을 물려받지 않게
    subprocess.Popen([current], env=env, close_fds=True)


class ComposerApp:
    def __init__(self, root):
        self.root = root
        self.version = get_version()
        root.title("쉬운 작곡 프로그램" if self.version == "dev"
                   else f"쉬운 작곡 프로그램 v{self.version}")
        self.update_q = queue.Queue()
        self.wait_win = None

        pygame.mixer.pre_init(SAMPLE_RATE, -16, 1, 512)
        pygame.mixer.init()
        pygame.mixer.set_num_channels(64)

        self.notes = []          # 음 목록: [높이 줄, 시작 칸, 길이(칸)]
        self.sound_cache = {}    # (줄, 길이, 빠르기) -> 소리
        self.drag_note = None    # 지금 끌고 있는 음
        self.playing = False
        self.current_col = 0
        self.after_id = None

        self.build_controls()
        self.build_canvas()
        self.draw_grid()
        self.draw_notes()
        self.root.after(1500, self.check_update)   # 켜진 뒤 잠깐 있다가 새 버전 확인

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

        tk.Label(self.root, fg="#555", font=("맑은 고딕", 9),
                 text="클릭: 음 찍기 · 오른쪽으로 끌기: 길게 · 음의 오른쪽 끝을 끌기: 길이 조절 · 음 클릭: 지우기"
                 ).pack(anchor="w", padx=10)

    def build_canvas(self):
        width = LABEL_W + COLS * CELL_W
        height = ROWS * CELL_H
        self.canvas = tk.Canvas(self.root, width=width, height=height, bg="white",
                                highlightthickness=0)
        self.canvas.pack(padx=8, pady=(4, 8))
        self.canvas.bind("<Button-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Motion>", self.on_motion)

    def draw_grid(self):
        """바탕 칸과 왼쪽 음 이름 (한 번만 그린다)"""
        for r in range(ROWS):
            midi = HIGHEST_MIDI - r
            name = NOTE_NAMES[midi % 12]
            octave = midi // 12 - 1
            is_black = (midi % 12) in BLACK_KEYS
            y = r * CELL_H
            self.canvas.create_rectangle(0, y, LABEL_W, y + CELL_H,
                                         fill="#333" if is_black else "#f4f4f4", outline="#bbb",
                                         tags="grid")
            self.canvas.create_text(LABEL_W / 2, y + CELL_H / 2, text=f"{name}{octave}",
                                    fill="white" if is_black else "black",
                                    font=("맑은 고딕", 9), tags="grid")
            for c in range(COLS):
                x = LABEL_W + c * CELL_W
                fill = "#ececec" if is_black else "white"
                outline = "#999" if c % 4 == 0 else "#ddd"   # 4칸마다 구분선을 진하게
                self.canvas.create_rectangle(x, y, x + CELL_W, y + CELL_H,
                                             fill=fill, outline=outline, tags="grid")

    def draw_notes(self):
        """음 막대를 다시 그린다"""
        self.canvas.delete("note")
        for r, c, length in self.notes:
            x1 = LABEL_W + c * CELL_W + 1
            x2 = LABEL_W + (c + length) * CELL_W - 1
            y1 = r * CELL_H + 1
            y2 = (r + 1) * CELL_H - 1
            self.canvas.create_rectangle(x1, y1, x2, y2, fill="#3b82f6", outline="#1d4ed8",
                                         tags="note")
            # 오른쪽 끝: 길이 조절 손잡이
            self.canvas.create_rectangle(x2 - HANDLE_W, y1, x2, y2, fill="#1d4ed8", outline="",
                                         tags="note")
        self.canvas.tag_raise("playhead")

    def draw_playhead(self):
        self.canvas.delete("playhead")
        if self.playing:
            x = LABEL_W + self.current_col * CELL_W
            self.canvas.create_rectangle(x, 0, x + CELL_W, ROWS * CELL_H,
                                         outline="#ef4444", width=2, tags="playhead")

    # ---------- 음 다루기 ----------
    def note_at(self, r, c):
        """r줄 c칸을 덮고 있는 음을 찾는다"""
        for n in self.notes:
            if n[0] == r and n[1] <= c < n[1] + n[2]:
                return n
        return None

    def max_length(self, note):
        """같은 줄의 다음 음과 겹치지 않는 최대 길이"""
        r, start = note[0], note[1]
        limit = COLS
        for n in self.notes:
            if n is not note and n[0] == r and n[1] > start:
                limit = min(limit, n[1])
        return limit - start

    def right_edge_x(self, note):
        return LABEL_W + (note[1] + note[2]) * CELL_W

    def cell_at(self, event):
        r = event.y // CELL_H
        c = (event.x - LABEL_W) // CELL_W
        if 0 <= r < ROWS and 0 <= c < COLS:
            return r, c
        return None

    # ---------- 마우스 ----------
    def on_press(self, event):
        if event.x < LABEL_W:
            # 음 이름을 누르면 그 음을 들려준다
            r = event.y // CELL_H
            if 0 <= r < ROWS:
                self.play_note(r, 1)
            return
        cell = self.cell_at(event)
        if cell is None:
            return
        r, c = cell
        note = self.note_at(r, c)
        if note is None:
            # 빈 칸: 1칸짜리 새 음을 만들고, 끌면 길어진다
            note = [r, c, 1]
            self.notes.append(note)
            self.drag_note = note
            self.draw_notes()
        elif event.x >= self.right_edge_x(note) - HANDLE_W:
            # 음의 오른쪽 끝: 길이 조절
            self.drag_note = note
        else:
            # 음의 몸통: 지우기
            self.notes.remove(note)
            self.draw_notes()

    def on_drag(self, event):
        note = self.drag_note
        if note is None:
            return
        c = (event.x - LABEL_W) // CELL_W
        new_len = max(1, min(c - note[1] + 1, self.max_length(note)))
        if new_len != note[2]:
            note[2] = new_len
            self.draw_notes()

    def on_release(self, event):
        note = self.drag_note
        self.drag_note = None
        if note is not None:
            self.play_note(note[0], note[2])   # 길이를 귀로 확인

    def on_motion(self, event):
        cursor = ""
        if event.x >= LABEL_W and self.drag_note is None:
            cell = self.cell_at(event)
            if cell is not None:
                note = self.note_at(*cell)
                if note is not None and event.x >= self.right_edge_x(note) - HANDLE_W:
                    cursor = "sb_h_double_arrow"
        self.canvas.config(cursor=cursor)

    # ---------- 소리 ----------
    def get_sound(self, row, length):
        bpm = self.bpm_var.get()
        key = (row, length, bpm)
        sound = self.sound_cache.get(key)
        if sound is None:
            if len(self.sound_cache) > 400:
                self.sound_cache.clear()
            step_seconds = 60.0 / bpm / 2        # 한 칸 = 8분음표
            sound = make_tone(midi_to_freq(HIGHEST_MIDI - row), length * step_seconds)
            self.sound_cache[key] = sound
        return sound

    def play_note(self, row, length):
        self.get_sound(row, length).play()

    # ---------- 재생 ----------
    def play(self):
        if self.playing:
            return
        for r, _, length in self.notes:          # 끊김 없게 미리 소리를 만들어 둔다
            self.get_sound(r, length)
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

    def finish(self):
        """곡이 끝났을 때: 마지막 음의 여운은 끊지 않고 재생만 끝낸다"""
        self.playing = False
        self.after_id = None
        self.draw_playhead()

    def step(self):
        if not self.playing:
            return
        for r, c, length in self.notes:
            if c == self.current_col:
                self.get_sound(r, length).play()
        self.draw_playhead()

        delay = int(60000 / self.bpm_var.get() / 2)  # 한 칸 = 8분음표
        self.current_col += 1
        if self.current_col >= COLS:
            if self.loop_var.get():
                self.current_col = 0
            else:
                self.after_id = self.root.after(delay, self.finish)
                return
        self.after_id = self.root.after(delay, self.step)

    def clear_all(self):
        self.stop()
        self.notes = []
        self.draw_notes()

    # ---------- 업데이트 ----------
    def check_update(self):
        """exe로 실행 중일 때만, 새 버전이 있는지 조용히 확인한다."""
        if not getattr(sys, "frozen", False) or self.version == "dev":
            return
        if GITHUB_REPO.startswith("아이디"):
            return
        threading.Thread(target=self._check_worker, daemon=True).start()
        self.root.after(500, self.poll_update)

    def _check_worker(self):
        try:
            info = fetch_latest_release(GITHUB_REPO)
            if info and info["version"] > int(self.version):
                self.update_q.put(("found", info))
                return
        except Exception:
            pass                       # 인터넷이 없거나 실패해도 조용히 넘어간다
        self.update_q.put(("none", None))

    def poll_update(self):
        try:
            kind, value = self.update_q.get_nowait()
        except queue.Empty:
            self.root.after(500, self.poll_update)
            return
        if kind == "found":
            self.ask_update(value)
        elif kind == "ok":
            self.finish_update(value)
        elif kind == "error":
            self.update_failed(value)

    def ask_update(self, info):
        ok = messagebox.askyesno(
            "업데이트",
            f"새 버전(v{info['version']})이 있어요.\n지금 받아서 바꿀까요?\n\n"
            f"(지금 버전은 v{self.version}이고, 이전 버전은 {BACKUP_NAME}로 남겨둬요)")
        if not ok:
            return
        self.stop()
        self.wait_win = tk.Toplevel(self.root)
        self.wait_win.title("업데이트")
        tk.Label(self.wait_win, text="새 버전을 받는 중이에요...\n잠시만 기다려 주세요.",
                 padx=30, pady=20).pack()
        new_path = os.path.join(os.path.dirname(sys.executable), NEW_NAME)
        threading.Thread(target=update_worker, args=(info, new_path, self.update_q),
                         daemon=True).start()
        self.root.after(500, self.poll_update)

    def close_wait_win(self):
        if self.wait_win is not None:
            self.wait_win.destroy()
            self.wait_win = None

    def finish_update(self, new_path):
        self.close_wait_win()
        try:
            apply_update(new_path)
        except Exception as e:
            try:
                os.remove(new_path)
            except OSError:
                pass
            self.update_failed(str(e))
            return
        self.root.destroy()            # 새 버전이 이미 실행됐으니 이전 창은 닫는다

    def update_failed(self, reason):
        self.close_wait_win()
        messagebox.showerror("업데이트 실패",
                             f"업데이트를 하지 못했어요. 지금 버전은 그대로 쓸 수 있어요.\n\n(사유: {reason})")

    # ---------- 저장 / 불러오기 ----------
    def save_song(self):
        path = filedialog.asksaveasfilename(defaultextension=".json",
                                            filetypes=[("곡 파일", "*.json")])
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"bpm": self.bpm_var.get(), "notes": self.notes}, f, ensure_ascii=False)
        messagebox.showinfo("저장", "곡을 저장했어요.")

    def load_song(self):
        path = filedialog.askopenfilename(filetypes=[("곡 파일", "*.json")])
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            notes = []
            for item in data["notes"]:
                r, c = int(item[0]), int(item[1])
                length = int(item[2]) if len(item) >= 3 else 1   # 옛날 파일은 길이 1칸
                if 0 <= r < ROWS and 0 <= c < COLS:
                    length = max(1, min(length, COLS - c))
                    notes.append([r, c, length])
            self.stop()
            self.notes = notes
            self.bpm_var.set(data.get("bpm", 120))
            self.draw_notes()
        except Exception:
            messagebox.showerror("오류", "곡 파일을 불러올 수 없어요.")


if __name__ == "__main__":
    root = tk.Tk()
    app = ComposerApp(root)
    root.protocol("WM_DELETE_WINDOW", lambda: (app.stop(), root.destroy()))
    root.mainloop()
