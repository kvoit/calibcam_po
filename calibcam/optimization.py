# import multiprocessing
# from joblib import Parallel, delayed

import numpy as np
from scipy.spatial.transform import Rotation as R  # noqa
# from autograd import jacobian, elementwise_grad  # noqa
from jax import jacfwd as jacobian  # jacfwd is recommended for 'tall' Jacobians, jacrev for 'wide'

import timeit
from . import optimization_autograd as opt_ag


def obj_fcn_wrapper(vars_opt, args):
    corners = args['precalc']['corners'].copy()  # copy is necessary since this is a reference, so further down, nans
    # will be replaced with 0 globally  TODO find more efficient solution
    corners_mask = np.isnan(corners)
    corners[corners_mask] = 0
    boards_coords_3d_0 = args['precalc']['boards_coords_3d_0']

    # Fill vars_full from initialization with vars_opts
    vars_full, n_cams = make_vars_full(vars_opt, args)

    # Unravel inputs. Note that calibs, board_coords_3d and their representations in args are changed in this function
    # and the return is in fact unnecessary!
    rvecs_cams, tvecs_cams, cam_matrices, ks, rvecs_boards, tvecs_boards = unravel_vars_full(vars_full, n_cams)

    # Tested correct:
    # print(rvecs_cams[2])
    # print(tvecs_cams[2])
    # print(cam_matrices[2])
    # print(ks[2])

    residuals = np.array(opt_ag.obj_fcn(
        rvecs_cams.ravel(),
        tvecs_cams.ravel(),
        cam_matrices.ravel(),
        ks.ravel(),
        rvecs_boards.ravel(),
        tvecs_boards.ravel(),
        boards_coords_3d_0.ravel(),
        corners.ravel()
    ))

    # Residuals of untracked corners are invalid
    residuals[corners_mask] = 0
    print(np.unravel_index(np.argmax(np.abs(residuals)),shape=residuals.shape))
    print(np.max(np.abs(residuals)))
    return residuals.ravel()


def obj_fcn_jacobian_wrapper(vars_opt, args):
    corners = args['precalc']['corners']
    corners_mask = np.isnan(corners)
    boards_coords_3d_0 = args['precalc']['boards_coords_3d_0']

    # Fill vars_full from initialization with vars_opts
    vars_full, n_cams = make_vars_full(vars_opt, args)

    # Unravel inputs. Note that calibs, board_coords_3d and their representations in args are changed in this function
    # and the return is in fact unnecessary!
    rvecs_cams, tvecs_cams, cam_matrices, ks, rvecs_boards, tvecs_boards = unravel_vars_full(vars_full, n_cams)

    # All zero rotvec causes division by 0 problems. Usually, this usually does not matter since the ref cam
    # orientation is not part of the free variables, but we apply this fix to avoid misleading errors
    rvecs_cams = rvecs_cams.copy()
    for i_cam in range(corners.shape[0]):
        if np.all(rvecs_cams[i_cam] == 0):
            rvecs_cams[i_cam][:] = np.finfo(np.float16).eps

    jacobians = args['precalc']['jacobians']

    tic = timeit.default_timer()

    obj_fcn_jacobian_cam_pose, obj_fcn_jacobian_cam_mat, obj_fcn_jacobian_cam_k = \
        calc_cam_jacobian(jacobians,
                          rvecs_cams, tvecs_cams, cam_matrices, ks, rvecs_boards, tvecs_boards,
                          boards_coords_3d_0, corners)
    obj_fcn_jacobian_pose = \
        calc_pose_jacobian(jacobians,
                           rvecs_cams, tvecs_cams, cam_matrices, ks, rvecs_boards, tvecs_boards,
                           boards_coords_3d_0, corners)

    obj_fcn_jacobian = np.concatenate((
        obj_fcn_jacobian_cam_pose.reshape(corners.shape + (-1,)),
        obj_fcn_jacobian_cam_mat.reshape(corners.shape + (-1,)),
        obj_fcn_jacobian_cam_k.reshape(corners.shape + (-1,)),
        obj_fcn_jacobian_pose.reshape(corners.shape + (-1,)),
    ), corners.ndim)

    print(timeit.default_timer() - tic)

    # Residuals of untracked corners are invalid
    obj_fcn_jacobian[corners_mask] = 0

    # Return section of free variables
    obj_fcn_jacobian = obj_fcn_jacobian.reshape(np.prod(corners_mask.shape), -1)
    return obj_fcn_jacobian[:, args['mask_opt']]


