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

# pylint: disable=C0111, W0511
# pylint: disable=E0401 # import error

import os
import warnings
import glob
import sys

import numpy as np
from skimage import color
from skimage.io import imread, imsave
from skimage import img_as_ubyte
from skimage.transform import resize
from skimage.measure import label
from skimage.morphology import binary_dilation, remove_small_holes
from PIL import Image
from PyQt5 import QtGui
import qimage2ndarray
import nibabel as nib
from scipy.ndimage import binary_fill_holes
from skimage.segmentation import flood
import nrrd
import SimpleITK as sitk
from pathlib import Path
from scipy.ndimage import zoom
import pandas as pd
import psycopg2

def is_image(fname):
    extensions = {
        '.npy', 'gz', 'nrrd', '.dicom',
        '.dcm', '.DCM', '.DICOM', '.sr', '.SR'
        }
    return any(fname.lower().endswith(ext) for ext in extensions)


def getDataFromDatabase(sql_config):
    connection = psycopg2.connect(
        host=sql_config['host'],
        database=sql_config['database'],
        user=sql_config['username'],
        password=sql_config['password'])
    sql = sql_config['query'].replace(
        "?table_name", "\"" + sql_config['table_name'] + "\"")
    sql = sql.replace(
        "?schema_name", "\"" + sql_config['schema_name'] + "\"")
    sql = sql.replace(
        "??", "\"")
    df = pd.read_sql_query(sql, connection)
    if len(df) == 0:
        print('The requested query does not have any data!')
    connection.close()

    return df['DcmPathFlatten'].tolist()


def get_recursive_files(input_path):
    filenames = []
    path = Path(input_path)
    for p in path.rglob("*"):
        if os.path.isdir(str(p)) is False:
            filenames.append(p._str)
    return filenames



def resizeVolume(img, output_size):
    factors = (output_size[0]/img.shape[1],
                output_size[1]/img.shape[2])

    return zoom(img, (1, factors[0], factors[1]))

def pad_image(image, pad_size):
    shap = image.shape
    if shap[0] < pad_size:
        to_pad = pad_size - shap[0]
        if (to_pad % 2) == 0:
            image = np.pad(image, ((to_pad/2,to_pad/2), (0,0), (0, 0)), 'constant')
        else:
            image = np.pad(image, ((int(to_pad/2)+1,int(to_pad/2)), (0,0), (0, 0)), 'constant')
    # else:
    #     image = image[0:pad_size, :, :]
    return image


def load_image(image_path):
    if image_path.endswith('.npy'):
        return np.load(image_path, mmap_mode='c')
    elif image_path.endswith('.nii.gz'):
        image = nib.load(image_path)
        image = np.array(image.dataobj)
    elif image_path.endswith('.nrrd'):
        image, header = nrrd.read(image_path)
    elif image_path.endswith(('.dicom', '.DICOM', '.dcm',
                              '.DCM',  '.sr', '.SR')):
        image = sitk.ReadImage(image_path)
        image = sitk.GetArrayFromImage(image)
       # print('image shape loaded (dcm)', image.shape)
    else:
        raise Exception(f"Unhandled file ending {image_path}")
    image = image.astype(np.int)
    return image

def load_annot(annot_path, img_data_shape):
    if annot_path.endswith(('.dicom', '.DICOM', '.dcm',
                            '.DCM',  '.sr', '.SR')):
        image = sitk.ReadImage(annot_path)
        annot_data = sitk.GetArrayFromImage(image)
        annot_data = np.array(annot_data, dtype=bool)
        print('annot data shape', annot_data.shape)
        
    else:
        annot_image = nib.load(annot_path)
        annot_data = np.array(annot_image.dataobj, dtype=bool)
  #      annot_data = np.rot90(annot_data, k=3)
      # print('annot data shape before permutation', annot_data.shape)
       # annot_data = np.moveaxis(annot_data, -1, 0) # depth moved to beginning
        print('annot data shape permutation', annot_data.shape)

    return annot_data


def load_seg(seg_path):
    if seg_path.endswith(('.dicom', '.DICOM', '.dcm',
                            '.DCM',  '.sr', '.SR')):
        image = sitk.ReadImage(seg_path)
        seg_data = sitk.GetArrayFromImage(image)
       # seg_data = resizeVolume(seg_data, (128, 128))
        seg_data = np.array(seg_data, dtype=bool)
        print('seg data shape', seg_data.shape)

        
    else:
        seg_image = nib.load(seg_path)
        seg_data = np.array(seg_image.dataobj, dtype=bool)
    #    seg_data = np.rot90(seg_data, k=3)
        print('seg_data shape before per', seg_data.shape)
       # seg_data = np.moveaxis(seg_data, -1, 0) # depth moved to beginning
        print('seg_data shape', seg_data.shape)
    # This issue may be related to file system issues.
    assert  len(seg_data.shape) == 3, f"seg shape is {seg_data.shape} for {seg_path}"
    return seg_data


