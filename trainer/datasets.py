"""
Copyright (C) 2019, 2020 Abraham George Smith

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

import random
import math
import os
from pathlib import Path

import torch
import numpy as np
from skimage import img_as_float32
from torch.utils.data import Dataset

from im_utils import load_train_image_and_annot
import im_utils
# from monai.transforms import (
#     AddChanneld,
#     Compose,
#     LoadImaged,
#     RandAffined,
#     RepeatChanneld,
#     MapTransform,
#     RandFlipd,
#     RandGaussianNoised,
#     RandZoomd,
#     NormalizeIntensityd,
#     RandGaussianSmoothd,
#     RandScaleIntensityd,
#     RandFlipd,
#     RandCropByPosNegLabeld,
#     SpatialPadd)

def rnd():
    """ Give higher than random chance to select the edges """
    return max(0, min(1, (1.2 * random.random()) - 0.1))


class RPDataset(Dataset):
    def __init__(self, annot_dirs, train_seg_dirs, dataset_dir, in_w, out_w,
                 in_d, out_d, mode, tile_refs=None, length=None):
        """
        in_w and out_w are the tile size in pixels

        target_classes is a list of the possible output classes
            the position in the list is the index (target) to be predicted by
            the network in the output.
            The value of the elmenent is the rgba (int, int, int) used to draw this
            class in the annotation.

            When the data is 3D the raw channels (for each class)
            are saved and the RGB values are not necessary.
        """
        self.mode = mode
        self.in_w = in_w
        self.out_w = out_w
        self.in_d = in_d
        self.out_d = out_d
        self.annot_dirs = annot_dirs
        self.train_seg_dirs = train_seg_dirs
        self.dataset_dir = dataset_dir
        assert (tile_refs is None) or (length is None) and (length or tile_refs)
        # if tile_refs are defined then these will be used.
        self.tile_refs = tile_refs
        # other wise length will return the number of items
        self.length = length

    def __len__(self):
        if self.mode == 'val':
            return len(self.tile_refs)
        if self.tile_refs is not None:
            return len(self.tile_refs)
        return self.length

    def __getitem__(self, i):
        if self.mode == 'val':
            return self.get_val_item(self.tile_refs[i])
        if self.tile_refs is not None:
            return self.get_train_item(self.tile_refs)
        return self.get_train_item()

    def get_train_item(self, tile_ref=None):
        return self.get_train_item_3d(tile_ref)

    def get_random_tile_3d(self, annots, segs, image, fname):
        # this will find something eventually as we know
        # all annotation contain labels somewhere

        # Limits for possible sampling locations from image (based on size of image)
        depth_lim = image.shape[0] - self.in_d
        bottom_lim = image.shape[1] - self.in_w
        right_lim = image.shape[2] - self.in_w

        attempts = 0 
        warn_after_attempts = 100
        while True:
            attempts += 1
            x_in = math.floor(rnd() * right_lim)
            y_in = math.floor(rnd() * bottom_lim)
            z_in = math.floor(rnd() * depth_lim)

            annot_tiles = []
            seg_tiles = []
            for seg, annot in zip(segs, annots):
                # Get the corresponding region of the annotation after network crop
                annot_tiles.append(annot[:,
                                         z_in:z_in+self.in_d,
                                         y_in:y_in+self.in_w,
                                         x_in:x_in+self.in_w])
                if seg is None:
                    seg_tiles.append(None)
                else:
                    seg_tiles.append(seg[z_in:z_in+self.in_d,
                                         y_in:y_in+self.in_w,
                                         x_in:x_in+self.in_w])



            # we only want annotations with defiend regions in the output area.
            # Otherwise we will have nothing to update the loss.
            if np.any([np.any(a) for a in annot_tiles]):
                # ok we have some annotation for this
                # part of the image so let's return the patch.
                im_tile = image[z_in:z_in+self.in_d,
                                y_in:y_in+self.in_w,
                                x_in:x_in+self.in_w]

                return annot_tiles, seg_tiles, im_tile
            if attempts > warn_after_attempts:
                print(f'Warning {attempts} attempts to get random patch from {fname}')
                warn_after_attempts *= 10

    def one_hot_decode(self, img):
        res = []
        for i in range(0, img.shape[0]):
            res.append(np.expand_dims(img[0]*(i+1), 0))

        return res

    def get_data_all_annots(self, k,  data_dict, annot):
        labels = annot.shape[0]
        remaning = list(range(0, labels))
        remaning.remove(k)

        data_dict['annots'] = np.expand_dims(annot[k], 0)
        for i in remaning:
            data_dict['annots_' + str(i)] = np.expand_dims(annot[i], 0)
        return data_dict, remaning
    
    def get_remaing_keys(self, data_dict):
        annot_names = []
        for k in data_dict:
            if k.startswith('annots_'):
                annot_names.append(k)
        return annot_names
    
    def combine_dicts(self, data_dict, k, remaining):
        combined = []
        total = len([k] + remaining)
        for i in range(0, total):
            if i == k:
                combined.append(data_dict['annots'])
            else:
                combined.append(data_dict['annots_' + str(i)])
        return np.concatenate(combined, axis=0)

    def get_random_tile_3d_byPosNeg(self, annots, segs, image, fname):
        attempts = 0 
        warn_after_attempts = 100
        #image = np.concatenate((np.expand_dims(image, 0), np.expand_dims(image, 0)), axis=0)
        data_dict = dict()
        data_dict['image'] = np.expand_dims(image, 0)

        while True:
            attempts += 1
            annot_tiles = []
            seg_tiles = []
            for seg, annot in zip(segs, annots):
                # Get the corresponding region of the annotation after network crop
                data_dict['segs'] = np.expand_dims(seg, 0)
                k = random.randint(0, annot.shape[0]-1)
                data_dict, remaining = self.get_data_all_annots(k, data_dict, annot)
                annot_names = self.get_remaing_keys(data_dict)
                spatialpad = SpatialPadd(
                    keys=['image', 'annots', 'segs'] + annot_names,
                    spatial_size=[self.in_d,
                                  self.in_w,
                                  self.in_w])
                randCrop = RandCropByPosNegLabeld(
                    keys=['image', 'annots', 'segs'] + annot_names,
                    label_key='annots',
                    spatial_size=[self.in_d,
                                  self.in_w,
                                  self.in_w],
                    pos=1, neg=1, num_samples=1)

                transform = Compose([spatialpad, randCrop])
                data_dict_return = transform(data_dict)
                seg_tiles.append(data_dict_return[0]['segs'][0])
                annot_tiles.append(self.combine_dicts(
                    data_dict_return[0],
                    k,
                    remaining))
            if np.any([np.any(a) for a in annot_tiles]):
                im_tile = data_dict_return[0]['image'][0]
                return annot_tiles, seg_tiles, im_tile
            if attempts > warn_after_attempts:
                print(f'Warning {attempts} attempts to get random patch from {fname}')
                warn_after_attempts *= 10

    def apply_data_augmentation(self, im_tile, annot_tiles, seg_tiles):
        # wrap in dict for transforms
        data_dict = dict()
        data_dict['image'] = np.expand_dims(im_tile, 0)
        if seg_tiles[0] is not None:
            data_dict['segs'] = np.expand_dims(seg_tiles[0], 0)
        # unroll the list of annots into a dict
        annots_dict = self.unroll_annots_to_dict(annot_tiles)
        # get names for each annot
        # annot_names = annots_dict.keys()
        data_dict.update(annots_dict)
        self.feature_names = data_dict.keys()
        transforms_to_apply = self.get_train_transforms()
        data_dict_return = transforms_to_apply(data_dict)
        im_tile = data_dict_return['image'][0, :, :, :]
        if seg_tiles[0] is not None:
            seg_tiles = [data_dict_return['segs'][0, :, :, :]]
        annot_tiles = [self.transform_dict_of_annots_to_numpy(annots_dict)]
        return im_tile, annot_tiles, seg_tiles


    def get_train_item_3d(self, tile_ref):
        # When tile_ref is specified we use these coordinates to get
        # the input tile. Otherwise we will sample randomly
        if tile_ref:
            raise Exception('not using these')
            im_tile, foregrounds, backgrounds, classes = self.get_tile_from_ref_3d(tile_ref)
            # For now just return the tile. We plan to add augmentation here.
            return im_tile, foregrounds, backgrounds, classes

        (image, annots, segs, classes, fname) = load_train_image_and_annot(self.dataset_dir,
                                                                           self.train_seg_dirs,
                                                                           self.annot_dirs)
        
       # annot_tiles, seg_tiles, im_tile = self.get_random_tile_3d_byPosNeg(annots, segs, image, fname)
        annot_tiles, seg_tiles, im_tile = self.get_random_tile_3d(annots, segs, image, fname)
        im_tile, annot_tiles, seg_tiles = self.apply_data_augmentation(
            im_tile, annot_tiles, seg_tiles)
        
        

        im_tile = img_as_float32(im_tile)
        im_tile = im_utils.normalize_tile(im_tile)
        # ensure image is still 32 bit after normalisation.
        im_tile = im_tile.astype(np.float32)
        # need list of foregrounds and masks for all tiles.
        foregrounds = []
        backgrounds = []
        for annot_tile in annot_tiles:
            #annot tile shape is  (2, 18, 194, 194)
            foreground = np.array(annot_tile)[1]
            background = np.array(annot_tile)[0]
            foreground = foreground.astype(np.int64)
            foreground = torch.from_numpy(foreground)
            foregrounds.append(foreground)
            background = background.astype(np.int64)
            background = torch.from_numpy(background)
            backgrounds.append(background)

        im_tile = im_tile.astype(np.float32)
        
        # add dimension for input channel
        im_tile = np.expand_dims(im_tile, axis=0)
        assert len(backgrounds) == len(seg_tiles)
        return im_tile, foregrounds, backgrounds, seg_tiles, classes

    def unroll_annots_to_dict(self, annots):
        annot_dict = dict()
        for i in range(0, annots[0].shape[0]):
            annot_dict['annots_' + str(i)] = np.expand_dims(annots[0][i], 0)
        return annot_dict

    def transform_dict_of_annots_to_numpy(self, annot_dict):
        # loop through the dict annd stack the numpy arrays
        for idx, annot in enumerate(list(annot_dict.keys())):
            if idx == 0:
                annots = annot_dict[annot]
            else:
                annots = np.vstack((annots, annot_dict[annot]))
        return annots

    def get_modes_sampling(self):
        modes = []
        for i in self.feature_names:
            if i == 'image':
                modes.append('bilinear')
            else:
                modes.append('nearest')
        return modes

    def import_transforms(self):
        from monai.transforms import (
            AddChanneld,
            Compose,
            LoadImaged,
            RandAffined,
            RepeatChanneld,
            MapTransform,
            RandFlipd,
            RandGaussianNoised,
            RandZoomd,
            NormalizeIntensityd,
            RandGaussianSmoothd,
            RandScaleIntensityd,
            RandFlipd,
            RandCropByPosNegLabeld,
            SpatialPadd)
        return Compose, LoadImaged, RandAffined, RepeatChanneld, MapTransform, RandFlipd, RandGaussianNoised, RandZoomd, NormalizeIntensityd, RandGaussianSmoothd, RandScaleIntensityd, RandFlipd, RandCropByPosNegLabeld, SpatialPadd
    def get_train_transforms(self):
        modes = self.get_modes_sampling()
        Compose, LoadImaged, RandAffined, RepeatChanneld, MapTransform, RandFlipd, RandGaussianNoised, RandZoomd, NormalizeIntensityd, RandGaussianSmoothd, RandScaleIntensityd, RandFlipd, RandCropByPosNegLabeld, SpatialPadd = self.import_transforms()
        transforms = Compose([
            # translation
            RandAffined(
                    keys=self.feature_names,
                    mode=modes,
                    prob=0.2,
                    spatial_size=(self.in_d,
                                  self.in_w,
                                  self.in_w),
                    translate_range=(
                            int(0.5*self.in_d),
                            int(0.22*self.in_w),
                            int(0.22*self.in_w)),
                    padding_mode="zeros"),
            # scaling spatial
            RandAffined(
                    keys=self.feature_names,
                    mode=modes,
                    prob=0.2,
                    spatial_size=(self.in_d,
                                  self.in_w,
                                  self.in_w),
                    scale_range=(0, 0.15, 0.15),
                    padding_mode="zeros"),
            # scaling temporal
            RandZoomd(
                keys=self.feature_names,
                prob=0.2,
                min_zoom=(0.5, 1, 1),
                max_zoom=(1.5, 1, 1),
                mode=['nearest' for i in range(0, len(self.feature_names))]),
            # rotation
            RandAffined(
                keys=self.feature_names,
                mode=modes,
                prob=0.2,
                rotate_range=(0.17, 0, 0),
                spatial_size=(self.in_d,
                              self.in_w,
                              self.in_w),
                padding_mode="zeros"),
            # flipping
            RandFlipd(keys=self.feature_names, spatial_axis=[0], prob=0.2),
            RandFlipd(keys=self.feature_names, spatial_axis=[1], prob=0.2),
            RandFlipd(keys=self.feature_names, spatial_axis=[2], prob=0.2),
            # Sclae intensity
            RandScaleIntensityd(keys=["image"], factors=0.3, prob=0.15),
            # Gaussian noise
            RandGaussianNoised(keys=["image"], std=0.01, prob=0.15),
            # Gaussian smoothing
            RandGaussianSmoothd(
                keys=["image"],
                sigma_x=(0.0, 0.0),
                sigma_y=(0.5, 1.15),
                sigma_z=(0.5, 1.15),
                prob=0.15,
            ),
        ])

        return transforms
    def get_val_item(self, tile_ref):
        return self.get_tile_from_ref_3d(tile_ref)

    def get_tile_from_ref_3d(self, tile_ref):
        """ return image tile, annotation tile and mask
            for a given file name ans location specified
            in x,y,z relative to the annotation """

        fname, (tile_x, tile_y, tile_z), _, _ = tile_ref
        image_path = os.path.join(self.dataset_dir, fname)
        # image could have nrrd extension
        if not os.path.isfile(image_path):
            image_path = image_path.replace('.nii.gz', '.nrrd')
        image = im_utils.load_with_retry(im_utils.load_image, image_path)
        #  needs to be swapped to channels first and rotated etc
        # to be consistent with everything else.
        # todo: consider removing this soon.
        #image = np.rot90(image, k=3)
       # image = np.moveaxis(image, -1, 0) # depth moved to beginning
        # reverse lr and ud
        #image = image[::-1, :, ::-1]

        # pad so seg will be size of input image
        image = np.pad(image, ((17, 17), (17, 17), (17, 17)), mode='constant')

        classes = []
        foregrounds = []
        backgrounds = []
        annot_tiles = []

        for annot_dir in self.annot_dirs:
            annot_path = os.path.join(annot_dir, fname)

            annot = im_utils.load_with_retry(im_utils.load_image, annot_path)
            classes.append(Path(annot_dir).parts[-2])
            
            # pad to provide annotation at same size as input image.
            annot = np.pad(annot, ((0, 0), (17, 17), (17, 17), (17, 17)), mode='constant')
            # The x, y and z are in reference to the annotation tile before padding.
            annot_tile = annot[:,
                               tile_z:tile_z+self.in_d,
                               tile_y:tile_y+self.in_w,
                               tile_x:tile_x+self.in_w]

            assert annot_tile.shape[1:] == (self.in_d, self.in_w, self.in_w), (
                f" annot is {annot_tile.shape}, and "
                f"should be ({self.in_d},{self.in_w},{self.in_w})")

            #else: # not auto-complete
            #    if os.path.isfile(annot_path):
            #        # The x, y and z are in reference to the annotation tile before padding.
            #        annot_tile = annot[:,
            #                           tile_z:tile_z+self.out_d,
            #                           tile_y:tile_y+self.out_w,
            #                           tile_x:tile_x+self.out_w]

            annot_tiles.append(annot_tile)

        im_tile = image[tile_z:tile_z + self.in_d,
                        tile_y:tile_y + self.in_w,
                        tile_x:tile_x + self.in_w]
 
        assert im_tile.shape == (self.in_d, self.in_w, self.in_w), (
            f" shape is {im_tile.shape}")
        
        for annot_tile in annot_tiles:
            foreground = np.array(annot_tile)[1]
            background = np.array(annot_tile)[0]
            foreground = foreground.astype(np.int64)
            foreground = torch.from_numpy(foreground)
            foregrounds.append(foreground)
            background = background.astype(np.int64)
            background = torch.from_numpy(background)
            backgrounds.append(background)

        im_tile = img_as_float32(im_tile)
        im_tile = im_utils.normalize_tile(im_tile)
        im_tile = im_tile.astype(np.float32)
        im_tile = np.expand_dims(im_tile, axis=0)
        segs = [None] * len(backgrounds)
        return im_tile, foregrounds, backgrounds, segs, classes
