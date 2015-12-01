#!/usr/bin/env python

from __future__ import division

import argparse
import numpy as np
import cv2
import time
import simulator
import controller
from generate_data import DataCollector
import util
import trajoptpy
from lfd.rapprentice import resampling, berkeley_pr2
from lfd.transfer import  planning
from lfd.environment.simulation import DynamicSimulationRobotWorld
from lfd.environment.simulation_object import XmlSimulationObject
from lfd.environment import environment
from lfd.environment import sim_util
from lfd.environment.robot_world import RealRobotWorld
try:
    import cloudprocpy
    from lfd.rapprentice import PR2
    import rospy
except:
    print "Couldn't import libraries for execution"
import IPython as ipy

def rotation_z(theta):
    return np.array([[np.cos(theta), -np.sin(theta), 0], [np.sin(theta), np.cos(theta), 0], [0, 0, 1]])

def look_at_angle(robot, xyz_target, reference_frame, worldFromCam):
    worldFromRef = robot.GetLink(reference_frame).GetTransform()
    refFromCam = np.linalg.inv(worldFromRef).dot(worldFromCam)

    xyz_cam = refFromCam[:3, 3]
    ax = xyz_target - xyz_cam # pointing axis
    pan = np.arctan(ax[1] / ax[0])
    tilt = np.arcsin(-ax[2] / np.linalg.norm(ax))
    return pan, tilt

def parse_input_args():
    parser = argparse.ArgumentParser()

    parser.add_argument("--animation", type=int, default=0, help="animates if it is non-zero. the viewer is stepped according to this number")
    parser.add_argument("--interactive", action="store_true", help="step animation and optimization if specified")
    parser.add_argument("--execution", type=int, default=0)

    parser.add_argument('--output', '-o', type=str, default=None)
    parser.add_argument('--num_trajs', '-n', type=int, default=10, metavar='N', help='total number of data points is N*T')
    parser.add_argument('--num_steps', '-t', type=int, default=10, metavar='T', help='number of time steps per trajectory')
    parser.add_argument('--visualize', '-v', type=int, default=1)
    parser.add_argument('--vis_scale', '-r', type=int, default=10, metavar='R', help='rescale image by R for visualization')
    parser.add_argument('--vel_max', '-m', type=float, default=np.deg2rad(5))
    parser.add_argument('--gripper_pos_min', type=float, nargs=3, default=[.7, -.3, .7], metavar=tuple([xyz + '_gripper_pos_min' for xyz in 'xyz']))
    parser.add_argument('--gripper_pos_max', type=float, nargs=3, default=[.5, .3, 1.1], metavar=tuple([xyz + '_gripper_pos_max' for xyz in 'xyz']))

    args = parser.parse_args()

    args.gripper_pos_min = np.asarray(args.gripper_pos_min)
    args.gripper_pos_max = np.asarray(args.gripper_pos_max)
    return args

def plan_full_traj(robot, hmat_start, hmat_end, start_fixed, n_steps=10, lr = 'r'):
    manip_name = {"l":"leftarm", "r":"rightarm"}[lr]
    ee_link_name = "%s_gripper_tool_frame"%lr
    ee_link = robot.GetLink(ee_link_name)

    new_hmats = np.asarray(resampling.interp_hmats(np.arange(n_steps), np.r_[0, n_steps-1], [hmat_start, hmat_end]))
    dof_vals = robot.GetManipulator(manip_name).GetArmDOFValues()
    old_traj = np.tile(dof_vals, (n_steps,1))

    traj, _, _ = planning.plan_follow_traj(robot, manip_name, ee_link, new_hmats, old_traj, start_fixed=start_fixed, beta_rot=10000.0)
    dof_inds = sim_util.dof_inds_from_name(robot, manip_name)
    return traj, dof_inds