def norm_slice(img, min_v, max_v, brightness_percent):
    if img.dtype != np.float32:
        img = img.astype(np.float32)
    bright_v = (brightness_percent / 100)
    img[img < min_v] = min_v
    img[img > max_v] = max_v
    img -= min_v
    img /= (max_v - min_v)
    img *= bright_v
    img[img > 1] = 1.0
    img *= 255
    return img


def annot_slice_to_pixmap(slice_np):
    """ convert slice from the numpy annotation data
        to a PyQt5 pixmap object """
    # for now fg and bg colors are hard coded.
    # later we plan to let the user specify these in the user interface.
    np_rgb = np.zeros((slice_np.shape[1], slice_np.shape[2], 4))
    np_rgb[:, :, 1] = slice_np[0] * 255 # green is bg
    np_rgb[:, :, 0] = slice_np[1] * 255 # red is fg
    np_rgb[:, :, 3] = np.sum(slice_np, axis=0) * 180 # alpha is defined
    q_image = qimage2ndarray.array2qimage(np_rgb)
    return QtGui.QPixmap.fromImage(q_image)


def get_outline_pixmap(seg_slice, annot_slice):
    seg_map = (seg_slice > 0).astype(np.int)
    annot_plus = (annot_slice[1] > 0).astype(np.int)
    annot_minus = (annot_slice[0] > 0).astype(np.int)

    # remove anything where seg is less than 0 as this is outside of the box
    seg_minus = (seg_slice < 0).astype(np.int)
    mask = ((((seg_map + annot_plus) - annot_minus) - seg_minus) > 0)
    dilated = binary_dilation(mask)
    outline = dilated.astype(np.int) - mask.astype(np.int)
    np_rgb = np.zeros((outline.shape[0], outline.shape[1], 4))
    np_rgb[outline > 0] = [255, 255, 0, 180]
    q_image = qimage2ndarray.array2qimage(np_rgb)
    return QtGui.QPixmap.fromImage(q_image)


def seg_slice_to_pixmap(slice_np):
    """ convert slice from the numpy segmentation data
        to a PyQt5 pixmap object """
    np_rgb = np.zeros((slice_np.shape[0], slice_np.shape[1], 4))
    np_rgb[slice_np > 0] = [0, 255, 255, 180]
    q_image = qimage2ndarray.array2qimage(np_rgb)
    return QtGui.QPixmap.fromImage(q_image)

def get_slice(volume, slice_idx, mode):
    if mode == 'axial':
        if len(volume.shape) > 3:
            slice_idx = (volume.shape[1] - slice_idx) - 1
            # if more than 3 presume first is channel dimension
            slice_data = volume[:, slice_idx, :, :]
        else:
            slice_idx = (volume.shape[0] - slice_idx) - 1
            slice_data = volume[slice_idx, :, :]
    elif mode == 'coronal':
        raise Exception("not yet implemented")
        if len(volume.shape) > 3:
            # if more than 3 presume first is channel dimension
            slice_data = volume[:, :, :, slice_idx]
        else:
            slice_data = volume[:, slice_idx, :]
    elif mode == 'sagittal':
        if len(volume.shape) > 3:
            # if more than 3 presume first is channel dimension
            slice_data = volume[:, :, :, slice_idx]
        else:
            slice_data = volume[:, :, slice_idx]
    else:
        raise Exception(f"Unhandled slice mode: {mode}")
    return slice_data


def store_annot_slice(annot_pixmap, annot_data, slice_idx, mode):
    """
    Update .annot_data at slice_idx
    so the values for fg and bg correspond to annot_pixmap)
    """
    slice_rgb_np = np.array(qimage2ndarray.rgb_view(annot_pixmap.toImage()))
    fg = slice_rgb_np[:, :, 0] > 0
    bg = slice_rgb_np[:, :, 1] > 0
    if mode == 'axial': 
        slice_idx = (annot_data.shape[1] - slice_idx) - 1
        annot_data[0, slice_idx] = bg
        annot_data[1, slice_idx] = fg
    elif mode == 'coronal':
        raise Exception("not yet implemented")
        annot_data[0, :, slice_idx, :] = bg
        annot_data[1, :, slice_idx, :] = fg
    elif mode == 'sagittal':
        #slice_idx = (annot_data.shape[3] - slice_idx) - 1
        annot_data[0, :, :, slice_idx] = bg
        annot_data[1, :, :, slice_idx] = fg
    else:
        raise Exception(f"Unhandled slice mode: {mode}")
    return annot_data


