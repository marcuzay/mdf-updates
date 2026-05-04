from typing import Dict
from pathlib import Path
import cv2
from functools import partial
import copy

from PySide6 import QtWidgets, QtGui
from PySide6 import QtCore

from app.ui.core.main_window import Ui_MainWindow
import app.ui.widgets.actions.common_actions as common_widget_actions
from app.ui.widgets.actions import card_actions
from app.ui.widgets.actions import layout_actions
from app.ui.widgets.actions import video_control_actions
from app.ui.widgets.actions import filter_actions
from app.ui.widgets.actions import save_load_actions
from app.ui.widgets.actions import list_view_actions
from app.ui.widgets.actions import graphics_view_actions

from app.processors.video_processor import VideoProcessor
from app.processors.models_processor import ModelsProcessor
from app.processors.lucy_client import LucyClient, AIORTC_AVAILABLE
from app.ui.widgets import widget_components
from app.ui.widgets.event_filters import GraphicsViewEventFilter, VideoSeekSliderEventFilter, videoSeekSliderLineEditEventFilter, ListWidgetEventFilter
from app.ui.widgets import ui_workers
from app.ui.widgets.common_layout_data import COMMON_LAYOUT_DATA
from app.ui.widgets.swapper_layout_data import SWAPPER_LAYOUT_DATA
from app.ui.widgets.settings_layout_data import SETTINGS_LAYOUT_DATA
from app.ui.widgets.face_editor_layout_data import FACE_EDITOR_LAYOUT_DATA
from app.helpers.miscellaneous import DFM_MODELS_DATA, ParametersDict
from app.helpers.typing_helper import FacesParametersTypes, ParametersTypes, ControlTypes, MarkerTypes

ParametersWidgetTypes = Dict[str, widget_components.ToggleButton|widget_components.SelectionBox|widget_components.ParameterDecimalSlider|widget_components.ParameterSlider|widget_components.ParameterText]

