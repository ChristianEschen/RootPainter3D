"""
Check for required folders on startup and create if required

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


import os
from pathlib import Path
import json
import copy

def startup_setup(arguments, sync_dir=None):
    """
    1. arguments contains dict with setting.
    2. If the sync dir doesn't exist then create it.
    3. If the required sync dir subfolders don't exist then create them.
    """

    # Get sync dir from settings file.
    if os.path.isfile(arguments['sync_dir']):
        sync_dir = Path(json.load(open(arguments['sync_dir'], 'r'))['sync_dir'])
        sync_dir_abs = os.path.abspath(sync_dir)
    else:
        # only create config file if user did not specify the sync dir on the command line
        if not sync_dir:
            # Or if the settings file doesn't exist get a sync_dir
            # from the user and save it to a settings file.
            sync_dir = input("Please specify RootPainter3D sync directory")
        sync_dir = os.path.expanduser(sync_dir)
        sync_dir_abs = os.path.abspath(sync_dir)
        with open(arguments['sync_dir'], 'w') as json_file:
            del arguments['sync_dir']
            arguments['contrast_presets'] = {'Mediastinal': [-125, 250, 100]}
            content = {**arguments, **{"sync_dir": sync_dir_abs}}
            print(f'Writing {sync_dir_abs} to {sync_dir}')
            json.dump(content, json_file, indent=4)

    # If sync_dir doesn't exist then create it.
    if not os.path.isdir(sync_dir_abs):
        print('Creating', sync_dir_abs)
        os.mkdir(sync_dir_abs)

    # RootPainter requires some folders to run. If they aren't already
    # in the sync_dir then create them
    required_subfolders = ['projects', 'datasets', 'instructions']
    for subfolder in required_subfolders:
        subfolder_path = os.path.join(sync_dir_abs, subfolder)
        if not os.path.isdir(subfolder_path):
            print('Creating', subfolder_path)
            os.mkdir(subfolder_path)