def calc_cam_jacobian(jacobians, rvecs_cams, tvecs_cams, cam_matrices, ks, rvecs_boards, tvecs_boards,
                      boards_coords_3d_0, corners):
    n_cam_param_list = np.array([3, 3, 9, 5])

    obj_fcn_jacobian_cam_pose = np.zeros(corners.shape + (2, corners.shape[0], 3), dtype=np.float16)
    offset = 0
    for i_cam in range(corners.shape[0]):
        print(i_cam)
        jacs = [calc_jacobian(jacobians[offset + i_param], (
            # jacs = Parallel(n_jobs=int(np.floor(multiprocessing.cpu_count() / 2) - 2))(
            #     delayed(calc_jacobian)(jacobians[i_param], (
            rvecs_cams[i_cam].ravel(),
            tvecs_cams[i_cam].ravel(),
            cam_matrices[i_cam].ravel(),
            ks[i_cam].ravel(),
            rvecs_boards[0:10].ravel(),
            tvecs_boards[0:10].ravel(),
            boards_coords_3d_0[i_cam, 0:10].ravel(),
            corners[i_cam, 0:10].ravel()
        ))
                for i_param in range(2)]

        for i_param, j in enumerate(jacs):
            if np.any(np.isnan(j)):
                print("In cam")
                print(j)
                print(j.shape)
                print(i_cam)
                print(i_param)
                exit()
            obj_fcn_jacobian_cam_pose[i_cam, 0:10, :, :, i_param, i_cam, :] = j

    offset = offset + 6
    obj_fcn_jacobian_cam_mat = np.zeros(corners.shape + (2, corners.shape[0], 9), dtype=np.float16)
    for i_cam in range(corners.shape[0]):
        print(i_cam)
        print(offset + i_parami)
        jacs = [calc_jacobian(jacobians[offset + i_param], (
            # jacs = Parallel(n_jobs=int(np.floor(multiprocessing.cpu_count() / 2) - 2))(
            #     delayed(calc_jacobian)(jacobians[i_param], (
            rvecs_cams[i_cam].ravel(),
            tvecs_cams[i_cam].ravel(),
            cam_matrices[i_cam].ravel(),
            ks[i_cam].ravel(),
            rvecs_boards[0:10].ravel(),
            tvecs_boards[0:10].ravel(),
            boards_coords_3d_0[i_cam, 0:10].ravel(),
            corners[i_cam, 0:10].ravel()
        ))
                for i_param in range(9)]

        for i_param, j in enumerate(jacs):
            if np.any(np.isnan(j)):
                print("In cam")
                print(j)
                print(j.shape)
                print(i_cam)
                print(i_param)
                exit()
            obj_fcn_jacobian_cam_mat[i_cam, 0:10, :, :, i_param, i_cam, :] = j

    offset = offset + 9
    obj_fcn_jacobian_cam_k = np.zeros(corners.shape + (2, corners.shape[0], 5), dtype=np.float16)
    for i_cam in range(corners.shape[0]):
        print(i_cam)
        jacs = [calc_jacobian(jacobians[offset + i_param], (
            # jacs = Parallel(n_jobs=int(np.floor(multiprocessing.cpu_count() / 2) - 2))(
            #     delayed(calc_jacobian)(jacobians[i_param], (
            rvecs_cams[i_cam].ravel(),
            tvecs_cams[i_cam].ravel(),
            cam_matrices[i_cam].ravel(),
            ks[i_cam].ravel(),
            rvecs_boards[0:10].ravel(),
            tvecs_boards[0:10].ravel(),
            boards_coords_3d_0[i_cam, 0:10].ravel(),
            corners[i_cam, 0:10].ravel()
        ))
                for i_param in range(5)]

        for i_param, j in enumerate(jacs):
            if np.any(np.isnan(j)):
                print("In cam")
                print(j)
                print(j.shape)
                print(i_cam)
                print(i_param)
                exit()
            obj_fcn_jacobian_cam_k[i_cam, 0:10, :, :, i_param, i_cam, :] = j

    return obj_fcn_jacobian_cam_pose, obj_fcn_jacobian_cam_mat, obj_fcn_jacobian_cam_k


