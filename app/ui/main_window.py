"""Native Qt project workflow. External operations run off the GUI thread."""
import json
import webbrowser
from pathlib import Path
from PySide6.QtCore import Qt, QObject, QThread, Signal, QTimer
from PySide6.QtWidgets import (QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
    QFileDialog, QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTableWidget, QTableWidgetItem,
    QTextEdit, QListWidget, QStackedWidget, QMessageBox, QFormLayout, QCheckBox, QDateTimeEdit,
    QInputDialog, QProgressBar, QSlider, QHeaderView, QListWidgetItem)
from app.core import settings as preferences
from app.core import credentials
from app.projects.store import ProjectStore
from app.ai.schemas import Candidate, Metadata
from app.ai.scoring import rank
from app.ai import gemini
from app.video import engine
from app.video.captions import load_srt
from app.youtube import client as youtube


class Worker(QObject):
    done = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, function):
        super().__init__()
        self.function = function

    def run(self):
        try:
            self.done.emit(self.function())
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('AI Shorts Maker Pro')
        self.resize(1250, 800)
        self.settings = preferences.load()
        self.store = None
        self.busy = False
        self.threads = []
        self.flow = None
        self.player = None
        self.render_queue = []
        root = QWidget()
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        self.navigation = QListWidget()
        self.navigation.setFixedWidth(185)
        self.pages = QStackedWidget()
        layout.addWidget(self.navigation)
        layout.addWidget(self.pages, 1)
        for name, builder in [('Dashboard', self.dashboard_page), ('New Project', self.new_page),
                              ('Analysis', self.analysis_page), ('Shorts & Editor', self.shorts_page),
                              ('Metadata', self.metadata_page), ('Publishing', self.publish_page),
                              ('Settings', self.settings_page)]:
            self.navigation.addItem(name)
            self.pages.addWidget(builder())
        self.navigation.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.navigation.setCurrentRow(0)
        self.status = QLabel('Ready · Local video stays on your computer')
        self.statusBar().addPermanentWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setFixedWidth(120)
        self.progress.hide()
        self.statusBar().addPermanentWidget(self.progress)
        self.refresh_dashboard()
        self.load_recent()

    def page(self, title):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        heading = QLabel(title)
        heading.setObjectName('heading')
        layout.addWidget(heading)
        return widget, layout

    def button(self, text, callback, layout):
        button = QPushButton(text)
        button.clicked.connect(callback)
        layout.addWidget(button)
        return button

    def dashboard_page(self):
        page, layout = self.page('Your production studio')
        layout.addWidget(QLabel('Create Shorts from a local video, review every step, then publish on your terms.'))
        self.summary = QLabel('No project open')
        layout.addWidget(self.summary)
        self.button('New Project', lambda: self.navigation.setCurrentRow(1), layout)
        self.button('Open Project Folder…', self.open_project_dialog, layout)
        layout.addWidget(QLabel('Recent projects'))
        self.recent = QListWidget()
        self.recent.itemDoubleClicked.connect(lambda item: self.open_project(item.data(Qt.ItemDataRole.UserRole)))
        layout.addWidget(self.recent)
        return page

    def file_row(self, layout, label, file_filter):
        row = QHBoxLayout()
        edit = QLineEdit()
        row.addWidget(QLabel(label))
        row.addWidget(edit, 1)
        self.button('Browse…', lambda: edit.setText(QFileDialog.getOpenFileName(self, label, '', file_filter)[0] or edit.text()), row)
        layout.addLayout(row)
        return edit

    def new_page(self):
        page, layout = self.page('New Project')
        layout.addWidget(QLabel('Choose a local video and SRT, audio, or both. The video is never sent to Gemini.'))
        self.video_input = self.file_row(layout, 'Video', 'Video (*.mp4 *.mov *.mkv *.avi *.webm)')
        self.srt_input = self.file_row(layout, 'SRT', 'SubRip (*.srt)')
        self.audio_input = self.file_row(layout, 'Audio', 'Audio (*.mp3 *.wav *.m4a *.aac *.flac *.ogg)')
        form = QFormLayout()
        self.count = QSpinBox(); self.count.setRange(1, 30); self.count.setValue(5)
        self.minimum = QSpinBox(); self.minimum.setRange(3, 180); self.minimum.setValue(15)
        self.maximum = QSpinBox(); self.maximum.setRange(5, 180); self.maximum.setValue(60)
        self.crop = QComboBox(); self.crop.addItems(['center', 'manual'])
        self.offset = QDoubleSpinBox(); self.offset.setRange(0, 1); self.offset.setSingleStep(.05); self.offset.setValue(.5)
        self.audio_preset = QComboBox(); self.audio_preset.addItems(['original', 'balanced', 'loud', 'voice']); self.audio_preset.setCurrentText('balanced')
        for label, control in [('Number of Shorts', self.count), ('Minimum seconds', self.minimum),
                               ('Maximum seconds', self.maximum), ('Crop mode', self.crop),
                               ('Manual horizontal offset (0–1)', self.offset), ('Audio preset', self.audio_preset)]:
            form.addRow(label, control)
        layout.addLayout(form)
        self.button('Create project & analyze', self.create_project, layout)
        layout.addStretch()
        return page

    def analysis_page(self):
        page, layout = self.page('Discover Shorts')
        self.analysis_info = QLabel('Create or open a project to begin.')
        layout.addWidget(self.analysis_info)
        self.button('Analyze / use cached result', lambda: self.start_analysis(False), layout)
        self.button('Reanalyze (new Gemini request)', lambda: self.start_analysis(True), layout)
        self.candidates_table = QTableWidget(0, 6)
        self.candidates_table.setHorizontalHeaderLabels(['Select', 'ID', 'Time range', 'Duration', 'Topic / hook', 'Score'])
        self.candidates_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.candidates_table)
        self.button('Save selections & review Shorts', self.save_selection, layout)
        return page

    def shorts_page(self):
        page, layout = self.page('Shorts & Editor')
        self.shorts_table = QTableWidget(0, 4)
        self.shorts_table.setHorizontalHeaderLabels(['ID', 'Topic', 'Status', 'Video file'])
        self.shorts_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.shorts_table.itemSelectionChanged.connect(self.load_editor)
        layout.addWidget(self.shorts_table)
        self.preview = QWidget()
        self.preview.setMinimumHeight(240)
        self.preview.setStyleSheet('background:#101820')
        layout.addWidget(self.preview)
        controls = QHBoxLayout()
        self.button('Play / Pause', self.play_pause, controls)
        self.button('Preview source range', self.preview_source, controls)
        self.seek = QSlider(Qt.Orientation.Horizontal); self.seek.setRange(0, 1000)
        self.seek.sliderReleased.connect(self.seek_video)
        controls.addWidget(self.seek, 1)
        layout.addLayout(controls)
        form = QFormLayout()
        self.edit_start = QLineEdit(); self.edit_end = QLineEdit()
        form.addRow('Start HH:MM:SS.mmm', self.edit_start); form.addRow('End HH:MM:SS.mmm', self.edit_end)
        layout.addLayout(form)
        row = QHBoxLayout()
        self.button('Save time range', self.save_range, row)
        self.button('Render selected Short', self.render_selected, row)
        self.button('Render all selected', self.render_all, row)
        self.button('Extract thumbnail', self.create_thumbnail, row)
        layout.addLayout(row)
        QTimer.singleShot(0, self.init_vlc)
        return page

    def metadata_page(self):
        page, layout = self.page('Metadata & review')
        self.meta_select = QComboBox()
        self.meta_select.currentIndexChanged.connect(self.load_metadata)
        layout.addWidget(self.meta_select)
        self.button('Generate (uses cache if available)', lambda: self.generate_metadata(False), layout)
        self.button('Regenerate (replaces fields after confirmation)', lambda: self.generate_metadata(True), layout)
        self.title_options = QComboBox()
        self.title_options.currentTextChanged.connect(lambda value: self.title_edit.setText(value) if value else None)
        layout.addWidget(self.title_options)
        self.title_edit = QLineEdit(); self.description_edit = QTextEdit(); self.tags_edit = QLineEdit(); self.hashtags_edit = QLineEdit(); self.category_edit = QLineEdit('22')
        form = QFormLayout()
        for label, control in [('Selected title', self.title_edit), ('Description', self.description_edit),
                               ('Tags (comma-separated)', self.tags_edit), ('Hashtags', self.hashtags_edit),
                               ('YouTube category ID', self.category_edit)]:
            form.addRow(label, control)
        layout.addLayout(form)
        self.button('Save manual edits', self.save_metadata, layout)
        return page

    def publish_page(self):
        page, layout = self.page('YouTube publishing · review first')
        self.channel = QLabel('Not connected')
        layout.addWidget(self.channel)
        self.client_secrets = self.file_row(layout, 'OAuth client JSON', 'JSON (*.json)')
        self.button('Connect YouTube', self.connect_youtube, layout)
        self.button('Disconnect', self.disconnect_youtube, layout)
        self.publish_select = QComboBox()
        layout.addWidget(self.publish_select)
        self.visibility = QComboBox(); self.visibility.addItems(['private', 'unlisted', 'public'])
        layout.addWidget(self.visibility)
        self.scheduled = QCheckBox('Schedule for local date and time (uploads as private)')
        layout.addWidget(self.scheduled)
        self.publish_time = QDateTimeEdit(); self.publish_time.setCalendarPopup(True)
        self.publish_time.setDateTime(self.publish_time.dateTime().addDays(1))
        layout.addWidget(self.publish_time)
        self.button('Review & upload selected', self.publish, layout)
        self.upload_status = QLabel('Nothing uploaded yet')
        layout.addWidget(self.upload_status)
        layout.addStretch()
        return page

    def settings_page(self):
        page, layout = self.page('Settings')
        form = QFormLayout()
        self.analysis_model = QLineEdit(self.settings['analysis_model'])
        self.metadata_model = QLineEdit(self.settings['metadata_model'])
        self.ffmpeg_path = QLineEdit(self.settings['ffmpeg'])
        self.ffprobe_path = QLineEdit(self.settings['ffprobe'])
        self.projects_path = QLineEdit(self.settings['projects_dir'])
        self.analysis_key = QLineEdit(); self.analysis_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.metadata_key = QLineEdit(); self.metadata_key.setEchoMode(QLineEdit.EchoMode.Password)
        for label, widget in [('Analysis model', self.analysis_model), ('Metadata model', self.metadata_model),
                              ('FFmpeg executable', self.ffmpeg_path), ('ffprobe executable', self.ffprobe_path),
                              ('Projects directory', self.projects_path), ('Analysis API key (vault)', self.analysis_key),
                              ('Metadata API key (vault)', self.metadata_key)]:
            form.addRow(label, widget)
        layout.addLayout(form)
        self.button('Save settings and keys', self.save_settings, layout)
        self.button('Test Gemini credentials', self.test_credentials, layout)
        self.button('Delete API credentials from vault', self.clear_credentials, layout)
        layout.addStretch()
        return page

    def alert(self, message):
        QMessageBox.warning(self, 'AI Shorts Maker Pro', message)

    def work(self, kind, operation, done, candidate_id=None):
        if self.busy:
            self.alert('A job is already running. Wait for it to finish.')
            return
        self.busy = True
        self.progress.setRange(0, 0); self.progress.show()
        self.status.setText(f'{kind}…')
        project = self.store
        job_id = project.job(kind, candidate_id) if project else None
        thread = QThread(self)
        worker = Worker(operation)
        worker.moveToThread(thread)
        self.threads.append((thread, worker))
        def success(value):
            if job_id:
                project.job_status(job_id, 'COMPLETED')
            done(value)
            self.status.setText(f'{kind} complete')
        def failure(message):
            if job_id:
                project.job_status(job_id, 'FAILED', message)
            self.status.setText(f'{kind} failed')
            self.alert(message)
        def cleanup():
            self.busy = False; self.progress.hide()
            self.refresh_dashboard()
            if kind.startswith('Render #') and self.render_queue:
                QTimer.singleShot(0, self.next_render)
        worker.done.connect(success); worker.failed.connect(failure)
        worker.finished.connect(thread.quit); worker.finished.connect(cleanup)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda: self.threads.remove((thread, worker)) if (thread, worker) in self.threads else None)
        thread.started.connect(worker.run)
        if job_id:
            project.job_status(job_id, 'RUNNING')
        thread.start()

    def create_project(self):
        try:
            video, srt, audio = self.video_input.text().strip(), self.srt_input.text().strip(), self.audio_input.text().strip()
            if not video or not Path(video).is_file() or not (srt or audio):
                raise ValueError('Select an existing video and at least an SRT or an audio file')
            if self.minimum.value() > self.maximum.value():
                raise ValueError('Minimum duration cannot exceed maximum')
            for path in (srt, audio):
                if path and not Path(path).is_file():
                    raise FileNotFoundError(path)
            if srt:
                load_srt(srt)
            source = Path(video).resolve()
            base = Path(self.settings['projects_dir']) / source.stem
            folder = base
            number = 2
            while (folder / 'project.db').exists():
                folder = Path(f'{base}_{number}'); number += 1
            store = ProjectStore(folder)
            store.create(source.stem, source, srt, audio,
                         {'count': self.count.value(), 'min': self.minimum.value(), 'max': self.maximum.value(),
                          'crop': self.crop.currentText(), 'offset': self.offset.value(), 'audio_preset': self.audio_preset.currentText()})
            self.set_project(store)
            self.navigation.setCurrentRow(2)
            self.start_analysis(False)
        except Exception as exc:
            self.alert(str(exc))

    def open_project_dialog(self):
        folder = QFileDialog.getExistingDirectory(self, 'Open project folder')
        if folder:
            self.open_project(folder)

    def open_project(self, folder):
        try:
            if not (Path(folder) / 'project.db').is_file():
                raise ValueError('No project.db in this folder')
            store = ProjectStore(folder)
            if not store.project():
                raise ValueError('This project has no source video')
            self.set_project(store)
            self.navigation.setCurrentRow(2)
        except Exception as exc:
            self.alert(str(exc))

    def set_project(self, store):
        if self.busy:
            store.close()
            return self.alert('Wait for the current job before switching projects')
        if self.store:
            self.store.close()
        self.store = store
        recent = [str(store.folder)] + [x for x in self.settings['recent'] if x != str(store.folder)]
        self.settings['recent'] = recent[:20]
        preferences.save(self.settings)
        self.refresh_all()

    def load_recent(self):
        self.recent.clear()
        for path in self.settings['recent']:
            if (Path(path) / 'project.db').is_file():
                item = QListWidgetItem(Path(path).name)
                item.setData(Qt.ItemDataRole.UserRole, path)
                self.recent.addItem(item)

    def refresh_dashboard(self):
        self.load_recent()
        if self.store:
            shorts = self.store.shorts()
            self.summary.setText(f'{self.store.project()["name"]}  ·  {len(shorts)} selected  ·  '
                                 f'{sum(x["status"] == "READY" for x in shorts)} ready  ·  '
                                 f'{sum(x["status"] in ("UPLOADED", "SCHEDULED") for x in shorts)} uploaded  ·  '
                                 f'{sum(x["status"] == "FAILED" for x in shorts)} failed')

    def refresh_all(self):
        self.refresh_dashboard(); self.refresh_candidates(); self.refresh_shorts(); self.refresh_meta_options()
        self.analysis_info.setText(f'Project: {self.store.project()["name"]}  ·  {len(self.store.candidates())} candidates')

    def start_analysis(self, force):
        if not self.store:
            return self.alert('Create or open a project first')
        folder = str(self.store.folder); settings = self.settings.copy()
        def operation():
            with_store = ProjectStore(folder, recover=False)
            try:
                duration = engine.duration_of(engine.probe(with_store.project()['source'], settings['ffprobe']))
                options = json.loads(with_store.project()['settings'])
                result = gemini.analyze(with_store, settings, duration, force)
                items = rank(result.candidates, options['min'], options['max'], (options['min']+options['max'])/2,
                             options['count'] * 2, duration)
                with_store.save_candidates(items)
                return len(items)
            finally:
                with_store.close()
        self.work('Analysis', operation, lambda count: (self.refresh_all(), self.status.setText(f'{count} candidates ranked')))

    def refresh_candidates(self):
        self.candidates_table.setRowCount(0)
        if not self.store:
            return
        for row, item in enumerate(self.store.candidates()):
            data = Candidate.model_validate_json(item['data'])
            self.candidates_table.insertRow(row)
            check = QCheckBox(); check.setChecked(bool(item['selected']))
            self.candidates_table.setCellWidget(row, 0, check)
            for col, value in enumerate([str(item['id']), f'{data.start} – {data.end}', f'{data.duration:.1f}s',
                                         f'{data.topic} · {data.hook}', str(item['score'])], 1):
                self.candidates_table.setItem(row, col, QTableWidgetItem(value))

    def save_selection(self):
        if not self.store:
            return self.alert('Open a project first')
        for row in range(self.candidates_table.rowCount()):
            cid = int(self.candidates_table.item(row, 1).text())
            self.store.select(cid, self.candidates_table.cellWidget(row, 0).isChecked())
        self.refresh_all(); self.navigation.setCurrentRow(3)

    def refresh_shorts(self):
        self.shorts_table.setRowCount(0)
        if not self.store:
            return
        candidates = {x['id']: Candidate.model_validate_json(x['data']) for x in self.store.candidates()}
        for row, short in enumerate(self.store.shorts()):
            self.shorts_table.insertRow(row)
            cid = short['candidate_id']
            for col, value in enumerate([str(cid), candidates[cid].topic if cid in candidates else '',
                                         short['status'], short['video'] or 'Not rendered']):
                self.shorts_table.setItem(row, col, QTableWidgetItem(value))

    def selected_id(self):
        row = self.shorts_table.currentRow()
        return int(self.shorts_table.item(row, 0).text()) if row >= 0 and self.shorts_table.item(row, 0) else None

    def candidate(self, cid):
        row = next((r for r in self.store.candidates() if r['id'] == cid), None)
        if not row:
            raise ValueError('Candidate not found')
        return Candidate.model_validate_json(row['data'])

    def load_editor(self):
        cid = self.selected_id()
        if cid is None or not self.store:
            return
        candidate = self.candidate(cid)
        self.edit_start.setText(candidate.start); self.edit_end.setText(candidate.end)
        short = next((s for s in self.store.shorts() if s['candidate_id'] == cid), None)
        if short and short['video'] and Path(short['video']).exists():
            self.load_video(short['video'])

    def save_range(self):
        cid = self.selected_id()
        if cid is None:
            return self.alert('Select a Short first')
        try:
            from app.core.timecode import parse_time
            data = self.candidate(cid)
            start, end = self.edit_start.text(), self.edit_end.text()
            data = data.model_copy(update={'start': start, 'end': end, 'duration': parse_time(end)-parse_time(start)})
            data = Candidate.model_validate(data.model_dump())
            info = engine.probe(self.store.project()['source'], self.settings['ffprobe'])
            if parse_time(end) > engine.duration_of(info):
                raise ValueError('End exceeds source duration')
            with self.store.db:
                self.store.db.execute('UPDATE candidates SET data=? WHERE id=?', (data.model_dump_json(), cid))
            self.store.update_short(cid, video=None, captions=None, thumbnail=None, status='SELECTED')
            self.refresh_all()
        except Exception as exc:
            self.alert(str(exc))

    def render_selected(self):
        cid = self.selected_id()
        if cid is None:
            return self.alert('Select a Short first')
        self.start_render(cid)

    def render_all(self):
        if not self.store:
            return self.alert('Open a project first')
        pending = [s['candidate_id'] for s in self.store.shorts() if not s['video'] or not Path(s['video']).exists()]
        if not pending:
            return self.alert('All selected Shorts have already been rendered')
        self.render_queue = pending
        self.next_render()

    def next_render(self):
        if self.render_queue:
            self.start_render(self.render_queue.pop(0), batch=True)

    def start_render(self, cid, batch=False):
        if not self.store:
            return self.alert('Open a project first')
        folder, settings, candidate = str(self.store.folder), self.settings.copy(), self.candidate(cid)
        def operation():
            local = ProjectStore(folder, recover=False)
            try:
                return engine.render(local, candidate, settings)
            finally:
                local.close()
        def done(result):
            self.store.update_short(cid, video=result[0], captions=result[1], status='READY')
            self.refresh_all()
        self.work(f'Render #{cid}', operation, done, cid)

    def create_thumbnail(self):
        cid = self.selected_id()
        if cid is None or not self.store:
            return self.alert('Select a rendered Short first')
        short = next(s for s in self.store.shorts() if s['candidate_id'] == cid)
        if not short['video'] or not Path(short['video']).is_file():
            return self.alert('Render this Short first')
        output = str(self.store.folder / 'thumbnails' / f'short_{cid:03}.jpg')
        self.work('Thumbnail', lambda: engine.thumbnail(short['video'], output, self.settings['ffmpeg']),
                  lambda result: self.store.update_short(cid, thumbnail=result), cid)

    def init_vlc(self):
        try:
            import vlc
            self.vlc = vlc.Instance('--no-video-title-show')
            self.player = self.vlc.media_player_new()
            window_id = int(self.preview.winId())
            if __import__('sys').platform == 'win32':
                self.player.set_hwnd(window_id)
            else:
                self.player.set_xwindow(window_id)
            self.timer = QTimer(self)
            self.timer.timeout.connect(lambda: self.seek.setValue(max(0, int(self.player.get_position()*1000))) if self.player and not self.seek.isSliderDown() else None)
            self.timer.start(500)
        except Exception:
            self.player = None
            self.status.setText('VLC unavailable; rendered videos can still be opened from disk')

    def load_video(self, path, at=0):
        if not self.player:
            return self.alert('Install VLC media player to enable embedded preview')
        self.player.set_media(self.vlc.media_new(str(path)))
        self.player.play()
        if at:
            QTimer.singleShot(400, lambda: self.player.set_time(round(at*1000)))

    def play_pause(self):
        if self.player:
            self.player.pause()

    def seek_video(self):
        if self.player:
            self.player.set_position(self.seek.value()/1000)

    def preview_source(self):
        if not self.store or self.selected_id() is None:
            return self.alert('Select a Short first')
        from app.core.timecode import parse_time
        self.load_video(self.store.project()['source'], parse_time(self.edit_start.text()))

    def refresh_meta_options(self):
        current = self.meta_select.currentData()
        self.meta_select.blockSignals(True); self.publish_select.blockSignals(True)
        self.meta_select.clear(); self.publish_select.clear()
        if self.store:
            for short in self.store.shorts():
                label = f'Short #{short["candidate_id"]} · {short["status"]}'
                self.meta_select.addItem(label, short['candidate_id'])
                self.publish_select.addItem(label, short['candidate_id'])
        index = self.meta_select.findData(current)
        self.meta_select.setCurrentIndex(max(0, index))
        self.meta_select.blockSignals(False); self.publish_select.blockSignals(False)
        self.load_metadata()

    def load_metadata(self):
        if not self.store or self.meta_select.currentData() is None:
            return
        cid = self.meta_select.currentData()
        short = next(s for s in self.store.shorts() if s['candidate_id'] == cid)
        data = json.loads(short['metadata']) if short['metadata'] else {}
        self.title_options.blockSignals(True)
        self.title_options.clear(); self.title_options.addItems(data.get('titles', []))
        self.title_options.blockSignals(False)
        self.title_edit.setText(data.get('selected_title', ''))
        self.description_edit.setPlainText(data.get('description', ''))
        self.tags_edit.setText(', '.join(data.get('tags', [])))
        self.hashtags_edit.setText(' '.join(data.get('hashtags', [])))
        self.category_edit.setText(data.get('category_id', '22'))

    def generate_metadata(self, force):
        cid = self.meta_select.currentData()
        if cid is None or not self.store:
            return self.alert('Select a Short first')
        short = next(s for s in self.store.shorts() if s['candidate_id'] == cid)
        if short['metadata'] and not force:
            return self.alert('Metadata already exists. Use Regenerate to replace manual edits.')
        if short['metadata'] and force and QMessageBox.question(self, 'Overwrite edits?', 'Replace all metadata edits with new AI output?') != QMessageBox.StandardButton.Yes:
            return
        candidate = self.candidate(cid); folder = str(self.store.folder); settings = self.settings.copy()
        text = Path(short['captions']).read_text(encoding='utf-8') if short['captions'] and Path(short['captions']).exists() else ''
        def operation():
            local = ProjectStore(folder, recover=False)
            try:
                return gemini.metadata(local, settings, candidate, text, force).model_dump()
            finally:
                local.close()
        def done(result):
            result['selected_title'] = result['titles'][0]
            self.store.update_short(cid, metadata=json.dumps(result))
            self.save_metadata_file(cid, result)
            self.load_metadata()
        self.work(f'Metadata #{cid}', operation, done, cid)

    def save_metadata_file(self, cid, data):
        path = self.store.folder / 'metadata' / f'short_{cid:03}.json'
        path.write_text(json.dumps(data, indent=2), encoding='utf-8')

    def save_metadata(self):
        cid = self.meta_select.currentData()
        if cid is None or not self.store:
            return self.alert('Select a Short first')
        short = next(s for s in self.store.shorts() if s['candidate_id'] == cid)
        data = json.loads(short['metadata']) if short['metadata'] else {'titles': [], 'keywords': []}
        data.update(selected_title=self.title_edit.text().strip(), description=self.description_edit.toPlainText(),
                    tags=[x.strip() for x in self.tags_edit.text().split(',') if x.strip()],
                    hashtags=[x.strip() for x in self.hashtags_edit.text().split() if x.strip()],
                    category_id=self.category_edit.text().strip())
        self.store.update_short(cid, metadata=json.dumps(data))
        self.save_metadata_file(cid, data)
        self.status.setText('Metadata saved')

    def connect_youtube(self):
        try:
            path = self.client_secrets.text().strip()
            if not Path(path).is_file():
                raise ValueError('Choose the Desktop OAuth client JSON downloaded from Google Cloud')
            self.flow, url, self.auth_state = youtube.start_auth(path)
            webbrowser.open(url)
            answer, ok = QInputDialog.getText(self, 'Complete YouTube authorization',
                'After approving access, the browser may show “site can’t be reached” (no server is used).\n'
                'Copy the FULL URL from its address bar and paste it here:')
            if ok and answer.strip():
                flow, state = self.flow, self.auth_state
                self.work('YouTube connect', lambda: youtube.channel_name(youtube.finish_auth(flow, answer.strip(), state)),
                          lambda name: self.channel.setText(f'Connected: {name}'))
        except Exception as exc:
            self.alert(str(exc))

    def disconnect_youtube(self):
        youtube.disconnect(); self.channel.setText('Not connected')

    def publish(self):
        if not self.store or self.publish_select.currentData() is None:
            return self.alert('Select a Short first')
        cid = self.publish_select.currentData()
        short = next(s for s in self.store.shorts() if s['candidate_id'] == cid)
        if not short['video'] or not Path(short['video']).is_file() or short['status'] not in ('READY', 'UPLOADED', 'SCHEDULED'):
            return self.alert('Render the Short before uploading')
        if not short['metadata']:
            return self.alert('Save title and metadata before uploading')
        data = json.loads(short['metadata'])
        try:
            metadata = Metadata.model_validate(data)
        except Exception as exc:
            return self.alert(f'Metadata is invalid: {exc}')
        title = data.get('selected_title', '')
        if not title or not metadata.description:
            return self.alert('Title and description are required')
        if short['youtube_id']:
            return self.alert('Already uploaded. YouTube video ID: ' + short['youtube_id'])
        schedule = self.publish_time.dateTime().toPython().astimezone() if self.scheduled.isChecked() else None
        visibility = self.visibility.currentText()
        choice = QMessageBox.question(self, 'Confirm YouTube upload',
            f'Upload Short #{cid} as {"scheduled private" if schedule else visibility}?\nTitle: {title}\nThis is a real YouTube API upload.')
        if choice != QMessageBox.StandardButton.Yes:
            return
        def operation():
            api = youtube.service()
            return youtube.upload(api, short['video'], metadata, title, visibility, schedule, short['thumbnail'])
        def done(video_id):
            self.store.update_short(cid, youtube_id=video_id, status='SCHEDULED' if schedule else 'UPLOADED')
            self.upload_status.setText(f'Uploaded Short #{cid}: https://youtube.com/watch?v={video_id}')
            self.refresh_all()
        self.work(f'Upload #{cid}', operation, done, cid)

    def save_settings(self):
        self.settings.update(analysis_model=self.analysis_model.text().strip(), metadata_model=self.metadata_model.text().strip(),
                             ffmpeg=self.ffmpeg_path.text().strip(), ffprobe=self.ffprobe_path.text().strip(),
                             projects_dir=self.projects_path.text().strip())
        preferences.save(self.settings)
        for name, widget in [('analysis', self.analysis_key), ('metadata', self.metadata_key)]:
            if widget.text().strip():
                credentials.set_secret(name, widget.text().strip()); widget.clear()
        self.status.setText('Settings saved; secrets stored in system credential vault')

    def test_credentials(self):
        models = [('analysis', self.analysis_model.text().strip()),
                  ('metadata', self.metadata_model.text().strip())]
        def operation():
            names = []
            for purpose, model in models:
                api = gemini.client_for(purpose)
                try:
                    next(iter(api.models.list(config={'page_size': 1})))
                    names.append(f'{purpose}: connected ({model})')
                finally:
                    api.close()
            return '\n'.join(names)
        self.work('Credential test', operation, lambda value: QMessageBox.information(self, 'Gemini connection', value))

    def clear_credentials(self):
        if QMessageBox.question(self, 'Remove credentials?', 'Delete both Gemini keys from the OS vault?') == QMessageBox.StandardButton.Yes:
            credentials.delete('analysis'); credentials.delete('metadata')
            self.status.setText('Gemini credentials deleted')

    def closeEvent(self, event):
        if self.busy:
            self.alert('Wait for the current operation to finish before closing. Job progress is saved.')
            event.ignore()
            return
        if self.player:
            self.player.stop()
        if self.store:
            self.store.close()
        super().closeEvent(event)
