"""
Copyright (C) 2020 Abraham George Smith

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.
You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""

# pylint: disable=I1101,C0111,W0201,R0903,E0611, R0902, R0914

# too many statements
# pylint: disable=R0915

# catching too general exception
# pylint: disable=W0703

# too many public methods
# pylint: disable=R0904
# pylint: disable=E0401 # import error
# pylint: disable=C0103 # Method name "initUI" doesn't conform to snake_case naming style (invalid-name)

import sys
import os
from pathlib import PurePath
import json
from functools import partial
import copy
import traceback
from datetime import datetime
import time

import numpy as np
from skimage.io import use_plugin
from PyQt5 import QtWidgets
from PyQt5 import QtGui
from PyQt5 import QtCore
from PyQt5.QtCore import Qt

from view_state import ViewState
from about import AboutWindow, LicenseWindow, ShortcutWindow
from create_project import CreateProjectWidget
from im_viewer import ImViewer, ImViewerWindow
from nav import NavWidget
from file_utils import last_fname_with_segmentation
from file_utils import get_annot_path
from file_utils import maybe_save_annotation_3d
from instructions import send_instruction
from contrast_slider import ContrastSlider
import im_utils
import menus

use_plugin("pil")

class RootPainter(QtWidgets.QMainWindow):

    closed = QtCore.pyqtSignal()

    def __init__(self, sync_dir, contrast_presets):
        super().__init__()
        self.sync_dir = sync_dir
        self.instruction_dir = sync_dir / 'instructions'
        self.send_instruction = partial(send_instruction,
                                        instruction_dir=self.instruction_dir,
                                        sync_dir=sync_dir)
        self.contrast_presets = contrast_presets
        self.view_state = ViewState.BOUNDING_BOX
        self.tracking = False
        self.seg_mtime = None
        self.im_width = None
        self.im_height = None
        self.annot_data = None
        self.seg_data = None
        self.box = {'x':20, 'y':20, 'z': 20, 'width': 50, 'height': 50,
                    'depth': 20, 'visible': False}
        # for patch, useful for bounding box.
        self.input_shape = (52, 228, 228)
        self.output_shape = (18, 194, 194)

        self.lines_to_log = []
        self.log_debounce = QtCore.QTimer()
        self.log_debounce.setInterval(500)
        self.log_debounce.setSingleShot(True)
        self.log_debounce.timeout.connect(self.log_debounced)

        self.initUI()

    def initUI(self):
        if len(sys.argv) < 2:
            self.init_missing_project_ui()
            return

        fname = sys.argv[1]
        if os.path.splitext(fname)[1] == '.seg_proj':
            proj_file_path = os.path.abspath(sys.argv[1])
            self.open_project(proj_file_path)
        else:
            # only warn if -psn not in the args. -psn is in the args when
            # user opened app in a normal way by clicking on the Application icon.
            if not '-psn' in sys.argv[1]:
                QtWidgets.QMessageBox.about(self, 'Error', sys.argv[1] +
                                            ' is not a valid '
                                            'segmentation project (.seg_proj) file')
            self.init_missing_project_ui()

    def open_project(self, proj_file_path):
        # extract json
        with open(proj_file_path, 'r') as json_file:
            settings = json.load(json_file)
            self.dataset_dir = self.sync_dir / 'datasets' / PurePath(settings['dataset'])

            self.proj_location = self.sync_dir / PurePath(settings['location'])
            self.image_fnames = settings['file_names']
            self.seg_dir = self.proj_location / 'segmentations'
            self.log_dir = self.proj_location / 'logs'
            self.train_annot_dir = self.proj_location / 'annotations' / 'train'
            self.val_annot_dir = self.proj_location / 'annotations' / 'val'
            self.model_dir = self.proj_location / 'models'
            self.message_dir = self.proj_location / 'messages'
            self.classes = settings['classes']

            # If there are any segmentations which have already been saved
            # then go through the segmentations in the order specified
            # by self.image_fnames
            # and set fname (current image) to be the last image with a segmentation
            last_with_seg = last_fname_with_segmentation(self.image_fnames,
                                                         self.seg_dir)
            if last_with_seg:
                fname = last_with_seg
            else:
                fname = self.image_fnames[0]

            # set first image from project to be current image
            self.image_path = os.path.join(self.dataset_dir, fname)
            self.update_window_title()
            self.seg_path = os.path.join(self.seg_dir, fname)
            self.annot_path = get_annot_path(fname, self.train_annot_dir,
                                             self.val_annot_dir)
            self.init_active_project_ui()

            if self.view_state == ViewState.ANNOTATING:
                self.track_changes()

    def log_debounced(self):
        """ write to log file only so often to avoid lag """
        with open(os.path.join(self.log_dir, 'client.csv'), 'a+') as log_file:
            while len(self.lines_to_log):
                line = self.lines_to_log[0]
                log_file.write(line)
                self.lines_to_log = self.lines_to_log[1:]

    def log(self, message):
        self.lines_to_log.append(f"{datetime.now()}|{time.time()}|{message}\n")
        self.log_debounce.start() # write after 1 second

    def update_file(self, fpath):
        """ Invoked when the file to view has been changed by the user.
            Show image file and it's associated annotation and segmentation """
        # save annotation for current file before changing to new file.

        self.log(f'update_file_start,fname:{os.path.basename(fpath)},view_state:{self.view_state}')
        if self.view_state == ViewState.ANNOTATING:
            self.save_annotation()

        fname = os.path.basename(fpath)
        self.image_path = os.path.join(self.dataset_dir, fname)
        seg_fname = os.path.splitext(fname)[0] + '.nii.gz'
       
        # Try to find the annotation and segmentation for this particular image.
        bounded_im_dir = os.path.join(self.proj_location, 'bounded_images')
        bounded_fnames = os.listdir(bounded_im_dir)
        bounded_fnames = [f for f in bounded_fnames if '.nii.gz' in f]
        self.bounded_fname = None
        # reset box information
        self.box = {'x':20, 'y':20, 'z': 20, 'width': 50, 'height': 50, 'depth': 20, 'visible': False}

        for f in bounded_fnames:
            original_name = '_'.join(f.split('_')[0:-16]) + '.nii.gz'
            if original_name == fname:
                self.bounded_fname = f    
                break

        self.seg_path = None
        self.annot_path = None
        self.view_state = ViewState.BOUNDING_BOX

        for v in self.viewers:
            if v.scene.bounding_box:
                v.scene.removeItem(v.scene.bounding_box)
                v.scene.bounding_box = None

        if self.bounded_fname:
            self.seg_path = os.path.join(self.seg_dir, self.bounded_fname)
            self.annot_path = get_annot_path(self.bounded_fname,
                                             self.train_annot_dir,
                                             self.val_annot_dir)

        self.img_data = im_utils.load_image(self.image_path)
        fname = os.path.basename(self.image_path)

        if self.annot_path and os.path.isfile(self.annot_path):

            self.annot_data = im_utils.load_annot(self.annot_path, self.img_data.shape)
        else:
            # otherwise create empty annotation array
            # if we are working with 3D data (npy file) and the
            # file hasn't been found then create an empty array to be
            # used for storing the annotation information.
            # channel for bg (0) and fg (1)
            self.annot_data = np.zeros([2] + list(self.img_data.shape))

        if self.seg_path and os.path.isfile(self.seg_path):
            self.log(f'load_seg,fname:{os.path.basename(self.seg_path)}')
            self.seg_data, self.seg_props = im_utils.load_seg(self.seg_path, self.img_data)
            self.view_state = ViewState.ANNOTATING
            self.update_segmentation()
        else:
            # it should come later
            self.seg_data = None
           
        for v in self.viewers:
            v.update_image()
            v.update_cursor()
            # hide the segmentation if we don't have it
            if self.seg_data is None and v.seg_visible:
                # show seg in order to show the loading message
                v.show_hide_seg()

        self.contrast_slider.update_range(self.img_data)
        self.update_window_title()
        self.log(f'update_file_end,fname:{os.path.basename(fpath)},view_state:{self.view_state}')

    def update_segmentation(self):
        if self.seg_path and os.path.isfile(self.seg_path):
            self.seg_mtime = os.path.getmtime(self.seg_path)
            self.nav.next_image_button.setText('Save && Next >')
            self.nav.next_image_button.setEnabled(True)
        else:
            self.seg_mtime = None
            self.nav.next_image_button.setEnabled(False)
            self.nav.next_image_button.setText('Loading Segmentation...')

    def set_seg_loading(self):
        """ Transition from drawing bounding box to loading segmentation """
        self.seg_mtime = None
        self.nav.next_image_button.setEnabled(False)
        self.nav.next_image_button.setText('Loading Segmentation...')
        self.view_state = ViewState.LOADING_SEG
        self.box['visible'] = False 
        for v in self.viewers:
            # remove bounding box for all viewers.
            v.scene.removeItem(v.scene.bounding_box)
            v.scene.bounding_box = None
            v.scene.last_x = None
            v.scene.last_y = None
            if not v.seg_visible:
                # show seg in order to show the loading message
                v.show_hide_seg()


    def show_open_project_widget(self):
        options = QtWidgets.QFileDialog.Options()
        default_loc = self.sync_dir / 'projects'
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Load project file",
            str(default_loc),
            "Segmentation project file (*.seg_proj)",
            options=options)

        if file_path:
            self.open_project(file_path)

    def show_create_project_widget(self):
        print("Open the create project widget..")
        self.create_project_widget = CreateProjectWidget(self.sync_dir)
        self.create_project_widget.show()
        self.create_project_widget.created.connect(self.open_project)

    def init_missing_project_ui(self):
        ## Create project menu
        # project has not yet been selected or created
        # need to open minimal interface which allows users
        # to open or create a project.

        menu_bar = self.menuBar()
        self.menu_bar = menu_bar
        self.menu_bar.clear()
        self.project_menu = menu_bar.addMenu("Project")

        # Open project
        self.open_project_action = QtWidgets.QAction(QtGui.QIcon(""), "Open project", self)
        self.open_project_action.setShortcut("Ctrl+O")

        self.project_menu.addAction(self.open_project_action)
        self.open_project_action.triggered.connect(self.show_open_project_widget)

        # Create project
        self.create_project_action = QtWidgets.QAction(QtGui.QIcon(""), "Create project", self)
        self.create_project_action.setShortcut("Ctrl+C")
        self.project_menu.addAction(self.create_project_action)
        self.create_project_action.triggered.connect(self.show_create_project_widget)

        menus.add_help_menu(self, menu_bar)

        # Add project btns to open window (so it shows something useful)
        project_btn_widget = QtWidgets.QWidget()
        self.setCentralWidget(project_btn_widget)

        layout = QtWidgets.QHBoxLayout()
        project_btn_widget.setLayout(layout)
        open_project_btn = QtWidgets.QPushButton('Open existing project')
        open_project_btn.clicked.connect(self.show_open_project_widget)
        layout.addWidget(open_project_btn)

        create_project_btn = QtWidgets.QPushButton('Create new project')
        create_project_btn.clicked.connect(self.show_create_project_widget)
        layout.addWidget(create_project_btn)

        self.setWindowTitle("RootPainter3D - Not approved for clinical use.")
        self.resize(layout.sizeHint())


    def show_license_window(self):
        self.license_window = LicenseWindow()
        self.license_window.show()

    def show_about_window(self):
        self.about_window = AboutWindow()
        self.about_window.show()

    def show_shortcut_window(self):
        self.shortcut_window = ShortcutWindow()
        self.shortcut_window.show()

    def update_window_title(self):
        proj_dirname = os.path.basename(self.proj_location)
        self.setWindowTitle(f"RootPainter3D {proj_dirname}"
                            f" {os.path.basename(self.image_path)}"
                            " - Not approved for clinical use")

    def closeEvent(self, event):
        if hasattr(self, 'contrast_slider'):
            self.contrast_slider.close()
        if hasattr(self, 'sagittal_viewer'):
            self.sagittal_viewer.close()
        if hasattr(self, 'coronal_viewer'):
            self.coronal_viewer.close()

    def update_viewer_image_slice(self):
        for v in self.viewers:
            if v.isVisible():
                v.update_image_slice()

    def update_viewer_annot_slice(self):
        for v in self.viewers:
            if v.isVisible():
                v.update_annot_slice()

    def update_viewer_outline(self):
        for v in self.viewers:
            if v.isVisible():
                v.update_outline()

    def before_nav_change(self):
        """
        I'm trying to make sure the user doesn't forget to remove
        disconnected regions.
        """
        # return False to block nav change
        num_regions = im_utils.get_num_regions(self.seg_data,
                                               self.annot_data)
        if num_regions == 1:
            return True
        button_reply = QtWidgets.QMessageBox.question(self,
            'Confirm',
            f"There are {num_regions} regions in this image. "
            "Are you sure you want to proceed to the next image?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, 
            QtWidgets.QMessageBox.No)

        if button_reply == QtWidgets.QMessageBox.Yes:
            return True
        else:
            return False

    def init_active_project_ui(self):
        # container for both nav and im_viewer.
        container = QtWidgets.QWidget()
        container_layout = QtWidgets.QVBoxLayout()
        container_layout.setContentsMargins(0, 0, 0, 0)
        self.container = container
        self.container_layout = container_layout
        container.setLayout(container_layout)
        self.setCentralWidget(container)

        self.viewers_container = QtWidgets.QWidget()
        self.viewers_layout = QtWidgets.QHBoxLayout()
        self.viewers_container.setLayout(self.viewers_layout)

        self.axial_viewer = ImViewer(self, 'axial')
        self.sagittal_viewer = ImViewerWindow(self, 'sagittal')
        self.sagittal_viewer.show()
        
        #self.coronal_viewer = ImViewerWindow(self, 'coronal')
        self.viewers = [self.axial_viewer, self.sagittal_viewer] #, self.coronal_viewer]
        self.viewers_layout.addWidget(self.axial_viewer)

        container_layout.addWidget(self.viewers_container)
        self.contrast_slider = ContrastSlider(self.contrast_presets)
        self.contrast_slider.changed.connect(self.update_viewer_image_slice)


        self.nav = NavWidget(self.image_fnames, self.before_nav_change)

        # bottom bar right
        bottom_bar_r = QtWidgets.QWidget()
        bottom_bar_r_layout = QtWidgets.QVBoxLayout()
        bottom_bar_r.setLayout(bottom_bar_r_layout)
        self.axial_viewer.bottom_bar_layout.addWidget(bottom_bar_r)
        
        # Nav
        self.nav.file_change.connect(self.update_file)
        self.nav.image_path = self.image_path
        self.nav.update_nav_label()
        # info label
        info_container = QtWidgets.QWidget()
        info_container_layout = QtWidgets.QHBoxLayout()
        info_container_layout.setAlignment(Qt.AlignCenter)
        info_label = QtWidgets.QLabel()
        info_label.setText("")
        info_container_layout.addWidget(info_label)
        # left, top, right, bottom
        info_container_layout.setContentsMargins(0, 0, 0, 0)
        info_container.setLayout(info_container_layout)
        self.info_label = info_label
        # add nav and info label to the axial viewer.
        bottom_bar_r_layout.addWidget(info_container)
        bottom_bar_r_layout.addWidget(self.nav)

        self.add_menu()
        self.resize(container_layout.sizeHint())
        self.axial_viewer.update_cursor()
        self.update_file(self.image_path)

        def view_fix():
            """ started as hack for linux bug.
                now used for setting defaults """
            self.axial_viewer.update_cursor()
            # These are causing issues on windows so commented out until I find a better solution
            # self.set_to_left_half_screen()
            # self.sagittal_viewer.set_to_right_half_screen()
            self.set_default_view_size()

        QtCore.QTimer.singleShot(100, view_fix)


    def set_default_view_size(self):
        # sensible defaults for CT scans
        self.axial_viewer.graphics_view.zoom = 2.4
        self.sagittal_viewer.graphics_view.zoom = 2.0
        self.axial_viewer.graphics_view.update_zoom()
        self.sagittal_viewer.graphics_view.update_zoom()

    def set_to_left_half_screen(self): 
        screen_shape = QtWidgets.QDesktopWidget().screenGeometry()
        w = screen_shape.width() // 2
        h = screen_shape.height()
        x = 0
        y = 0
        self.setGeometry(x, y, w, h)

    def track_changes(self):
        if self.tracking:
            return
        print('Starting watch for changes')
        self.tracking = True
        def check():
            # check for any messages
            messages = os.listdir(str(self.message_dir))
            for m in messages:
                if hasattr(self, 'info_label'):
                    self.info_label.setText(m)
                try:
                    # Added try catch because this error happened (very rarely)
                    # PermissionError: [WinError 32]
                    # The process cannot access the file because it is
                    # being used by another process
                    os.remove(os.path.join(self.message_dir, m))
                except Exception as e:
                    print('Caught exception when trying to detele msg', e)
            # if a segmentation exists (on disk)
            if hasattr(self, 'seg_path') and self.seg_path and os.path.isfile(self.seg_path):
                try:
                    # seg mtime is not actually used any more.
                    new_mtime = os.path.getmtime(self.seg_path)
                    # seg_mtime is None before the seg is loaded.
                    if self.seg_mtime is None or new_mtime > self.seg_mtime:
                        self.log(f'load_seg,fname:{os.path.basename(self.seg_path)}')
                        self.seg_data, self.seg_props = im_utils.load_seg(self.seg_path,
                                                                          self.img_data)
                        self.axial_viewer.update_seg_slice()
                        # Change to annotating state.                        
                        self.view_state = ViewState.ANNOTATING
                        for v in self.viewers:
                            v.update_cursor()
                            # for some reason cursor doesn't update straight away sometimes.
                            # trigger again half a second later to make sure correct cursor is shown.
                            QtCore.QTimer.singleShot(500, v.update_cursor)
                            if v.isVisible():
                                v.update_seg_slice()

                        self.seg_mtime = new_mtime
                        self.nav.next_image_button.setText('Save && Next >')
                        self.nav.next_image_button.setEnabled(True)
                except Exception as e:
                    print(f'Exception loading segmentation,{e},{traceback.format_exc()}')
                    # sometimes problems reading file.
                    # don't worry about this exception
            else:
                pass
            QtCore.QTimer.singleShot(500, check)
        QtCore.QTimer.singleShot(500, check)

    def close_project_window(self):
        self.close()
        self.closed.emit()

    def add_menu(self):
        menu_bar = self.menuBar()
        menu_bar.clear()

        self.project_menu = menu_bar.addMenu("Project")
        # Open project
        self.close_project_action = QtWidgets.QAction(QtGui.QIcon(""), "Close project", self)
        self.project_menu.addAction(self.close_project_action)
        self.close_project_action.triggered.connect(self.close_project_window)
        menus.add_edit_menu(self, self.axial_viewer, menu_bar)

        menus.add_bounding_box_menu(self, self.axial_viewer, menu_bar)

        #options_menu = menu_bar.addMenu("Options")

        self.menu_bar = menu_bar

        # add brushes menu for axial slice navigation
        menus.add_brush_menu(self.classes, self.axial_viewer, self.menu_bar)

        # add view menu for axial slice navigation.
        view_menu = menus.add_view_menu(self, self.axial_viewer, self.menu_bar)
        self.add_contrast_setting_options(view_menu)

        menus.add_network_menu(self, self.menu_bar)
        menus.add_windows_menu(self)
        menus.add_help_menu(self, self.menu_bar)

    def add_contrast_setting_options(self, view_menu):
        preset_count = 0
        for preset in self.contrast_presets:
            def add_preset_option(new_preset, preset_count):
                preset = new_preset
                preset_btn = QtWidgets.QAction(QtGui.QIcon('missing.png'),
                                               f'{preset} contrast settings', self)
                preset_btn.setShortcut(QtGui.QKeySequence(f"Alt+{preset_count}"))
                preset_btn.setStatusTip(f'Use {preset} contrast settings')
                def on_select():
                    self.contrast_slider.preset_selected(preset)
                preset_btn.triggered.connect(on_select)
                view_menu.addAction(preset_btn)
            preset_count += 1
            add_preset_option(preset, preset_count)

    def stop_training(self):
        self.info_label.setText("Stopping training...")
        content = {"message_dir": self.message_dir}
        self.send_instruction('stop_training', content)

    def start_training(self):
        self.info_label.setText("Starting training...")
        # 3D just uses the name of the first class
        classes = self.classes
        content = {
            "model_dir": self.model_dir,
            "dataset_dir": os.path.join(self.proj_location, 'bounded_images'),
            "train_annot_dir": self.train_annot_dir,
            "val_annot_dir": self.val_annot_dir,
            "seg_dir": self.seg_dir,
            "log_dir": self.log_dir,
            "message_dir": self.message_dir,
            "classes": ['Foreground']
        }
        self.send_instruction('start_training', content)

    def save_annotation(self):
        if self.annot_data is not None:
            for v in self.viewers:
                v.store_annot_slice()
            fname = os.path.basename(self.seg_path)
            self.annot_path = maybe_save_annotation_3d(self.img_data.shape,
                                                       self.annot_data,
                                                       self.annot_path,
                                                       fname,
                                                       self.train_annot_dir,
                                                       self.val_annot_dir,
                                                       self.seg_props, self.log)

            if self.annot_path:
                # start training when an annotation exists
                self.start_training()