def move_gripper(lfd_env, lfd_env_real, t_end, args, lr='r'):
    sim = lfd_env.sim
    ee_link_name = "%s_gripper_tool_frame"%lr
    hmat_start = sim.robot.GetLink(ee_link_name).GetTransform()
    if lr == 'r':
        theta = np.pi/2
    elif lr == 'l':
        theta = -np.pi/2
    else:
        raise
    R_end = rotation_z(theta)
    hmat_end = np.r_[np.c_[R_end, t_end], np.c_[0,0,0,1]]
    full_traj = plan_full_traj(sim.robot, hmat_start, hmat_end, True, lr=lr)

    if sim.viewer:
        sim_callback = lambda i: sim.viewer.Step()
    else:
        sim_callback = lambda i: None
    lfd_env.world.execute_trajectory(full_traj, step_viewer=args.animation, interactive=args.interactive, sim_callback=sim_callback,
                                     max_cart_vel_trans_traj=.05, max_cart_vel=.05)

    if args.execution:
        lfd_env_real.world.execute_trajectory(full_traj, step_viewer=args.animation, interactive=args.interactive)

def main():
    args = parse_input_args()

    trajoptpy.SetInteractive(args.interactive)

    sim = DynamicSimulationRobotWorld()
    world = sim
    sim.add_objects([XmlSimulationObject("robots/pr2-beta-static.zae", dynamic=False)])
    lfd_env = environment.LfdEnvironment(world, sim)
    if args.animation:
        sim.create_viewer()

    if args.execution:
        rospy.init_node("exec_task", disable_signals=True)
        pr2 = PR2.PR2()
        world = RealRobotWorld(pr2)
        lfd_env_real = environment.LfdEnvironment(world, sim)
    else:
        lfd_env_real = None

    if sim.viewer:
        camera_matrix = np.array([[   0, 1,   0, 0],
                                  [-0.5, 0, 0.9, 0],
                                  [ 0.9, 0, 0.5, 0],
                                  [ 3.7, 0, 2.5, 1]])
        sim.viewer.SetCameraManipulatorMatrix(camera_matrix)

    sim_util.reset_arms_to_side(sim)
    if args.execution:
        pr2.head.set_pan_tilt(0, 1.05)
        pr2.rarm.goto_posture('side')
        pr2.larm.goto_posture('side')
        pr2.rgrip.set_angle(0.54800022)
        pr2.lgrip.set_angle(0.54800022)
        pr2.join_all()
        time.sleep(.5)
        pr2.update_rave()

    if args.execution:
        pr2_head = simulator.PR2Head(sim.robot, pr2, args.vel_max)
    else:
        pr2_head = simulator.PR2HeadSimulator(sim.robot, args.vel_max)
    ctrl = controller.RandomController(*pr2_head.action_bounds)
    if args.output:
        sim_args = dict(vel_max=args.vel_max, simulator=args.simulator)
        collector = DataCollector(args.output, args.num_trajs * args.num_steps, sim_args=sim_args)
    else:
        collector = None

    done = False
    for traj_iter in range(args.num_trajs):
        try:
            gripper_pos = args.gripper_pos_min + np.random.random_sample(3) * (args.gripper_pos_max - args.gripper_pos_min)
            move_gripper(lfd_env, lfd_env_real, gripper_pos, args)
            angle = look_at_angle(sim.robot, gripper_pos, 'base_link', berkeley_pr2.get_kinect_transform(sim.robot))
            pr2_head.reset(angle)
            for step_iter in range(args.num_steps):
                state = pr2_head.state
                image = pr2_head.observe()
                action = ctrl.step(image)
                action = pr2_head.apply_action(action)
                image_next = pr2_head.observe()

                if collector:
                    collector.add(image_curr=image,
                                  image_next=image_next,
                                  image_diff=image_next - image,
                                  state=state,
                                  vel=action,
                                  gripper_pos=gripper_pos,
                                  T_w_k=berkeley_pr2.get_kinect_transform(sim.robot))

                # visualization
                if sim.viewer:
                    sim.viewer.Step()

                if args.visualize:
                    cv2.imshow("Image window", image)
                    key = cv2.waitKey(100)
                    key &= 255
                    if key == 27 or key == ord('q'):
                        print "Pressed ESC or q, exiting"
                        done = True
                        break
            if done:
                break
        except KeyboardInterrupt:
            break

    if args.visualize:
        cv2.destroyAllWindows()

if __name__ == '__main__':
    main()