def calc_pose_jacobian(jacobians, rvecs_cams, tvecs_cams, cam_matrices, ks, rvecs_boards, tvecs_boards,
                       boards_coords_3d_0, corners):
    n_cam_param_list = np.array([3, 3, 9, 5])
    n_pose_param_list = np.array([3, 3])

    obj_fcn_jacobian_pose = np.zeros(corners.shape + (n_pose_param_list.size(), corners.shape[1], 3), dtype=np.float16)
    for i_pose in range(corners.shape[1]):
        print(i_pose)
        jacs = [calc_jacobian(jacobians[i_param + n_cam_param_list.size], (
            # jacs = Parallel(n_jobs=int(np.floor(multiprocessing.cpu_count() / 2) - 2))(
            #     delayed(calc_jacobian)(jacobians[i_param + n_cam_param_list.size], (
            rvecs_cams.ravel(),
            tvecs_cams.ravel(),
            cam_matrices.ravel(),
            ks.ravel(),
            rvecs_boards[i_pose].ravel(),
            tvecs_boards[i_pose].ravel(),
            boards_coords_3d_0[:, i_pose].ravel(),
            corners[:, i_pose].ravel()
        ))
                for i_param in range(len(n_pose_param_list))]

        for i_param, (j, len_param) in enumerate(zip(jacs, n_pose_param_list)):
            if np.any(np.isnan(j)):
                print("In pose")
                print(j)
                print(j.shape)
                print(i_pose)
                print(i_param)
                exit()
            obj_fcn_jacobian_pose[:, i_pose, :, :, i_param, i_pose, :] = j[:, 0]

    return obj_fcn_jacobian_pose


def calc_jacobian(jac, parameters):
    return jac(*parameters)


def make_vars_full(vars_opt, args):
    n_cams = len(args['precalc']['boards_coords_3d_0'])

    # Update full set of vars with free wars
    vars_full = args['vars_full']
    mask_opt = args['mask_opt']
    vars_full[mask_opt] = vars_opt

    return vars_full, n_cams


def unravel_vars_full(vars_full, n_cams):
    n_cam_param_list = np.array([3, 3, 9, 5])  # r, t, A, k
    n_cam_params = n_cam_param_list.sum(dtype=int)

    p_idx = 0
    st_idx = n_cam_param_list[0:p_idx].sum(dtype=int) * n_cams
    rvecs_cams = vars_full[st_idx:st_idx + n_cam_param_list[p_idx] * n_cams].reshape(n_cam_param_list[p_idx], -1).T

    p_idx = 1
    st_idx = n_cam_param_list[0:p_idx].sum(dtype=int) * n_cams
    tvecs_cams = vars_full[st_idx:st_idx + n_cam_param_list[p_idx] * n_cams].reshape(n_cam_param_list[p_idx], -1).T

    p_idx = 2
    st_idx = n_cam_param_list[0:p_idx].sum(dtype=int) * n_cams
    cam_matrices = vars_full[st_idx:st_idx + n_cam_param_list[p_idx] * n_cams].reshape(3, 3, -1).transpose((2, 0, 1))

    p_idx = 3
    st_idx = n_cam_param_list[0:p_idx].sum(dtype=int) * n_cams
    ks = vars_full[st_idx:st_idx + n_cam_param_list[p_idx] * n_cams].reshape(n_cam_param_list[p_idx], -1).T

    board_pose_vars = vars_full[n_cams * n_cam_params:]
    rvecs_boards = board_pose_vars[0:int(board_pose_vars.size / 2)].reshape(-1, 3)
    tvecs_boards = board_pose_vars[int(board_pose_vars.size / 2):].reshape(-1, 3)

    return rvecs_cams, tvecs_cams, cam_matrices, ks, rvecs_boards, tvecs_boards