def get_num_regions(seg_data, annot_data):
    seg_map = (seg_data > 0).astype(np.int)
    annot_plus = (annot_data[1] > 0).astype(np.int)
    annot_minus = (annot_data[0] > 0).astype(np.int)
    # remove anything where seg is less than 0 as this is outside of the box
    corrected = (((seg_map + annot_plus) - annot_minus) > 0)
    labelled = label(corrected, connectivity=2)
    return len(np.unique(labelled)) - 1 # don't consider background a region.


def restrict_to_regions_containing_points(seg_data, annot_data, region_points):
    assert len(region_points), 'at least one region point must be specified'
    # restrict corrected structure to only the selected
    # connected region found at x,y,z
    # also remove small holes.
    seg_map = (seg_data > 0).astype(np.int)
    annot_plus = (annot_data[1] > 0).astype(np.int)
    annot_minus = (annot_data[0] > 0).astype(np.int)

    # remove anything where seg is less than 0 as this is outside of the box
    corrected = (((seg_map + annot_plus) - annot_minus) > 0)
    labelled = label(corrected, connectivity=2)
    holes_removed = 0
    removed_count = 0
    selected_component = None
    for x, y, z in region_points:
        selected_label = labelled[z, y, x]
        if selected_label == 0:
            error = "Selected region was background. Select a foreground region to keep."
            return annot_data, 0, 0, error
        if selected_component is None:
            selected_component = labelled == selected_label
        else:
            selected_component += labelled == selected_label
        labelled[selected_component] = -1

    disconnected_regions = labelled > 0
    # Then update the annotation so that these regions are now background.
    annot_data[0][disconnected_regions] = 1
    annot_data[1][disconnected_regions] = 0
    removed_count += len(np.unique(labelled[disconnected_regions]))
    # removing small holes
    # this was taking too long so we restrict to the object of interest.
    coords = np.where(selected_component > 0)
    min_z = np.min(coords[0])
    max_z = np.max(coords[0])
    min_y = np.min(coords[1])
    max_y = np.max(coords[1])
    min_x = np.min(coords[2])
    max_x = np.max(coords[2])
    # remove_small_holes
    roi_annot_plus = (annot_data[1, min_z: max_z, min_y:max_y, min_x:max_x] > 0).astype(np.int)
    roi_annot_minus = (annot_data[0,  min_z: max_z, min_y:max_y, min_x:max_x] > 0).astype(np.int)
    # remove anything where seg is less than 0 as this is outside of the box
    roi_corrected = (((seg_map[min_z: max_z, min_y:max_y, min_x:max_x] + roi_annot_plus) - roi_annot_minus) > 0)
    roi_corrected_no_holes = binary_fill_holes(roi_corrected).astype(np.int)
    roi_extra_fg = roi_corrected_no_holes - roi_corrected
    holes_removed += len(np.unique(label(roi_extra_fg))) - 1
    # Set the extra foreground from remove small holes to foreground in the annotation.
    annot_data[0, min_z: max_z, min_y:max_y, min_x:max_x][roi_extra_fg > 0] = 0
    annot_data[1, min_z: max_z, min_y:max_y, min_x:max_x][roi_extra_fg > 0] = 1
    return annot_data, removed_count, holes_removed, False


def fill_annot(annot_pixmap):
    # convert the current annot slice to numpy
    image = annot_pixmap.toImage()
    rgb_np = np.array(qimage2ndarray.rgb_view(image))
    fg_mask = rgb_np[:, :, 0]
    fg_mask = binary_fill_holes(fg_mask)
    fg_mask = fg_mask.astype(np.int)
    np_rgba = np.zeros((rgb_np.shape[0], rgb_np.shape[1], 4))
    # set fg annotation to be new fg mask
    # set bg annotation to be opposite of the fg mask
    np_rgba[:, :, :][fg_mask < 1] = [0, 255, 0, 180]
    np_rgba[:, :, :][fg_mask > 0] = [255, 0, 0, 180]

    q_image = qimage2ndarray.array2qimage(np_rgba)
    return QtGui.QPixmap.fromImage(q_image)
