"""
Copyright (C) 2020 Abraham Goerge Smith

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
# pylint: disable=W0703 # Too broad an exception

import os
from os.path import splitext
import filecmp
import numpy as np
from skimage.io import imread
import nibabel as nib
import im_utils
from pathlib import Path


def penultimate_fname_with_segmentation(fnames, seg_dir):
    """
    Go through fnames and return the last one with a segmentation
    If no segmentations are found return None.
    """

    # if the segmentation folder contains directories
    # then we assume that each directory is for a class
    
    pen_fname = None
    last_fname = None
    seg_dirs = [os.path.join(seg_dir, d) for d
                in os.listdir(seg_dir) if os.path.isdir(os.path.join(seg_dir, d))]
    if not len(seg_dirs):
        seg_dirs = [seg_dir]
    
    seg_fnames = []

    for seg_dir in seg_dirs:
        seg_fnames += os.listdir(seg_dir)

    for fname in fnames:
       #=  fname.replace('.nrrd', '.nii.gz')
        if fname.endswith(
            ('.dcm', '.dicom', '.sr', '.DCM', '.DICOM', '.SR')):
            base_fname = fname.replace('.dcm', '.nii.gz')
        else:
            base_fname = fname.replace('.nrrd', '.nii.gz')
        if base_fname in seg_fnames:
            if last_fname is not None:
                pen_fname = last_fname
            last_fname = fname
    return pen_fname

def get_recursive_files(input_path):
    filenames = []
    path = Path(input_path)
    for p in path.rglob("*"):
        if os.path.isdir(str(p)) is False:
            filenames.append(p._str)
    return filenames


def get_annot_path(fname, train_dir, val_dir):
    """
    return path to annot if it is found in
    train or val annot dirs.
    Otherwise return None
    """
    if fname.endswith(
        ('.dcm', '.dicom', '.sr', '.DCM', '.DICOM', '.SR')):
      #  fname = fname.replace('.dcm', '.nii.gz')
        fname = fname.replace('.dcm', '.nii.gz').replace('.DCM', '.nii.gz')

    else:
        fname = fname.replace('.nrrd', '.nii.gz')
    train_path = os.path.join(train_dir, fname)
    val_path = os.path.join(val_dir, fname)
    if os.path.isfile(train_path):
        return train_path
    if os.path.isfile(val_path):
        return val_path
    return None


def get_new_annot_target_dir(train_annot_dir, val_annot_dir):
    """ Should we add new annotations to train or validation data? """
    #train_annots = os.listdir(train_annot_dir)
    #val_annots = os.listdir(val_annot_dir)
    train_annots = get_recursive_files(train_annot_dir)
    val_annots = get_recursive_files(val_annot_dir)
    
    
    train_annots = [f for f in train_annots if (splitext(f)[1] in ['.png', '.npy', '.gz'])]
    val_annots = [f for f in val_annots if (splitext(f)[1] in ['.png', '.npy', '.gz'])]

    num_train_annots = len(train_annots)
    num_val_annots = len(val_annots)
    
    if num_train_annots == 0 and num_val_annots == 0:
        # save in train directory first 
        return train_annot_dir
    # otherwise aim to get at least one annotation in train and validation.
    if num_train_annots == 0 and num_val_annots > 0:
        return train_annot_dir
    if num_train_annots > 0 and num_val_annots == 0:
        return val_annot_dir
    # then only add files to validation if there is at least 5x in train
    if num_train_annots >= (num_val_annots * 5):
        return val_annot_dir
    return train_annot_dir


def maybe_save_annotation_3d(image_data_shape, annot_data, annot_path,
                             fname, train_annot_dir, val_annot_dir, log):
    annot_data = annot_data.astype(np.byte)
    # if there is an existing annotation.
    if annot_path:
        existing_annot = im_utils.load_annot(annot_path,
                                             image_data_shape).astype(np.byte)
        # and the annot we are saving is different.
        if not np.array_equal(annot_data, existing_annot):
            # Then we must over-write the previously saved annoation.
            # The user is performing an edit, possibly correcting an error.
            # First save to project folder as temp file.
            # if the annotation is empty then delete the current annotation
            if np.sum(annot_data) == 0:
                log(f'maybe_save_annot,{fname},remove existing annotation as new data is empty')
                os.remove(annot_path)
            else:
                # otherwise update the data.
                log(f'maybe_save_annot,{fname},updated existing data')
                img = nib.Nifti1Image(annot_data, np.eye(4))
                img.to_filename(annot_path)
    else:
        # if there is not an existing annotation
        # and the annotation has some content
        if np.sum(annot_data) > 0:
            log(f'maybe_save_annot,{fname},create new')
            # then find the best place to put it based on current counts.
            annot_dir = get_new_annot_target_dir(train_annot_dir, val_annot_dir)
            # files starting with . are not used in training.
            tmp_annot_path = os.path.join(annot_dir, '.tmp_' + fname)
            dir_to_create = os.path.dirname(tmp_annot_path)
            Path(dir_to_create).mkdir(parents=True, exist_ok=True)
            annot_path = os.path.join(annot_dir, fname)
            dir_to_create = os.path.dirname(annot_path)
            Path(dir_to_create).mkdir(parents=True, exist_ok=True)
            
            img = nib.Nifti1Image(annot_data, np.eye(4))
            img.to_filename(tmp_annot_path) 
            # rename after finished saving to avoid error with loading partially saved annotation.
            os.rename(tmp_annot_path, annot_path)
        else:
            # if the annotation did not have content.
            # and there was not an existing annotation
            # then don't save anything, this data is useless for
            # training.
            log(f'maybe_save_annot,{fname},not saving as annotation data empty')

    return annot_path
