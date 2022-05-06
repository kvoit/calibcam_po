import numpy as np
from scipy.spatial.transform import Rotation as R  # noqa
from autograd import jacobian  # noqa

from . import optimization_autograd as opt_ag

def obj_fcn_wrapper(vars_opt, args):
    corners = args['precalc']['corners']
    boards_coords_3d_0 = args['precalc']['boards_coords_3d_0']

    # Fill vars_full from initialization with vars_opts
    vars_full, n_cams = make_vars_full(vars_opt, args)

    # Unravel inputs. Note that calibs, board_coords_3d and their representations in args are changed in this function
    # and the return is in fact unnecessary!
    rvecs_cams, tvecs_cams, cam_matrices, ks, rvecs_boards, tvecs_boards = unravel_vars_full(vars_full, n_cams)

    return opt_ag.obj_fcn(
        rvecs_cams.ravel(),
        tvecs_cams.ravel(),
        cam_matrices.ravel(),
        ks.ravel(),
        rvecs_boards.ravel(),
        tvecs_boards.ravel(),
        boards_coords_3d_0.ravel(),
        corners.ravel()
    )


def make_vars_full(vars_opt, args):
    n_cams = len(args['precalc']['boards_coords_3d_0'])

    # Update full set of vars with free wars
    vars_full = args['vars_full']
    mask_opt = args['mask_opt']
    vars_full[mask_opt] = vars_opt

    return vars_full, n_cams


def obj_fcn_jacobian_wrapper(vars_opt, args):
    corners = args['precalc']['corners']
    boards_coords_3d_0 = args['precalc']['boards_coords_3d_0']

    # Fill vars_full from initialization with vars_opts
    vars_full, n_cams = make_vars_full(vars_opt, args)

    # Unravel inputs. Note that calibs, board_coords_3d and their representations in args are changed in this function
    # and the return is in fact unnecessary!
    rvecs_cams, tvecs_cams, cam_matrices, ks, rvecs_boards, tvecs_boards = unravel_vars_full(vars_full, n_cams)

    # Calculate the full jacobian
    obj_fcn_jacobian = np.concatenate(
        [j(
            rvecs_cams.ravel(),
            tvecs_cams.ravel(),
            cam_matrices.ravel(),
            ks.ravel(),
            rvecs_boards.ravel(),
            tvecs_boards.ravel(),
            boards_coords_3d_0.ravel(),
            corners.ravel()
        ) for j in args['precalc']['jacobians']],
        axis=1
    )

    # Return section of free variables
    return obj_fcn_jacobian[:, args['mask_opt']]


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
    cam_matrices = vars_full[st_idx:st_idx + n_cam_param_list[p_idx] * n_cams].reshape(3, 3, -1).transpose(axes=(2, 0, 1))

    p_idx = 3
    st_idx = n_cam_param_list[0:p_idx].sum(dtype=int) * n_cams
    ks = vars_full[st_idx:st_idx + n_cam_param_list[p_idx] * n_cams].reshape(n_cam_param_list[p_idx], -1).T

    board_pose_vars = vars_full[n_cams * n_cam_params:]
    rvecs_boards = board_pose_vars[0:board_pose_vars.size / 2].reshape(-1, 3)
    tvecs_boards = board_pose_vars[board_pose_vars.size / 2:].reshape(-1, 3)

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

    pose_idxs = np.where(np.any(frame_masks, axis=0))[0]  # indexes into full frame range
    pose_params = np.empty(shape=(2, pose_idxs.size, 3))
    for i_pose, pose_idx in enumerate(pose_idxs):  # Loop through the poses (frames that have a board pose)
        for calib, frame_mask_cam in zip(calibs, frame_masks):  # Loop through cameras ...
            if frame_mask_cam[pose_idx]:  # ... and check if frame is available
                frame_idxs_cam = np.where(frame_mask_cam)[0]  # Frame indexes corresponding to available rvecs/tvecs
                # TODO Instead of overwriting the pose, it should be averaged over all available cams.
                # See pose_estimation.estimate_cam_poses for averaging poses
                pose_params[0, i_pose, :] = calib['rvecs'][frame_idxs_cam == pose_idx].ravel()
                pose_params[1, i_pose, :] = calib['tvecs'][frame_idxs_cam == pose_idx].ravel()

    # camera_params are raveled with one scalar parameter for all cams grouped
    # pose_params are raveled with first all rvecs and then all tvecs (for faster unraveling in obj_fun)
    vars_full = np.concatenate((camera_params.T.ravel(), pose_params.ravel()), axis=0)
    mask_free = make_free_parameter_mask(calibs, frame_masks, opts_free_vars)
    vars_free = vars_full[mask_free]

    return vars_free, vars_full, mask_free


def make_free_parameter_mask(calibs, frame_masks, opts_free_vars):
    camera_mask = np.ones(shape=(
        len(calibs),
        3 + 3 + 9 + 5  # r + t + A + k
    ), dtype=bool)

    camera_mask[:, 0:3] = opts_free_vars['cam_pose']
    camera_mask[:, 3:6] = opts_free_vars['cam_pose']
    camera_mask[:, 6:15] = opts_free_vars['A'].ravel()
    camera_mask[:, 10:15] = opts_free_vars['k']

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
