"""Small native editor for JARVIS's saved voice-command library."""

from __future__ import annotations

import json
import threading

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QListWidget, QMessageBox, QPlainTextEdit, QPushButton,
    QSplitter, QVBoxLayout, QWidget,
)

import jarvis_commands as jc
from actions.voice_command import _invalidate


class CommandEditor(QDialog):
    """Browse, create, edit, import, export, and preview saved commands."""

    recorded_step = pyqtSignal(object)
    record_notice = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("JARVIS · Редактор голосовых команд")
        self.resize(930, 650)
        self.lib = jc.Library()
        self.current_id = ""
        self._recording = False
        self._key_listener = self._mouse_listener = None
        self._pressed_modifiers = set()
        self._last_combo = ""
        self._mouse_down = None
        self._typed_capture = False
        self._event_lock = threading.Lock()
        self._last_record_event = 0.0
        self._record_bounds = (0, 0, 0, 0)
        self.recorded_step.connect(self._append_recorded_step)
        self.record_notice.connect(self._record_notice)
        self.setStyleSheet("""
            QDialog { background:#07111b; color:#d9e8f5; }
            QLabel { color:#b7c9d9; }
            QLineEdit,QPlainTextEdit,QListWidget,QComboBox {
                background:#0b1925; color:#dcebf7; border:1px solid #234052;
                border-radius:6px; padding:6px;
            }
            QPushButton { background:#0c2635; color:#9ceaff; border:1px solid #286077;
                border-radius:6px; padding:7px 11px; }
            QPushButton:hover { background:#12384a; }
            QCheckBox { color:#b7c9d9; }
        """)
        root = QVBoxLayout(self)
        heading = QLabel("ГОЛОСОВЫЕ КОМАНДЫ")
        heading.setStyleSheet("color:#57dfff;font-size:16px;font-weight:700")
        root.addWidget(heading)
        split = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(split, 1)

        left = QWidget(); left_lay = QVBoxLayout(left)
        self.search = QLineEdit(); self.search.setPlaceholderText("Поиск по командам…")
        self.search.textChanged.connect(self._refresh)
        left_lay.addWidget(self.search)
        self.items = QListWidget(); self.items.currentRowChanged.connect(self._select)
        left_lay.addWidget(self.items, 1)
        row = QHBoxLayout()
        for title, callback in (("Новая", self._new), ("Копия", self._clone),
                                ("Вкл/выкл", self._toggle), ("Удалить", self._delete)):
            b = QPushButton(title); b.clicked.connect(callback); row.addWidget(b)
        left_lay.addLayout(row)
        orderrow = QHBoxLayout()
        self.command_up = QPushButton("↑ Команда")
        self.command_up.setToolTip("Переместить команду выше в библиотеке")
        self.command_up.clicked.connect(lambda: self._move_command(-1))
        orderrow.addWidget(self.command_up)
        self.command_down = QPushButton("↓ Команда")
        self.command_down.setToolTip("Переместить команду ниже в библиотеке")
        self.command_down.clicked.connect(lambda: self._move_command(1))
        orderrow.addWidget(self.command_down)
        left_lay.addLayout(orderrow)
        split.addWidget(left)

        right = QWidget(); formbox = QVBoxLayout(right)
        form = QFormLayout()
        self.name = QLineEdit(); form.addRow("Название", self.name)
        self.phrases = QPlainTextEdit(); self.phrases.setMaximumHeight(75)
        self.phrases.setPlaceholderText("Фразы-триггеры, по одной на строку")
        form.addRow("Фразы", self.phrases)
        self.folder = QComboBox(); self.folder.setEditable(True)
        form.addRow("Папка", self.folder)
        self.reply = QLineEdit(); form.addRow("Ответ голосом (необязательно)", self.reply)
        self.confirm = QCheckBox("Всегда спрашивать подтверждение перед запуском")
        form.addRow("Защита", self.confirm)
        formbox.addLayout(form)
        formbox.addWidget(QLabel("Шаги сценария · выберите строку, чтобы переставить или скопировать действие"))
        self.step_items = QListWidget()
        self.step_items.setMaximumHeight(135)
        formbox.addWidget(self.step_items)
        stepbar = QHBoxLayout()
        self.step_type = QComboBox()
        self._step_templates = {
            "Запустить приложение": {"type": "launch", "target": "notepad"},
            "Открыть сайт": {"type": "url", "url": "https://"},
            "Открыть файл": {"type": "file", "path": ""},
            "Нажать клавиши": {"type": "hotkey", "keys": "ctrl+"},
            "Нажать клавишу": {"type": "key", "key": "enter"},
            "Щелчок мыши": {"type": "click", "x": 0, "y": 0, "button": "left"},
            "Прокрутка мыши": {"type": "scroll", "x": 0, "y": 0, "ticks": 1},
            "Перетащить мышью": {"type": "drag", "x1": 0, "y1": 0, "x2": 100, "y2": 100},
            "Ввести текст": {"type": "type", "text": ""},
            "Вставить текст": {"type": "paste", "text": ""},
            "Пауза": {"type": "pause", "seconds": 1},
            "Установить громкость": {"type": "volume", "mode": "set", "level": 30},
            "Свернуть все окна": {"type": "minimize_all"},
            "Сказать фразу": {"type": "speak", "text": "Готово."},
            "Спросить ИИ": {"type": "ask", "prompt": "", "speak": True},
        }
        self.step_type.addItems(self._step_templates)
        stepbar.addWidget(self.step_type, 1)
        for label, callback in (("Добавить", self._add_step), ("↑", lambda: self._move_step(-1)),
                                ("↓", lambda: self._move_step(1)), ("Копия", self._clone_step),
                                ("Удалить шаг", self._delete_step)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            stepbar.addWidget(button)
        formbox.addLayout(stepbar)
        formbox.addWidget(QLabel("JSON шагов — ручная настройка всех поддерживаемых параметров"))
        self.steps = QPlainTextEdit(); self.steps.setPlaceholderText('[{"type":"launch","target":"notepad"}]')
        self.steps.textChanged.connect(self._refresh_step_items)
        formbox.addWidget(self.steps, 1)
        recordrow = QHBoxLayout()
        self.capture_text = QCheckBox("Записывать введённый текст")
        self.capture_text.setToolTip("По умолчанию текст клавиатуры не записывается. Не включайте запись на страницах с паролями и личными данными.")
        recordrow.addWidget(self.capture_text)
        self.record_btn = QPushButton("● Начать запись действий")
        self.record_btn.clicked.connect(self._toggle_recording)
        recordrow.addWidget(self.record_btn)
        self.record_label = QLabel("Запись выключена")
        recordrow.addWidget(self.record_label, 1)
        formbox.addLayout(recordrow)
        buttons = QHBoxLayout()
        self.save_btn = QPushButton("Сохранить"); self.save_btn.clicked.connect(self._save)
        buttons.addWidget(self.save_btn)
        ai = QPushButton("Создать с ИИ…"); ai.clicked.connect(self._create_ai); buttons.addWidget(ai)
        preview = QPushButton("Проверить фразу"); preview.clicked.connect(self._preview); buttons.addWidget(preview)
        formbox.addLayout(buttons)
        packrow = QHBoxLayout()
        self.pack = QComboBox()
        import jarvis_packs
        self._packs = jarvis_packs.PACKS
        for pid, spec in self._packs.items(): self.pack.addItem(spec.get("name", pid), pid)
        packrow.addWidget(self.pack, 1)
        install = QPushButton("Установить пак"); install.clicked.connect(self._install_pack)
        packrow.addWidget(install)
        formbox.addLayout(packrow)
        io = QHBoxLayout()
        exp = QPushButton("Экспорт…"); exp.clicked.connect(self._export); io.addWidget(exp)
        imp = QPushButton("Импорт…"); imp.clicked.connect(self._import); io.addWidget(imp)
        io.addStretch(1)
        close = QPushButton("Закрыть"); close.clicked.connect(self.accept); io.addWidget(close)
        formbox.addLayout(io)
        split.addWidget(right); split.setSizes([300, 630])
        self._refresh()

    def _refresh(self, *_):
        old = self.current_id
        self.lib.load()
        self.folder.clear(); self.folder.addItem("(без папки)", "")
        for f in self.lib.folders: self.folder.addItem(f.name, f.id)
        needle = self.search.text().strip().casefold()
        self.items.blockSignals(True); self.items.clear(); row_to_select = 0
        for c in self.lib.commands:
            folder = self.lib.folder_by_id(c.folder)
            label = f"{'●' if c.enabled else '○'}  {c.name}  ·  {folder.name if folder else 'без папки'}"
            if needle and needle not in (label + " " + " ".join(c.phrases)).casefold(): continue
            self.items.addItem(label)
            if c.id == old: row_to_select = self.items.count() - 1
        self.items.blockSignals(False)
        if self.items.count(): self.items.setCurrentRow(min(row_to_select, self.items.count()-1))
        else: self._load_command(None)

    def _visible(self):
        return [c for c in self.lib.commands
                if not self.search.text().strip() or
                self.search.text().strip().casefold() in (c.name + " " + " ".join(c.phrases)).casefold()]

    def _select(self, row):
        commands = self._visible()
        self._load_command(commands[row] if 0 <= row < len(commands) else None)

    def _move_command(self, direction):
        command = self.lib.command_by_id(self.current_id) if self.current_id else None
        if not command:
            return
        index = self.lib.commands.index(command)
        target = index + direction
        if not 0 <= target < len(self.lib.commands):
            return
        self.lib.commands[index], self.lib.commands[target] = self.lib.commands[target], self.lib.commands[index]
        self.lib.save()
        _invalidate()
        self._refresh()

    def _read_steps(self):
        try:
            value = json.loads(self.steps.toPlainText() or "[]")
            return value if isinstance(value, list) else []
        except json.JSONDecodeError:
            return []

    def _refresh_step_items(self):
        steps = self._read_steps()
        row = self.step_items.currentRow() if hasattr(self, "step_items") else -1
        self.step_items.blockSignals(True)
        self.step_items.clear()
        for index, step in enumerate(steps):
            if not isinstance(step, dict):
                label = f"{index + 1}. Некорректный шаг"
            else:
                kind = str(step.get("type") or "?")
                detail = next((str(step[key]) for key in ("target", "url", "text", "keys", "key", "seconds", "query") if step.get(key)), "")
                detail = detail.replace("\n", " ")[:54]
                label = f"{index + 1}. {kind}" + (f" · {detail}" if detail else "")
            self.step_items.addItem(label)
        if self.step_items.count():
            self.step_items.setCurrentRow(min(max(row, 0), self.step_items.count() - 1))
        self.step_items.blockSignals(False)

    def _write_steps(self, steps, selected=-1):
        self.steps.blockSignals(True)
        self.steps.setPlainText(json.dumps(steps, ensure_ascii=False, indent=2))
        self.steps.blockSignals(False)
        self._refresh_step_items()
        if self.step_items.count():
            self.step_items.setCurrentRow(min(max(selected, 0), self.step_items.count() - 1))

    def _add_step(self):
        steps = self._read_steps()
        steps.append(dict(self._step_templates[self.step_type.currentText()]))
        self._write_steps(steps, len(steps) - 1)

    def _move_step(self, direction):
        steps = self._read_steps()
        index = self.step_items.currentRow()
        target = index + direction
        if not (0 <= index < len(steps) and 0 <= target < len(steps)):
            return
        steps[index], steps[target] = steps[target], steps[index]
        self._write_steps(steps, target)

    def _clone_step(self):
        steps = self._read_steps()
        index = self.step_items.currentRow()
        if not 0 <= index < len(steps):
            return
        steps.insert(index + 1, dict(steps[index]))
        self._write_steps(steps, index + 1)

    def _delete_step(self):
        steps = self._read_steps()
        index = self.step_items.currentRow()
        if not 0 <= index < len(steps):
            return
        del steps[index]
        self._write_steps(steps, min(index, len(steps) - 1))

    def _load_command(self, c):
        self.current_id = c.id if c else ""
        self.name.setText(c.name if c else "")
        self.phrases.setPlainText("\n".join(c.phrases) if c else "")
        self.reply.setText(c.reply if c else "")
        self.confirm.setChecked(bool(c and c.confirm))
        self.steps.setPlainText(json.dumps(c.steps, ensure_ascii=False, indent=2) if c else "[]")
        idx = self.folder.findData(c.folder if c else "")
        self.folder.setCurrentIndex(max(0, idx))

    def _new(self):
        self.items.clearSelection(); self._load_command(None); self.name.setFocus()

    def _save(self):
        name = self.name.text().strip()
        phrases = [x.strip() for x in self.phrases.toPlainText().splitlines() if x.strip()]
        if not name or not phrases:
            QMessageBox.warning(self, "Не хватает данных", "Добавьте название и хотя бы одну фразу."); return
        try:
            steps = json.loads(self.steps.toPlainText() or "[]")
            if not isinstance(steps, list) or not steps or any(not isinstance(s, dict) or not s.get("type") for s in steps):
                raise ValueError("Нужен непустой JSON-массив объектов с полем type.")
        except (json.JSONDecodeError, ValueError) as exc:
            QMessageBox.warning(self, "Проверьте шаги", str(exc)); return
        fname = self.folder.currentText().strip()
        f = self.lib.ensure_folder(fname) if fname and fname != "(без папки)" else None
        c = self.lib.command_by_id(self.current_id) if self.current_id else None
        if c is None:
            c = jc.Command(id=jc._new_id(), name=name, phrases=phrases, steps=steps)
            self.lib.add(c)
        c.name=name; c.phrases=phrases; c.steps=steps; c.folder=f.id if f else ""
        c.reply=self.reply.text().strip(); c.confirm=self.confirm.isChecked(); c.modified=True
        c.origin={}; self.lib.save(); self.current_id=c.id; _invalidate(); self._refresh()

    def _clone(self):
        if not self.current_id: return
        clone=self.lib.clone(self.current_id); self.current_id=clone.id; _invalidate(); self._refresh()

    def _toggle(self):
        c=self.lib.command_by_id(self.current_id)
        if c: c.enabled=not c.enabled; self.lib.save(); _invalidate(); self._refresh()

    def _delete(self):
        c=self.lib.command_by_id(self.current_id)
        if not c: return
        answer=QMessageBox.question(self,"Удалить команду?",f"Удалить «{c.name}»?",
                                    QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No)
        if answer==QMessageBox.StandardButton.Yes:
            self.lib.remove(c.id);self.lib.save();self.current_id="";_invalidate();self._refresh()

    def _preview(self):
        phrase=self.phrases.toPlainText().splitlines()
        phrase=next((x.strip() for x in phrase if x.strip()),"")
        if not phrase: return
        matches=jc.match_phrase(phrase,self.lib)
        text="\n".join(f"{m.command.name} — {m.score:.0%}" for m in matches) or "Совпадение не найдено."
        QMessageBox.information(self,"Проверка распознавания",text)

    def _toggle_recording(self):
        if self._recording:
            self._stop_recording()
            return
        try:
            from pynput import keyboard, mouse
        except ImportError:
            QMessageBox.warning(self, "Запись действий", "Для записи действий установите зависимость pynput и перезапустите JARVIS.")
            return
        if self.capture_text.isChecked():
            answer = QMessageBox.warning(
                self, "Запись текста с клавиатуры",
                "Вводимый текст попадёт в сценарий открытым текстом. Не записывайте пароли, коды и личные данные. Продолжить?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            import pyautogui
            sw, sh = pyautogui.size()
        except Exception:
            sw = sh = 0
        self._screen_size = (int(sw), int(sh))
        rect = self.frameGeometry()
        self._record_bounds = (rect.x(), rect.y(), rect.x()+rect.width(), rect.y()+rect.height())
        try:
            self._record_hwnd = int(self.winId())
        except Exception:
            self._record_hwnd = 0
        self._typed_capture = self.capture_text.isChecked()
        self._last_record_event = 0.0
        self._recording = True
        self.record_btn.setText("■ Остановить запись (F10)")
        self.record_label.setText("Запись активна · F10 для остановки")

        def key_name(key):
            try:
                char = getattr(key, "char", None)
                if char: return char.lower()
            except Exception: pass
            return str(getattr(key, "name", "") or str(key).replace("Key.", "")).lower()

        def editor_has_focus():
            # pynput runs on a worker thread. On Windows, compare native window
            # handles instead of querying Qt widgets from that thread.
            if not self._record_hwnd:
                return False
            try:
                import ctypes
                return int(ctypes.windll.user32.GetForegroundWindow()) == self._record_hwnd
            except Exception:
                return False

        def on_press(key):
            if not self._recording: return
            name = key_name(key)
            if name == "f10":
                self.record_notice.emit("stop")
                return
            if editor_has_focus():
                return
            if name in {"ctrl_l", "ctrl_r", "control_l", "control_r", "ctrl"}:
                self._pressed_modifiers.add("ctrl"); return
            if name in {"alt_l", "alt_r", "alt"}:
                self._pressed_modifiers.add("alt"); return
            if name in {"shift", "shift_l", "shift_r"}:
                self._pressed_modifiers.add("shift"); return
            if name in {"cmd", "cmd_l", "cmd_r", "win", "win_l", "win_r"}:
                self._pressed_modifiers.add("win"); return
            mods = sorted(self._pressed_modifiers, key=lambda x: ["ctrl", "alt", "shift", "win"].index(x))
            if mods:
                combo = "+".join(mods + [name])
                if combo != self._last_combo:
                    self._emit_recorded_step({"type":"hotkey", "keys":combo})
                    self._last_combo = combo
                return
            if len(name) == 1:
                if self._typed_capture:
                    self._emit_recorded_step({"type":"type", "text":name})
                return
            keymap = {"space":"space", "enter":"enter", "tab":"tab", "backspace":"backspace",
                      "delete":"delete", "esc":"esc", "up":"up", "down":"down", "left":"left", "right":"right"}
            if name in keymap or (name.startswith("f") and name[1:].isdigit()):
                self._emit_recorded_step({"type":"key", "key":keymap.get(name,name)})

        def on_release(key):
            name = key_name(key)
            aliases = {"ctrl_l":"ctrl", "ctrl_r":"ctrl", "control_l":"ctrl", "control_r":"ctrl",
                       "alt_l":"alt", "alt_r":"alt", "shift_l":"shift", "shift_r":"shift",
                       "cmd_l":"win", "cmd_r":"win", "win_l":"win", "win_r":"win"}
            if name in aliases:
                self._pressed_modifiers.discard(aliases[name])
            elif name in self._pressed_modifiers:
                self._pressed_modifiers.discard(name)
            else:
                self._last_combo = ""

        def in_editor(x, y):
            l,t,r,b = self._record_bounds
            return l <= x <= r and t <= y <= b

        def on_click(x, y, button, pressed):
            if not self._recording or in_editor(x,y): return
            if pressed:
                self._mouse_down=(int(x),int(y),str(button).split(".")[-1])
                return
            start=self._mouse_down;self._mouse_down=None
            if not start:return
            x1,y1,btn=start; x2,y2=int(x),int(y)
            sw,sh=self._screen_size
            if btn=="left" and (abs(x2-x1)>7 or abs(y2-y1)>7):
                step={"type":"drag","x1":x1,"y1":y1,"x2":x2,"y2":y2}
            else:
                step={"type":"click","x":x2,"y":y2,"button":btn,"double":False}
            if sw and sh: step.update(screen_width=sw,screen_height=sh)
            self._emit_recorded_step(step)

        def on_scroll(x,y,dx,dy):
            if not self._recording or in_editor(x,y):return
            sw,sh=self._screen_size
            step={"type":"scroll","x":int(x),"y":int(y),"ticks":int(dy)}
            if sw and sh:step.update(screen_width=sw,screen_height=sh)
            self._emit_recorded_step(step)

        self._key_listener=keyboard.Listener(on_press=on_press,on_release=on_release)
        self._mouse_listener=mouse.Listener(on_click=on_click,on_scroll=on_scroll)
        self._key_listener.start();self._mouse_listener.start()

    def _emit_recorded_step(self, step):
        # Preserve pauses between user actions so a recorded sequence gives
        # applications time to open menus and react before the next input.
        with self._event_lock:
            now = __import__("time").monotonic()
            gap = now - self._last_record_event if self._last_record_event else 0.0
            if 0.55 <= gap <= 30:
                self.recorded_step.emit({"type": "pause", "seconds": round(gap, 1)})
            self._last_record_event = now
            self.recorded_step.emit(step)

    def _append_recorded_step(self, step):
        try:
            steps=json.loads(self.steps.toPlainText() or "[]")
            if not isinstance(steps,list):steps=[]
        except json.JSONDecodeError:steps=[]
        if len(steps) >= 200:
            self._stop_recording()
            QMessageBox.information(self,"Запись остановлена","Достигнут лимит 200 шагов.")
            return
        if step.get("type")=="type" and steps and isinstance(steps[-1],dict) and steps[-1].get("type")=="type":
            steps[-1]["text"]+=step.get("text","")
        else:
            steps.append(dict(step))
        self.steps.setPlainText(json.dumps(steps,ensure_ascii=False,indent=2))

    def _record_notice(self, notice):
        if notice=="stop":self._stop_recording()

    def _stop_recording(self):
        self._recording=False
        for listener in (self._key_listener,self._mouse_listener):
            if listener:
                try:listener.stop()
                except Exception:pass
        self._key_listener=self._mouse_listener=None
        self._pressed_modifiers.clear();self._mouse_down=None
        if hasattr(self,"record_btn"):
            self.record_btn.setText("● Начать запись действий")
            self.record_label.setText("Запись выключена")

    def closeEvent(self, event):
        self._stop_recording()
        super().closeEvent(event)

    def _create_ai(self):
        desc,ok=QInputDialog.getMultiLineText(self,"Создать команду с ИИ","Опишите фразу запуска и желаемые шаги:")
        if not ok or not desc.strip(): return
        try:
            folder=self.lib.ensure_folder(self.folder.currentText()) if self.folder.currentText().strip() not in {"","(без папки)"} else None
            c=jc.ai_create_command(desc.strip(),folder.id if folder else "")
            _invalidate();self.current_id=c.id;self._refresh()
        except Exception as exc:
            QMessageBox.warning(self,"ИИ-конструктор",str(exc))

    def _install_pack(self):
        pid=self.pack.currentData()
        try:
            res=jc.install_pack(self.lib,str(pid));self.lib.save();_invalidate();self._refresh()
            QMessageBox.information(self,"Готовые команды",f"Пак установлен. Добавлено: {res['added']}, обновлено: {res['updated']}.")
        except Exception as exc: QMessageBox.warning(self,"Пак команд",str(exc))

    def _export(self):
        path,_=QFileDialog.getSaveFileName(self,"Экспорт команд","jarvis-commands.json","JSON (*.json)")
        if not path:return
        try:
            payload=self.lib.export_payload(self.current_id or "")
            with open(path,"w",encoding="utf-8") as f:json.dump(payload,f,ensure_ascii=False,indent=2)
        except Exception as exc:QMessageBox.warning(self,"Экспорт",str(exc))

    def _import(self):
        path,_=QFileDialog.getOpenFileName(self,"Импорт команд","","JSON (*.json)")
        if not path:return
        try:
            with open(path,encoding="utf-8") as f:payload=json.load(f)
            if not isinstance(payload,dict) or payload.get("kind") not in {"library","folder","command",None}:
                raise ValueError("Неизвестный формат библиотеки команд.")
            result=self.lib.import_payload(payload,self.folder.currentData() or "")
            _invalidate();self._refresh()
            QMessageBox.information(self,"Импорт",f"Добавлено команд: {result['commands']}; папок: {result['folders']}.")
        except Exception as exc:QMessageBox.warning(self,"Импорт",str(exc))


def open_command_editor(parent=None):
    # Keep Qt imports optional for headless/CLI use of the command engine.
    dialog = CommandEditor(parent)
    dialog.exec()
