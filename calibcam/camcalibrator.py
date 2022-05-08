import os

import numpy as np
from scipy.io import savemat as scipy_io_savemat
import cv2

from itertools import compress
from pathlib import Path

import imageio
from ccvtools import rawio  # noqa

import multiprocessing
from joblib import Parallel, delayed

from .detection import detect_corners
from .exceptions import *
from . import helper, camfunctions, board

from .calibrator_opts import get_default_opts
from .pose_estimation import estimate_cam_poses


class CamCalibrator:
    def __init__(self, board_name=None, opts=None):
        if opts is None:
            opts = {}

        self.board_name = board_name  # Currently, recordings are needed to determine the board path in most cases

        # Options
        self.opts = helper.deepmerge_dicts(opts, get_default_opts())

        # Board
        self.board_params = None

        # Videos
        self.readers = None
        self.rec_file_names = None
        self.recordingIsLoaded = False
        self.dataPath = None
        self.n_frames = np.NaN
        self.reset_recordings()  # SPOT principle, previous defaults are irrelevant and only there for initial type

        return

    def get_board_params_from_name(self, board_name):
        if board_name is not None:
            board_params = board.get_board_params(board_name)
        else:
            board_params = board.get_board_params(Path(self.rec_file_names[0]).parent)
        return board_params

    def reset_recordings(self):
        self.readers = None
        self.rec_file_names = None
        self.recordingIsLoaded = False
        self.dataPath = None
        self.n_frames = np.NaN

    def set_recordings(self, recordings):
        # check if input files are valid files
        try:
            self.readers = [imageio.get_reader(rec) for rec in recordings]
        except ValueError:
            print('At least one unsupported format supplied')
            raise UnsupportedFormatException

        self.dataPath = os.path.dirname(recordings[0])
        self.rec_file_names = recordings

        # find frame numbers
        n_frames = np.zeros(len(self.readers), dtype=np.int64)
        for (i_cam, reader) in enumerate(self.readers):
            n_frames[i_cam] = camfunctions.get_n_frames_from_reader(reader)
            print(f'Found {n_frames[i_cam]} frames in cam {i_cam}')

        # check if frame number is consistent
        self.n_frames = n_frames[0]
        if not np.all(np.equal(n_frames[0], n_frames[1:])):
            print('WARNING: Number of frames is not identical for all cameras')
            print('Number of detected frames per camera:')
            for (i_cam, nF) in enumerate(n_frames):
                print(f'\tcamera {i_cam:03d}:\t{nF:04d}')

            if self.opts['allow_unequal_n_frame']:
                self.n_frames = np.int64(np.min(n_frames))
            else:
                # raise exception for outside confirmation
                raise UnequalFrameCountException

        self.board_params = self.get_board_params_from_name(self.board_name)

        self.recordingIsLoaded = True

    def perform_multi_calibration(self):
        # detect corners
        corners_all, ids_all, frames_masks = \
            detect_corners(self.rec_file_names, self.n_frames, self.board_params, self.opts)

        # # split into two frame sets
        # # first set contains frames for single calibration
        # # second set contains frames for multi calibration
        # self.split_frame_sets()

        # perform single calibration
        calibs_single = self.perform_single_cam_calibrations(corners_all, ids_all, frames_masks)

        # analytically estimate initial camera poses
        calibs_multi = estimate_cam_poses(calibs_single, self.opts['coord_cam'])

        # Save intermediate result, for dev purposes on optimization (quote code above and unquote code below)
        np.savez(self.dataPath + '/preoptim.npz', calibs_single, calibs_multi, corners_all, ids_all, frames_masks)
        # preoptim = np.load(self.dataPath + '/preoptim.npz', allow_pickle=True)
        # calibs_single = preoptim['arr_0']
        # # calibs_multi = preoptim['arr_1']
        # corners_all = preoptim['arr_2']
        # ids_all = preoptim['arr_3']
        # frames_masks = preoptim['arr_4']
        # calibs_multi = estimate_cam_poses(calibs_single, self.opts['coord_cam'])

        print('START MULTI CAMERA CALIBRATION')
        calibs_fit, _, _, min_result, args = \
            self.start_optimization(corners_all, ids_all, calibs_multi, frames_masks)

        result = self.build_result(calibs_fit, frames_masks=frames_masks, tvecs_boards=None, min_result=min_result, args=args)

        print('SAVE MULTI CAMERA CALIBRATION')
        self.save_multicalibration(result)

        print('FINISHED MULTI CAMERA CALIBRATION')
        return

    @staticmethod
    def calibrate_single_camera(corners_cam, ids_cam, sensor_size, board_params, opts, mask=None):
        if mask is None:
            mask = np.asarray([len(c) > 0 for c in corners_cam], dtype=bool)

        n_used_frames = np.sum(mask)

        if n_used_frames > 0:  # do this to not run into indexing issues
            corners_use = list(compress(corners_cam,
                                        mask))

            ids_use = list(compress(ids_cam,
                                    mask))
            cal_res = cv2.aruco.calibrateCameraCharuco(corners_use,  # noqa
                                                       ids_use,
                                                       board.make_board(board_params),
                                                       sensor_size,
                                                       None,
                                                       None,
                                                       **opts['aruco_calibration'])

            retval, A, k, rvecs, tvecs = cal_res[0:5]

            cal = {
                'rvec_cam': np.asarray([0., 0., 0.]),
                'tvec_cam': np.asarray([0., 0., 0.]),
                'A': np.asarray(A),
                'k': np.asarray(k),
                'rvecs': np.asarray(rvecs),
                'tvecs': np.asarray(tvecs),
                'repro_error': retval,
                'frame_mask': mask,
            }
            print('Finished single camera calibration.')
            return cal
        else:
            return []

    def perform_single_cam_calibrations(self, corners_all, ids_all, frame_mask):
        print('PERFORM SINGLE CAMERA CALIBRATION')

        # calibs_single = [self.calibrate_single_camera(corners_all[i_cam],
        #                                               ids_all[i_cam],
        #                                               helper.get_header_from_reader(self.readers[i_cam])['sensorsize'],
        #                                               self.board_params,
        #                                               self.opts,
        #                                               mask)
        #                  for i_cam in range(len(self.readers))]
        calibs_single = Parallel(n_jobs=int(np.floor(multiprocessing.cpu_count() / 2)))(
            delayed(self.calibrate_single_camera)(corners_all[i_cam],
                                                  ids_all[i_cam],
                                                  camfunctions.get_header_from_reader(self.readers[i_cam])['sensorsize'],
                                                  self.board_params,
                                                  self.opts)
            for i_cam in range(len(self.readers)))

        for i_cam, calib in enumerate(calibs_single):
            calib['frame_mask'] = frame_mask[i_cam].copy()
            assert calib['frame_mask'].sum(dtype=int) == calib['tvecs'].shape[0], "Sizes do not match, check masks."
            print(
                f'Used {calib["frame_mask"].sum(dtype=int):03d} frames for single cam calibration for cam {i_cam:02d}')

        return calibs_single

    def start_optimization(self, corners_all, ids_all, calibs_multi, frame_masks, opts=None, board_params=None):
        if opts is None:
            opts = self.opts
        if board_params is None:
            board_params = self.board_params

        calibs_fit, rvecs_boards, tvecs_boards, min_result, args =\
            camfunctions.optimize_calib_parameters(corners_all, ids_all, calibs_multi, frame_masks,
                                                   opts=opts, board_params=board_params)

        return calibs_fit, rvecs_boards, tvecs_boards, min_result, args

    def build_result(self, calibs,
                     frames_masks=None, rvecs_boards=None, tvecs_boards=None, min_result=None, args=None):  # noqa
        result = {
            'version': 2.0,
            'calibs': calibs,
            'board_params': self.board_params,
            'rec_file_names': self.rec_file_names,
            'vid_headers': [camfunctions.get_header_from_reader(r) for r in self.readers],
            'info': {
                'cost_val_final': np.NaN,
                'optimality_final': np.NaN,
                'frames_masks': np.array([], dtype=bool),
                'opts': self.opts,
            }
        }

        if min_result is not None:
            result['info']['cost_val_final'] = min_result.cost
            result['info']['optimality_final'] = min_result.optimality

        if frames_masks is not None:
            result['info']['frames_masks'] = frames_masks

        return result

    def save_multicalibration(self, result):
        # save
        result_path = self.dataPath + '/multicam_calibration'
        np.save(result_path+'.npy', result)
        scipy_io_savemat(result_path + '.mat', result)
        print('Saved multi camera calibration to file {:s}'.format(result_path))
        return
