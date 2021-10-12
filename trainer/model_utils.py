""" Utilities for working with the U-Net models 
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

# pylint: disable=C0111, R0913, R0914, W0511
import os
import time
import math
import numpy as np
import torch
import copy
from skimage import img_as_float32
import im_utils
from unet3d import UNet3D
from file_utils import ls
from torch.nn.functional import softmax
import torch.nn.functional as F

cached_model = None
cached_model_path = None
use_fake_cnn = False

def fake_cnn(tiles_for_gpu):
    """ Useful debug function for checking tile layout etc """
    output = []
    for t in tiles_for_gpu:
        v = t[0, 17:-17, 17:-17, 17:-17].data.cpu().numpy()
        v_mean = np.mean(v)
        output.append((v > v_mean).astype(np.int8))
    return np.array(output)
 


def get_latest_model_paths(model_dir, k):
    fnames = ls(model_dir)
    fnames = sorted(fnames)[-k:]
    fpaths = [os.path.join(model_dir, f) for f in fnames]
    return fpaths


def load_model(model_path, classes):
    global cached_model
    global cached_model_path
    
    # using cache can save up to half a second per segmentation with network drives
    if model_path == cached_model_path:
        return copy.deepcopy(cached_model)
    # two channels as one is input image and another is some of the fg and bg annotation
    # each non-empty channel in the annotation is included with 50% chance.
    # Option1 - fg and bg will go in as seprate channels 
    #           so channels are [image, fg_annot, bg_annot]
    # Option2 - 
    #   when included both fg a bg go into the model bg is -1 and fg is +1. undefined is 0
    # Option 1 will be evaluated first (possibilty easier to implement)
    model = UNet3D(classes, im_channels=3)
    try:
        model.load_state_dict(torch.load(model_path))
        model = torch.nn.DataParallel(model)
    # pylint: disable=broad-except, bare-except
    except:
        model = torch.nn.DataParallel(model)
        model.load_state_dict(torch.load(model_path))
    if not use_fake_cnn:
        model.cuda()
    # store in cache as most frequest model is laoded often
    cached_model_path = model_path
    cached_model = model
    return copy.deepcopy(model)


def random_model(classes):
    # num out channels is twice number of channels
    # as we have a positive and negative output for each structure.
    model = UNet3D(classes, im_channels=3)
    model = torch.nn.DataParallel(model)
    if not use_fake_cnn: 
        model.cuda()
    return model

def create_first_model_with_random_weights(model_dir, classes):
    # used when no model was specified on project creation.
    model_num = 1
    model_name = str(model_num).zfill(6)
    model_name += '_' + str(int(round(time.time()))) + '.pkl'
    model = random_model(classes)
    model_path = os.path.join(model_dir, model_name)
    torch.save(model.state_dict(), model_path)
    if not use_fake_cnn: 
        model.cuda()
    return model


def get_prev_model(model_dir, classes):
    prev_path = get_latest_model_paths(model_dir, k=1)[0]
    prev_model = load_model(prev_path, classes)
    return prev_model, prev_path


def save_if_better(model_dir, cur_model, prev_model_path, cur_dice, prev_dice):
    # convert the nans as they don't work in comparison
    if math.isnan(cur_dice):
        cur_dice = 0
    if math.isnan(prev_dice):
        prev_dice = 0
    print('Validation: prev dice', str(round(prev_dice, 5)).ljust(7, '0'),
          'cur dice', str(round(cur_dice, 5)).ljust(7, '0'))
    if cur_dice > prev_dice:
        save_model(model_dir, cur_model, prev_model_path)
        return True
    return False

def save_model(model_dir, cur_model, prev_model_path):
    prev_model_fname = os.path.basename(prev_model_path)
    prev_model_num = int(prev_model_fname.split('_')[0])
    model_num = prev_model_num + 1
    now = int(round(time.time()))
    model_name = str(model_num).zfill(6) + '_' + str(now) + '.pkl'
    model_path = os.path.join(model_dir, model_name)
    print('saving', model_path, time.strftime('%H:%M:%S', time.localtime(now)))
    torch.save(cur_model.state_dict(), model_path)


def ensemble_segment_3d(model_paths, image, fname, batch_size, in_w, out_w, in_d,
                        out_d, classes, bounded):
    """ Average predictions from each model specified in model_paths """
    t = time.time()
    input_image_shape = image.shape
    cnn = load_model(model_paths[0], classes)
    in_patch_shape = (in_d, in_w, in_w)
    out_patch_shape = (out_d, out_w, out_w)

    depth_diff = in_patch_shape[0] - out_patch_shape[0]
    height_diff = in_patch_shape[1] - out_patch_shape[1]
    width_diff = in_patch_shape[2] - out_patch_shape[2]

    if not bounded:
        # pad so seg will be size of input image
        image = im_utils.pad_3d(image, width_diff//2, depth_diff//2,
                                mode='reflect', constant_values=0)

    # segment returns a series of prediction maps. one for each class.
    pred_maps = segment_3d(cnn, image, batch_size, in_patch_shape, out_patch_shape)

    if not bounded:
        assert pred_maps[0].shape == input_image_shape

    # end of fname is constructed like this
    # the indices e.g -14 are inserted here for convenience
    # f"_x_{box['x'] (-14) }_y_{box['y'] (-13) }_z_{box['z'] (-11) }_pad_"
    # f"x_{x_pad_start (-8) }_{x_pad_end (-7) }"
    # f"y_{y_pad_start (-5) }_{y_pad_end (-4 )}"
    # f"z_{z_pad_start (-2) }_{z_pad_end (-1) }.nii.gz")

    if bounded: 
        fname_parts = fname.replace('.nii.gz', '').split('_')
        x_crop_start = int(fname_parts[-8])
        x_crop_end = int(fname_parts[-7])
        y_crop_start = int(fname_parts[-5])
        y_crop_end = int(fname_parts[-4])
        z_crop_start = int(fname_parts[-2])
        z_crop_end = int(fname_parts[-1])

        # The output of the cnn is already cropped during inference.
        # subtract this default cropping from each of the crop sizes
        z_crop_start -= depth_diff // 2
        z_crop_end -= depth_diff // 2

        y_crop_start -= height_diff // 2
        y_crop_end -= height_diff // 2

        x_crop_start -= width_diff // 2
        x_crop_end -= width_diff // 2

        for i, pred_map in enumerate(pred_maps):
            pred_maps[i] = pred_map[z_crop_start:pred_maps[i].shape[0] - z_crop_end,
                                    y_crop_start:pred_maps[i].shape[1] - y_crop_end,
                                    x_crop_start:pred_maps[i].shape[2] - x_crop_end]

    print('time to segment image', time.time() - t)
    return pred_maps


def segment_patch(model_path, in_dir, fname, classes, annot_dirs,
                  patch_z, patch_y, patch_x, in_d, in_w):
    # load the image with fname
    # and extract the patch from the supplied coorindates
    # segment it and then update the segmentation for this image.

    im_path = os.path.join(bounded_im_dir, bounded_fname)
    (image, annots, classes, fname) = im_utils.load_image_and_annot_for_seg(bounded_im_dir,
                                                                            annot_dirs,
                                                                            bounded_fname)
    im_patch = image[patch_z:patzh_z+in_d, patch_y+in_w, patch_x+in_w]
    im_patch = im_utils.normalize_tile(im_as_float32(im_patch))
    print('im_patch shape = ', im_patch.shape)
    #                       b, c, d,                 h,                 w
    model_input = np.zeros((1, 3, im_patch.shape[0], im_patch.shape[1], im_patch.shape[2]))
    model_input[0, 0] = im_patch 
    
    bg_patch = annots[0][0]
    fg_patch = annots[0][1]

    model_input[0, 1] = bg_patch
    model_input[0, 2] = fg_patch

    cnn = load_model(model_path, classes)
    model_input = torch.from_numpy(model_input).cuda()
    outputs = cnn(tiles_for_gpu)

    # bg channel index for each class in network output.
    class_idxs = [x * 2 for x in range(outputs.shape[1] // 2)]
    
    if class_output_patches is None:
        class_output_patches = [[] for _ in class_idxs]

    for i, class_idx in enumerate(class_idxs):
        class_output = outputs[:, class_idx:class_idx+2]
        # class_output : (batch_size, bg/fg, depth, height, width)
        softmaxed = softmax(class_output, 1) 
        foreground_probs = softmaxed[:, 1]  # just the foreground probability.
        predicted = foreground_probs > 0.5
        predicted = predicted.int()
        pred_np = predicted.data.detach().cpu().numpy()
        for out_tile in pred_np:
            class_output_patches[i].append(out_tile)

    return class_output_patches

def segment_3d(cnn, image, batch_size, in_tile_shape, out_tile_shape):
    """
    in_tile_shape and out_tile_shape are (depth, height, width)
    """
    # Return prediction for each pixel in the image
    # The cnn will give a the output as channels where
    # each channel corresponds to a specific class 'probability'
    # don't need channel dimension
    # make sure the width, height and depth is at least as big as the tile.
    assert len(image.shape) == 3, str(image.shape)
    assert image.shape[0] >= in_tile_shape[0], f"{image.shape[0]},{in_tile_shape[0]}"
    assert image.shape[1] >= in_tile_shape[1], f"{image.shape[1]},{in_tile_shape[1]}"
    assert image.shape[2] >= in_tile_shape[2], f"{image.shape[2]},{in_tile_shape[2]}"

    depth_diff = in_tile_shape[0] - out_tile_shape[0]
    width_diff = in_tile_shape[1] - out_tile_shape[1]
    
    out_im_shape = (image.shape[0] - depth_diff,
                    image.shape[1] - width_diff,
                    image.shape[2] - width_diff)

    coords = im_utils.get_coords_3d(out_im_shape, out_tile_shape)
    coord_idx = 0
    class_output_tiles = None # list of tiles for each class

    while coord_idx < len(coords):
        tiles_to_process = []
        coords_to_process = []
        for _ in range(batch_size):
            if coord_idx < len(coords):
                coord = coords[coord_idx]
                x_coord, y_coord, z_coord = coord
                tile = image[z_coord:z_coord+in_tile_shape[0],
                             y_coord:y_coord+in_tile_shape[1],
                             x_coord:x_coord+in_tile_shape[2]]

                # need to add channel dimension for GPU processing.
                tile = np.expand_dims(tile, axis=0)
                
                assert tile.shape[1] == in_tile_shape[0], str(tile.shape)
                assert tile.shape[2] == in_tile_shape[1], str(tile.shape)
                assert tile.shape[3] == in_tile_shape[2], str(tile.shape)

                tile = img_as_float32(tile)
                tile = im_utils.normalize_tile(tile)
                coord_idx += 1
                tiles_to_process.append(tile) # need channel dimension
                coords_to_process.append(coord)

        tiles_to_process = np.array(tiles_to_process)
        tiles_for_gpu = torch.from_numpy(tiles_to_process)

        tiles_for_gpu = tiles_for_gpu.cuda()
        # TODO: consider use of detach. 
        # I might want to move to cpu later to speed up the next few operations.
        # I added .detach().cpu() to prevent a memory error.
        # pad with zeros for the annotation input channels
        # l,r, l,r, but from end to start     w  w  h  h  d  d, c, c, b, b
        tiles_for_gpu = F.pad(tiles_for_gpu, (0, 0, 0, 0, 0, 0, 0, 2), 'constant', 0)
        # tiles shape after padding torch.Size([4, 3, 52, 228, 228])
        outputs = cnn(tiles_for_gpu).detach().cpu()
        # bg channel index for each class in network output.
        class_idxs = [x * 2 for x in range(outputs.shape[1] // 2)]
        
        if class_output_tiles is None:
            class_output_tiles = [[] for _ in class_idxs]

        for i, class_idx in enumerate(class_idxs):
            class_output = outputs[:, class_idx:class_idx+2]
            # class_output : (batch_size, bg/fg, depth, height, width)
            softmaxed = softmax(class_output, 1) 
            foreground_probs = softmaxed[:, 1]  # just the foreground probability.
            predicted = foreground_probs > 0.5
            predicted = predicted.int()
            pred_np = predicted.data.cpu().numpy()
            for out_tile in pred_np:
                class_output_tiles[i].append(out_tile)

    class_pred_maps = []
    for i, output_tiles in enumerate(class_output_tiles):
        # reconstruct for each class
        reconstructed = im_utils.reconstruct_from_tiles(output_tiles,
                                                        coords, out_im_shape)
        class_pred_maps.append(reconstructed)
    return class_pred_maps