class MainWindow(QtWidgets.QMainWindow, Ui_MainWindow):
    placeholder_update_signal = QtCore.Signal(QtWidgets.QListWidget, bool)
    gpu_memory_update_signal = QtCore.Signal(int, int)
    model_loading_signal = QtCore.Signal()
    model_loaded_signal = QtCore.Signal()
    display_messagebox_signal = QtCore.Signal(str, str, QtWidgets.QWidget)
    fbs_status_signal = QtCore.Signal(str, str)  # message, color
    def initialize_variables(self):
        self.video_loader_worker: ui_workers.TargetMediaLoaderWorker|bool = False
        self.input_faces_loader_worker: ui_workers.InputFacesLoaderWorker|bool = False
        self.target_videos_filter_worker = ui_workers.FilterWorker(main_window=self, search_text='', filter_list='target_videos')
        self.input_faces_filter_worker = ui_workers.FilterWorker(main_window=self, search_text='', filter_list='input_faces')
        self.merged_embeddings_filter_worker = ui_workers.FilterWorker(main_window=self, search_text='', filter_list='merged_embeddings')
        self.video_processor = VideoProcessor(self)
        self.models_processor = ModelsProcessor(self)
        self.target_videos: Dict[int, widget_components.TargetMediaCardButton] = {} #Contains button objects of target videos (Set as list instead of single video to support batch processing in future)
        self.target_faces: Dict[int, widget_components.TargetFaceCardButton] = {} #Contains button objects of target faces
        self.input_faces: Dict[int, widget_components.InputFaceCardButton] = {} #Contains button objects of source faces (images)
        self.merged_embeddings: Dict[int, widget_components.EmbeddingCardButton] = {}
        self.cur_selected_target_face_button: widget_components.TargetFaceCardButton = False
        self.selected_video_button: widget_components.TargetMediaCardButton = False
        self.selected_target_face_id = False
        # '''
            # self.parameters dict have the following structure:
            # {
                # face_id (int): 
                # {
                    # parameter_name: parameter_value,
                    # ------
                # }
                # -----
            # }
        # '''
        self.parameters: FacesParametersTypes = {} 

        self.default_parameters: ParametersTypes = {}
        self.copied_parameters: ParametersTypes = {}
        self.current_widget_parameters: ParametersTypes = {}

        self.markers: MarkerTypes = {} #Video Markers (Contains parameters for each face)
        self.parameters_list = {}
        self.control: ControlTypes = {}
        self.parameter_widgets: ParametersWidgetTypes = {}
        self.loaded_embedding_filename: str = ''
        
        self.last_target_media_folder_path = ''
        self.last_input_media_folder_path = ''

        self.is_full_screen = False
        self.lucy_client: 'LucyClient | None' = None
        self.full_body_swap_ref_path: str = ''
        self.full_body_licence_token: str = ''
        self._fbs_connecting: bool = False
        self._fbs_key_line_edit = None
        self.dfm_models_data = DFM_MODELS_DATA
        # This flag is used to make sure new loaded media is properly fit into the graphics frame on the first load
        self.loading_new_media = False

        self.gpu_memory_update_signal.connect(partial(common_widget_actions.set_gpu_memory_progressbar_value, self))
        self.fbs_status_signal.connect(self._set_full_body_swap_status)
        self.placeholder_update_signal.connect(partial(common_widget_actions.update_placeholder_visibility, self))
        self.model_loading_signal.connect(partial(common_widget_actions.show_model_loading_dialog, self))
        self.model_loaded_signal.connect(partial(common_widget_actions.hide_model_loading_dialog, self))
        self.display_messagebox_signal.connect(partial(common_widget_actions.create_and_show_messagebox, self))
    def initialize_widgets(self):
        # Initialize QListWidget for target media
        self.targetVideosList.setFlow(QtWidgets.QListWidget.LeftToRight)
        self.targetVideosList.setWrapping(True)
        self.targetVideosList.setResizeMode(QtWidgets.QListWidget.Adjust)

        # Initialize QListWidget for face images
        self.inputFacesList.setFlow(QtWidgets.QListWidget.LeftToRight)
        self.inputFacesList.setWrapping(True)
        self.inputFacesList.setResizeMode(QtWidgets.QListWidget.Adjust)

        # Set up Menu Actions
        layout_actions.set_up_menu_actions(self)

        # Set up placeholder texts in ListWidgets (Target Videos and Input Faces)
        list_view_actions.set_up_list_widget_placeholder(self, self.targetVideosList)
        list_view_actions.set_up_list_widget_placeholder(self, self.inputFacesList)

        # Set up click to select and drop action on ListWidgets
        self.targetVideosList.setAcceptDrops(True)
        self.targetVideosList.viewport().setAcceptDrops(False)
        self.inputFacesList.setAcceptDrops(True)
        self.inputFacesList.viewport().setAcceptDrops(False)
        list_widget_event_filter = ListWidgetEventFilter(self, self)
        self.targetVideosList.installEventFilter(list_widget_event_filter)
        self.targetVideosList.viewport().installEventFilter(list_widget_event_filter)
        self.inputFacesList.installEventFilter(list_widget_event_filter)
        self.inputFacesList.viewport().installEventFilter(list_widget_event_filter)

        # Set up folder open buttons for Target and Input
        self.buttonTargetVideosPath.clicked.connect(partial(list_view_actions.select_target_medias, self, 'folder'))
        self.buttonInputFacesPath.clicked.connect(partial(list_view_actions.select_input_face_images, self, 'folder'))

        # Initialize graphics frame to view frames
        self.scene = QtWidgets.QGraphicsScene()
        self.graphicsViewFrame.setScene(self.scene)
        # Event filter to start playing when clicking on frame
        graphics_event_filter = GraphicsViewEventFilter(self, self.graphicsViewFrame,)
        self.graphicsViewFrame.installEventFilter(graphics_event_filter)

        video_control_actions.enable_zoom_and_pan(self.graphicsViewFrame)

        video_slider_event_filter = VideoSeekSliderEventFilter(self, self.videoSeekSlider)
        self.videoSeekSlider.installEventFilter(video_slider_event_filter)
        self.videoSeekSlider.valueChanged.connect(partial(video_control_actions.on_change_video_seek_slider, self))
        self.videoSeekSlider.sliderPressed.connect(partial(video_control_actions.on_slider_pressed, self))
        self.videoSeekSlider.sliderReleased.connect(partial(video_control_actions.on_slider_released, self))
        video_control_actions.set_up_video_seek_slider(self)
        self.frameAdvanceButton.clicked.connect(partial(video_control_actions.advance_video_slider_by_n_frames, self))
        self.frameRewindButton.clicked.connect(partial(video_control_actions.rewind_video_slider_by_n_frames, self))

        self.addMarkerButton.clicked.connect(partial(video_control_actions.add_video_slider_marker, self))
        self.removeMarkerButton.clicked.connect(partial(video_control_actions.remove_video_slider_marker, self))
        self.nextMarkerButton.clicked.connect(partial(video_control_actions.move_slider_to_next_nearest_marker, self))
        self.previousMarkerButton.clicked.connect(partial(video_control_actions.move_slider_to_previous_nearest_marker, self))

        self.viewFullScreenButton.clicked.connect(partial(video_control_actions.view_fullscreen, self))
        # Set up videoSeekLineEdit and add the event filter to handle changes
        video_control_actions.set_up_video_seek_line_edit(self)
        video_seek_line_edit_event_filter = videoSeekSliderLineEditEventFilter(self, self.videoSeekLineEdit)
        self.videoSeekLineEdit.installEventFilter(video_seek_line_edit_event_filter)

        # Connect the Play/Stop button to the play_video method
        self.buttonMediaPlay.toggled.connect(partial(video_control_actions.play_video, self))
        self.buttonMediaRecord.toggled.connect(partial(video_control_actions.record_video, self))
        # self.buttonMediaStop.clicked.connect(partial(self.video_processor.stop_processing))
        self.findTargetFacesButton.clicked.connect(partial(card_actions.find_target_faces, self))
        self.clearTargetFacesButton.clicked.connect(partial(card_actions.clear_target_faces, self))
        self.targetVideosSearchBox.textChanged.connect(partial(filter_actions.filter_target_videos, self))
        self.filterImagesCheckBox.clicked.connect(partial(filter_actions.filter_target_videos, self))
        self.filterVideosCheckBox.clicked.connect(partial(filter_actions.filter_target_videos, self))
        self.filterWebcamsCheckBox.clicked.connect(partial(filter_actions.filter_target_videos, self))
        self.filterWebcamsCheckBox.clicked.connect(partial(list_view_actions.load_target_webcams, self))

        self.inputFacesSearchBox.textChanged.connect(partial(filter_actions.filter_input_faces, self))
        self.inputEmbeddingsSearchBox.textChanged.connect(partial(filter_actions.filter_merged_embeddings, self))
        self.openEmbeddingButton.clicked.connect(partial(save_load_actions.open_embeddings_from_file, self))
        self.saveEmbeddingButton.clicked.connect(partial(save_load_actions.save_embeddings_to_file, self))
        self.saveEmbeddingAsButton.clicked.connect(partial(save_load_actions.save_embeddings_to_file, self, True))

        self.swapfacesButton.clicked.connect(partial(video_control_actions.process_swap_faces, self))
        self.editFacesButton.clicked.connect(partial(video_control_actions.process_edit_faces, self))

        # Body Swap is now in the options tab — no button here

        self.saveImageButton.clicked.connect(partial(video_control_actions.save_current_frame_to_file, self))
        self.clearMemoryButton.clicked.connect(partial(common_widget_actions.clear_gpu_memory, self))

        self.parametersPanelCheckBox.toggled.connect(partial(layout_actions.show_hide_parameters_panel, self))
        self.facesPanelCheckBox.toggled.connect(partial(layout_actions.show_hide_faces_panel, self))
        self.mediaPanelCheckBox.toggled.connect(partial(layout_actions.show_hide_input_target_media_panel, self))

        self.faceMaskCheckBox.clicked.connect(partial(video_control_actions.process_compare_checkboxes, self))
        self.faceCompareCheckBox.clicked.connect(partial(video_control_actions.process_compare_checkboxes, self))

        layout_actions.add_widgets_to_tab_layout(self, LAYOUT_DATA=COMMON_LAYOUT_DATA, layoutWidget=self.commonWidgetsLayout, data_type='parameter')
        layout_actions.add_widgets_to_tab_layout(self, LAYOUT_DATA=SWAPPER_LAYOUT_DATA, layoutWidget=self.swapWidgetsLayout, data_type='parameter')
        layout_actions.add_widgets_to_tab_layout(self, LAYOUT_DATA=SETTINGS_LAYOUT_DATA, layoutWidget=self.settingsWidgetsLayout, data_type='control')
        layout_actions.add_widgets_to_tab_layout(self, LAYOUT_DATA=FACE_EDITOR_LAYOUT_DATA, layoutWidget=self.faceEditorWidgetsLayout, data_type='parameter')

        # Set up output folder select button (It is inside the settings tab Widget)
        self.outputFolderButton.clicked.connect(partial(list_view_actions.select_output_media_folder, self))
        # Create a control value for OutputMediaFolder
        common_widget_actions.create_control(self, 'OutputMediaFolder', '')

        # Register Body Swap controls into self.control so frame_worker can read them
        common_widget_actions.create_control(self, 'BodySwapEnableToggle', False)
        common_widget_actions.create_control(self, 'BodySwapBlendSlider', 100)
        common_widget_actions.create_control(self, 'BodySwapMaskSoftnessSlider', 15)
        common_widget_actions.create_control(self, 'BodySwapWarpStrengthDecimalSlider', 1.0)
        common_widget_actions.create_control(self, 'BodySwapKeepFaceRegionToggle', True)

        # Wire up Full Body Swap tab extras (Browse, status label, key masking)
        self._setup_full_body_swap_tab_extras()

        # Initialize current_widget_parameters with default values
        self.current_widget_parameters = ParametersDict(copy.deepcopy(self.default_parameters), self.default_parameters)

        # Initialize the button states
        video_control_actions.reset_media_buttons(self)

        #Set GPU Memory Progressbar
        font = self.vramProgressBar.font()
        font.setBold(True)
        self.vramProgressBar.setFont(font)
        common_widget_actions.update_gpu_memory_progressbar(self)
        # Set face_swap_tab as the default focused tab
        self.tabWidget.setCurrentIndex(0)
        # widget_actions.add_groupbox_and_widgets_from_layout_map(self)
    def __init__(self):
        super(MainWindow, self).__init__()
        self.setupUi(self)
        self.initialize_variables()
        self.initialize_widgets()
        self.load_last_workspace()

    def resizeEvent(self, event: QtGui.QResizeEvent):
        # print("Called resizeEvent()")
        super().resizeEvent(event)
        # Call the method to fit the image to the view whenever the window resizes
        if self.scene.items():
            pixmap_item = self.scene.items()[0]
            # Set the scene rectangle to the bounding rectangle of the pixmap
            scene_rect = pixmap_item.boundingRect()
            self.graphicsViewFrame.setSceneRect(scene_rect)
            graphics_view_actions.fit_image_to_view(self, pixmap_item, scene_rect )

    def keyPressEvent(self, event):
        match event.key():
            case QtCore.Qt.Key_F11:
                video_control_actions.view_fullscreen(self)
            case QtCore.Qt.Key_V:
                video_control_actions.advance_video_slider_by_n_frames(self, n=1)
            case QtCore.Qt.Key_C:
                video_control_actions.rewind_video_slider_by_n_frames(self, n=1)
            case QtCore.Qt.Key_D:
                video_control_actions.advance_video_slider_by_n_frames(self, n=30)
            case QtCore.Qt.Key_A:
                video_control_actions.rewind_video_slider_by_n_frames(self, n=30)
            case QtCore.Qt.Key_Z:
                self.videoSeekSlider.setValue(0)
            case QtCore.Qt.Key_Space:
                self.buttonMediaPlay.click()
            case QtCore.Qt.Key_R:
                self.buttonMediaRecord.click()
            case QtCore.Qt.Key_F:
                if event.modifiers() & QtCore.Qt.KeyboardModifier.AltModifier:
                    video_control_actions.remove_video_slider_marker(self)
                else:
                    video_control_actions.add_video_slider_marker(self)
            case QtCore.Qt.Key_W:
                video_control_actions.move_slider_to_nearest_marker(self, 'next')
            case QtCore.Qt.Key_Q:
                video_control_actions.move_slider_to_nearest_marker(self, 'previous')
            case QtCore.Qt.Key_S:
                self.swapfacesButton.click()

    def closeEvent(self, event):
        print("MainWindow: closeEvent called.")

        self.video_processor.stop_processing()
        list_view_actions.clear_stop_loading_input_media(self)
        list_view_actions.clear_stop_loading_target_media(self)

        save_load_actions.save_current_workspace(self, 'last_workspace.json')
        if self.lucy_client:
            self.lucy_client.stop()
            self.lucy_client = None
        event.accept()

    def load_last_workspace(self):
        # Show the load workspace dialog if the file exists
        if Path('last_workspace.json').is_file():
            load_dialog = widget_components.LoadLastWorkspaceDialog(self)
            load_dialog.exec_()

    def save_last_workspace(self):
        pass

    def _on_body_swap_button_toggled(self, enabled):
        """Body Swap toggled — pre-load models and set reference on main thread."""
        self.models_processor.clear_body_swap_reference()
        if not enabled:
            return

        # Pre-load RVM and DWPose on the main thread to avoid deadlock in FrameWorker
        print("[BodySwap] Pre-loading body swap models...")
        try:
            if not self.models_processor.models.get('RVMBodySeg'):
                self.models_processor.models['RVMBodySeg'] = \
                    self.models_processor.load_model('RVMBodySeg')
            if not self.models_processor.models.get('DWPoseBody'):
                self.models_processor.models['DWPoseBody'] = \
                    self.models_processor.load_model('DWPoseBody')
            print("[BodySwap] Models loaded OK")
        except Exception as e:
            print(f"[BodySwap] Model load error: {e}")
            return

        # Set reference from currently checked input face
        import numpy as np
        for _, input_face in self.input_faces.items():
            if input_face.isChecked() and hasattr(input_face, 'media_path') and input_face.media_path:
                full_img = cv2.imread(input_face.media_path)
                if full_img is not None:
                    full_img = cv2.cvtColor(full_img, cv2.COLOR_BGR2RGB)
                    self.models_processor.set_body_swap_reference(full_img)
                    print(f"[BodySwap] Reference set from: {input_face.media_path}")
                    return
        print("[BodySwap] Enabled — select an input face to set reference, then hit Swap Faces")
    # ── Full Body Swap tab extras ─────────────────────────────────────────────

    def _setup_full_body_swap_tab_extras(self):
        """Add Save Key button and Open Studio button to the FBS section."""
        self._fbs_connecting = False

        # Mask the key field
        api_widget = self.parameter_widgets.get('FullBodySwapApiKeyText')
        if api_widget:
            api_widget.setEchoMode(QtWidgets.QLineEdit.Password)
            api_widget.setPlaceholderText("Paste licence key here...")

        # Save Key button — injected beside the key field
        self.fullBodySwapSaveKeyBtn = QtWidgets.QPushButton("Save Key")
        self.fullBodySwapSaveKeyBtn.setFixedHeight(24)
        self.fullBodySwapSaveKeyBtn.setFixedWidth(72)
        self.fullBodySwapSaveKeyBtn.setStyleSheet(
            "QPushButton{background:#1e3a1e;color:#4caf50;border:1px solid #2a5a2a;"
            "border-radius:3px;font-size:11px;font-weight:bold;}"
            "QPushButton:hover{background:#254a25;}"
        )
        self.fullBodySwapSaveKeyBtn.clicked.connect(self._on_full_body_swap_save_key)

        # Open Studio button — injected beside Save Key
        self.fullBodySwapProceedBtn = QtWidgets.QPushButton("Open Studio ↗")
        self.fullBodySwapProceedBtn.setFixedHeight(24)
        self.fullBodySwapProceedBtn.setStyleSheet(
            "QPushButton{background:#c8a96e;color:#0a0a0c;border:none;"
            "border-radius:3px;font-size:11px;font-weight:bold;padding:0 10px;}"
            "QPushButton:hover{background:#d8b97e;}"
        )
        self.fullBodySwapProceedBtn.clicked.connect(self._launch_fbs_browser)

        # Add both buttons beside the key field
        if api_widget and api_widget.parentWidget():
            parent_layout = api_widget.parentWidget().layout()
            if parent_layout:
                parent_layout.addWidget(self.fullBodySwapSaveKeyBtn)
                parent_layout.addWidget(self.fullBodySwapProceedBtn)

        # Status label — shows validation/connection messages
        self.fullBodySwapStatusLabel = QtWidgets.QLabel("")
        self.fullBodySwapStatusLabel.setAlignment(QtCore.Qt.AlignCenter)
        self.fullBodySwapStatusLabel.setWordWrap(True)
        self.fullBodySwapStatusLabel.setStyleSheet(
            "QLabel{color:#888;font-size:10px;padding:2px 8px;}"
        )
        self.commonWidgetsLayout.addWidget(self.fullBodySwapStatusLabel)

        # Saved label — brief confirmation after Save Key
        self.fullBodySwapKeySavedLabel = QtWidgets.QLabel("")
        self.fullBodySwapKeySavedLabel.setStyleSheet(
            "QLabel{color:#555;font-size:10px;margin:0 8px;}"
        )
        self.commonWidgetsLayout.addWidget(self.fullBodySwapKeySavedLabel)

        # Load saved key on startup
        self._fbs_load_saved_key()

        # Wire toggle
        toggle_widget = self.parameter_widgets.get('FullBodySwapEnableToggle')
        if toggle_widget:
            toggle_widget.toggled.connect(self._on_full_body_swap_toggled)
            # Hide reset button on toggle
            if hasattr(toggle_widget, 'reset_default_button'):
                toggle_widget.reset_default_button.setVisible(False)

        # Hide reset button on key field
        api_widget = self.parameter_widgets.get('FullBodySwapApiKeyText')
        if api_widget and hasattr(api_widget, 'reset_default_button'):
            api_widget.reset_default_button.setVisible(False)


    def _on_full_body_swap_save_key(self):
        """Save Key button clicked — ParameterText IS a QLineEdit, read directly."""
        key = ''
        api_widget = self.parameter_widgets.get('FullBodySwapApiKeyText')
        if api_widget:
            key = api_widget.text().strip()

        if not key:
            self.fullBodySwapKeySavedLabel.setText("⚠ Key field is empty.")
            self.fullBodySwapKeySavedLabel.setStyleSheet(
                "QLabel { color: #e05252; font-size: 10px; margin: 0 8px; }"
            )
            return

        self.full_body_licence_token = key
        # Persist to config file so it survives app restarts
        try:
            import json as _json
            from pathlib import Path as _Path
            cfg_file = str(_Path(__file__).resolve().parents[1] / "fbs_config.json")
            try:
                with open(cfg_file) as f:
                    cfg = _json.load(f)
            except Exception:
                cfg = {}
            cfg["licence_key"] = key
            with open(cfg_file, "w") as f:
                _json.dump(cfg, f, indent=2)
        except Exception:
            pass
        self.fullBodySwapKeySavedLabel.setText("✓ Key saved.")
        self.fullBodySwapKeySavedLabel.setStyleSheet(
            "QLabel { color: #4caf50; font-size: 10px; margin: 0 8px; }"
        )
        QtCore.QTimer.singleShot(3000, lambda: self.fullBodySwapKeySavedLabel.setText(""))

    def _fbs_load_saved_key(self):
        """Load previously saved licence key into field and memory."""
        try:
            import json as _json
            from pathlib import Path as _Path
            cfg_file = str(_Path(__file__).resolve().parents[1] / "fbs_config.json")
            with open(cfg_file) as f:
                cfg = _json.load(f)
            key = cfg.get("licence_key", "").strip()
            if key:
                self.full_body_licence_token = key
                api_widget = self.parameter_widgets.get('FullBodySwapApiKeyText')
                if api_widget:
                    api_widget.setText(key)
                lbl = getattr(self, 'fullBodySwapKeySavedLabel', None)
                if lbl:
                    lbl.setText("✓ Key loaded.")
                    lbl.setStyleSheet("QLabel { color: #4caf50; font-size: 10px; margin: 0 8px; }")
                    QtCore.QTimer.singleShot(3000, lambda: lbl.setText(""))
        except Exception:
            pass

    def _fbs_load_python_path(self):
        """Load saved Python path from config and update label."""
        try:
            import json
            from pathlib import Path as _Path
            cfg_file = str(_Path(__file__).resolve().parents[1] / "fbs_config.json")
            with open(cfg_file) as f:
                cfg = json.load(f)
            py_path = cfg.get("python_path", "")
            if py_path:
                display = py_path if len(py_path) <= 35 else "..." + py_path[-32:]

        except Exception:
            pass

    def _on_full_body_swap_browse_python(self):
        """Browse for system Python executable with decart installed."""
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select Python executable (with decart installed)",
            "C:/Users",
            "Python (python.exe);;All files (*.*)"
        )
        if not path:
            return

        # Verify decart is importable from this Python
        import subprocess, json
        from pathlib import Path as _Path
        self._set_full_body_swap_status("Verifying Python...", '#f0a500')
        QtWidgets.QApplication.processEvents()

        try:
            result = subprocess.run(
                [path, "-c", "import decart; print('ok')"],
                capture_output=True, text=True, timeout=8,
                env={**__import__("os").environ, "PYTHONPATH": ""}
            )
            if result.stdout.strip() == "ok":
                # Save to config
                cfg_file = str(_Path(__file__).resolve().parents[1] / "fbs_config.json")
                try:
                    with open(cfg_file) as f:
                        cfg = json.load(f)
                except Exception:
                    cfg = {}
                cfg["python_path"] = path
                with open(cfg_file, "w") as f:
                    json.dump(cfg, f, indent=2)

                # Update label
                display = path if len(path) <= 35 else "..." + path[-32:]

                self._set_full_body_swap_status("✓ Python set.", '#4caf50')
            else:
                self._set_full_body_swap_status(
                    "✗ Decart not installed in that Python. Run lucy_install.bat.", '#e05252'
                )
        except Exception as e:
            self._set_full_body_swap_status(f"✗ Verification failed: {e}", '#e05252')

    def _on_full_body_swap_stop(self):
        """Stop Session button — disconnects Full Body Swap and resumes webcam."""
        # _fbs_uncheck handles stopping lucy_client and resuming webcam
        self._fbs_uncheck()
        self._set_full_body_swap_status("Session stopped.", '#888888')

    def _on_full_body_swap_api_key_changed(self, text: str):
        """Clear saved token when user edits the key field."""
        self.full_body_licence_token = ''
        lbl = getattr(self, 'fullBodySwapKeySavedLabel', None)
        if lbl:
            lbl.setText("Key not saved yet — click Save Key.")
            lbl.setStyleSheet("QLabel { color: #666; font-size: 10px; margin: 0 8px; }")

    def _set_full_body_swap_status(self, message: str, color: str = '#888888'):
        """Update status label safely (can be called from any thread via invokeMethod)."""
        label = getattr(self, 'fullBodySwapStatusLabel', None)
        if label is None:
            return
        label.setStyleSheet(
            f"QLabel {{ color: {color}; font-size: 10px; padding: 1px 4px; }}"
        )
        label.setText(message)

    def _on_full_body_swap_browse_ref(self):
        """File dialog for picking a full-body reference image."""
        start_dir = self.last_input_media_folder_path or ''
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select Full-Body Reference Image",
            start_dir,
            "Images (*.jpg *.jpeg *.png *.bmp *.webp)"
        )
        if not path:
            return
        self.full_body_swap_ref_path = path
        filename = Path(path).name
        display  = filename if len(filename) <= 40 else filename[:37] + '...'
        lbl = getattr(self, 'fullBodySwapFileLabel', None)
        if lbl:
            lbl.setText("📁 " + display)
            lbl.setStyleSheet("QLabel { color: #aaa; font-size: 10px; margin: 0 8px; }")
            lbl.setToolTip(path)
        # Hot-swap reference if already connected
        if self.lucy_client and self.lucy_client.is_connected():
            self._set_full_body_swap_status("Updating reference...", '#f0a500')
            self.lucy_client.update_reference(path)
            self._set_full_body_swap_status("● Active", '#4caf50')

    def _fbs_uncheck(self):
        """Silently uncheck toggle, stop Lucy, resume webcam."""
        self._fbs_connecting = True
        self.control['FullBodySwapEnabled'] = False
        # Always stop Lucy when unchecking
        if self.lucy_client:
            self.lucy_client.stop()
            self.lucy_client = None


        w = self.parameter_widgets.get('FullBodySwapEnableToggle')
        if w:
            w.blockSignals(True)
            w.setChecked(False)
            w.blockSignals(False)
        self._fbs_connecting = False

    def _launch_fbs_browser(self):
        """Validate key then launch local server and open browser."""
        # Read from saved token — set by Save Key button
        key = getattr(self, 'full_body_licence_token', '').strip()
        # Fallback: read directly from the key field if not saved yet
        if not key:
            api_widget = self.parameter_widgets.get('FullBodySwapApiKeyText')
            if api_widget:
                key = api_widget.text().strip()
        if not key:
            self._set_full_body_swap_status("Enter and save your licence key first.", '#e05252')
            return

        self._set_full_body_swap_status("Validating licence...", '#f0a500')
        QtWidgets.QApplication.processEvents()

        # Validate key against AWS server
        try:
            import httpx
            from app.processors.lucy_client import LICENCE_SERVER_URL
            r = httpx.post(
                f"{LICENCE_SERVER_URL}/token",
                json={"licence_token": key},
                timeout=10,
            )
            if r.status_code == 402:
                self._set_full_body_swap_status("✗ No credits remaining.", '#e05252')
                return
            if r.status_code == 403:
                self._set_full_body_swap_status("✗ Invalid licence key.", '#e05252')
                return
            if r.status_code != 200:
                self._set_full_body_swap_status(f"✗ Server error ({r.status_code})", '#e05252')
                return
            data = r.json()
            client_token = data.get("client_token", "")
        except Exception as e:
            self._set_full_body_swap_status(f"✗ Cannot reach server: {e}", '#e05252')
            return

        # Launch local server in background
        import subprocess, sys
        from pathlib import Path
        server_script = str(Path(__file__).resolve().parents[2] / "fbs_server.py")
        from app.processors.lucy_client import LICENCE_SERVER_URL

        # Prefer system Python over MDF portable Python for browser launching
        import shutil
        python_exe = shutil.which('python') or shutil.which('python3') or sys.executable

        try:
            subprocess.Popen([
                python_exe,
                server_script,
                '--key',    key,
                '--token',  client_token,
                '--server', LICENCE_SERVER_URL,
                '--port',   '7860',
            ])
        except Exception as e:
            self._set_full_body_swap_status(f"✗ Could not launch browser UI: {e}", '#e05252')
            return

        self._set_full_body_swap_status("✓ Opening Full Body Swap...", '#4caf50')

    def _on_full_body_swap_toggled(self, enabled: bool):
        if self._fbs_connecting:
            return
        if not enabled:
            self._set_full_body_swap_status("", '#888888')
            return
        # Toggle ON — just show key field, user clicks Open Studio when ready
        self._set_full_body_swap_status("Enter key and click Open Studio ↗", '#f0a500')


    def _on_lucy_status_update(self, message: str, color: str = '#f0a500'):
        """Thread-safe status update — uses Qt signal to cross thread boundary."""
        try:
            self.fbs_status_signal.emit(message, color)
        except Exception:
            pass