def make_initialization(calibs, frame_masks, opts, k_to_zero=True):
    # k_to_zero determines if non-free ks get set to 0 (for limited distortion model) or are kept (usually
    # when not optimizing distortion at all in the given step)
    opts_free_vars = opts['free_vars']

    camera_params = np.zeros(shape=(
        len(calibs),
        3 + 3 + 9 + 5  # r + t + A + k
    ))

    for calib, param in zip(calibs, camera_params):
        param[0:3] = calib['rvec_cam']
        param[3:6] = calib['tvec_cam']
        param[6:15] = calib['A'].ravel()
        if k_to_zero:
            idxs = (15 + np.where(opts_free_vars['k'])[0])
            param[idxs] = calib['k'][0][opts_free_vars['k']]
        else:
            param[15:20] = calib['k']

    pose_params = make_common_pose_params(calibs, frame_masks)

    # camera_params are raveled with one scalar parameter for all cams grouped
    # pose_params are raveled with first all rvecs and then all tvecs (for faster unraveling in obj_fun)
    vars_full = np.concatenate((camera_params.T.ravel(), pose_params.ravel()), axis=0)
    mask_free = make_free_parameter_mask(calibs, frame_masks, opts_free_vars, opts['coord_cam'])
    vars_free = vars_full[mask_free]

    return vars_free, vars_full, mask_free


def make_common_pose_params(calibs, frame_masks):
    pose_idxs = np.where(np.any(frame_masks, axis=0))[0]  # indexes into full frame range
    pose_params = np.zeros(shape=(2, pose_idxs.size, 3))
    # TODO Instead using pose from first available cam, it should be averaged over all available cams.
    # See pose_estimation.estimate_cam_poses for averaging poses
    # This might require fixing the other cam poses in calibration, see respective TODO in pose_estimation
    # calib = calibs[opts['coord_cam']]
    # frame_mask_cam = frame_masks[opts['coord_cam']]
    for i_pose, pose_idx in enumerate(pose_idxs):  # Loop through the poses (frames that have a board pose)
        for calib, frame_mask_cam in zip(calibs, frame_masks):  # Loop through cameras ...
            if np.all(pose_params[0, i_pose, :] == 0) and frame_mask_cam[pose_idx]:  # ... and check if frame is present
                frame_idxs_cam = np.where(frame_mask_cam)[0]  # Frame indexes corresponding to available rvecs/tvecs
                pose_params[0, i_pose, :] = calib['rvecs'][frame_idxs_cam == pose_idx].ravel()
                pose_params[1, i_pose, :] = calib['tvecs'][frame_idxs_cam == pose_idx].ravel()

    return pose_params


def make_free_parameter_mask(calibs, frame_masks, opts_free_vars, coord_cam_idx):
    camera_mask = np.ones(shape=(
        len(calibs),
        3 + 3 + 9 + 5  # r + t + A + k
    ), dtype=bool)

    camera_mask[:, 0:3] = opts_free_vars['cam_pose']
    camera_mask[:, 3:6] = opts_free_vars['cam_pose']
    camera_mask[:, 6:15] = opts_free_vars['A'].ravel()
    camera_mask[:, 10:15] = opts_free_vars['k']

    # Position of coord cam is not free
    camera_mask[coord_cam_idx, 0:6] = False

    pose_idxs = np.where(np.any(frame_masks, axis=0))[0]  # indexes into full frame range
    pose_mask = np.ones(shape=(pose_idxs.size, 2, 3), dtype=bool)
    pose_mask[:] = opts_free_vars['board_poses']

    return np.concatenate((camera_mask.ravel(), pose_mask.ravel()), axis=0)


def unravel_to_calibs(vars_opt, args):
    # Fill vars_full from initialization with vars_opts
    vars_full, n_cams = make_vars_full(vars_opt, args)

    # Unravel inputs. Note that calibs, board_coords_3d and their representations in args are changed in this function
    # and the return is in fact unnecessary!
    rvecs_cams, tvecs_cams, cam_matrices, ks, rvecs_boards, tvecs_boards = unravel_vars_full(vars_full, n_cams)

    calibs = [
        {
            'rvec_cam': rvecs_cams[i_cam],
            'tvec_cam': tvecs_cams[i_cam],
            'A': cam_matrices[i_cam],
            'k': ks[i_cam],
        }
        for i_cam in range(n_cams)
    ]

    return calibs, rvecs_boards, tvecs_boards


def get_obj_fcn_jacobians():
    return [jacobian(opt_ag.obj_fcn, i_var) for i_var in range(6)]
