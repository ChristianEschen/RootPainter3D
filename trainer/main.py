"""
Human in the loop deep learning segmentation for biological images

Copyright (C) 2020 Abraham George Smith
Copyright (C) 2022 Abraham George Smith


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

from pathlib import Path
import os
import json
import argparse
from trainer import Trainer
from startup import startup_setup
import yaml
# Import all the necessary submodules

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--syncdir',
                        help=('location of directory where data is'
                              ' synced between the client and server'),
                        required=True)
    parser.add_argument('--config_file',
                        help="""
                'please specify path to yaml config file""", type=str,
                required=True,
                default=False)

    settings_path = os.path.join(Path.home(), 'root_painter_settings.json')
   
    settings = None
    
    args = parser.parse_args()
    arguments = vars(args)
    with open(args.config_file, 'r') as f:
        config = yaml.safe_load(f)
    arguments['sync_dir'] = settings_path
    arguments = {**config, **arguments}
    sync_dir = args.syncdir
    startup_setup(arguments, sync_dir=sync_dir)
    import torch
    print('cuda=?' ,torch.cuda.is_available())

    if settings and 'auto_complete' in settings and settings['auto_complete']:
        ip = settings['server_ip']
        port = settings['server_port']
        trainer = Trainer(sync_dir, config, ip, port)
    else:
        trainer = Trainer(sync_dir, config)

    trainer.main_loop()
