#!/usr/bin/env python
import roslib; roslib.load_manifest('velma_task_cs_ros_interface')

import sys
import rospy
import math
import copy
import tf

from std_msgs.msg import ColorRGBA
from interactive_markers.interactive_marker_server import *
from interactive_markers.menu_handler import *
from visualization_msgs.msg import *
from geometry_msgs.msg import *
from tf.transformations import * 
import tf_conversions.posemath as pm
import PyKDL
from cartesian_trajectory_msgs.msg import *
import actionlib

from velma_common.velma_interface import *
from planner.planner import *

def exitError(code):
    if code == 0:
        print "OK"
        exit(0)
    print "ERROR:", code
    exit(code)

if __name__ == "__main__":

    rospy.init_node('jimp_test', anonymous=True)

    rospy.sleep(1)

    velma = VelmaInterface("/velma_task_cs_ros_interface")
    print "waiting for init..."

    velma.waitForInit()
    print "init ok"

    print "waiting for Planner init..."
    p = Planner(velma.maxJointTrajLen())
    if not p.waitForInit():
        print "could not initialize PLanner"
        exitError(2)
    print "Planner init ok"

#
#
#
    if velma.enableMotors() != 0:
        exitError(14)

    print "switching to SafeCol"
    velma.switchToSafeColBehavior()

    rospy.sleep(1.0)

    print "moving to current position"
    js = velma.getLastJointState()

    velma.moveJoint(js[1], 1.0, start_time=0.5, position_tol=15.0/180.0*math.pi)
    velma.waitForJoint()

    q_map = {'torso_0_joint':0.0,
            'right_arm_0_joint':-0.4843684434890747,
            'right_arm_1_joint':-1.8175444602966309,
            'right_arm_2_joint':1.3692808151245117,
            'right_arm_3_joint':1.1474847793579102,
            'right_arm_4_joint':0.37723231315612793,
            'right_arm_5_joint':-1.105877161026001,
            'right_arm_6_joint':-0.34941744804382324,
            'left_arm_0_joint':0.4843684434890747,
            'left_arm_1_joint':1.8175444602966309,
            'left_arm_2_joint':-1.3692808151245117,
            'left_arm_3_joint':-1.1474847793579102,
            'left_arm_4_joint':-0.37723231315612793,
            'left_arm_5_joint':1.105877161026001,
            'left_arm_6_joint':0.34941744804382324}

    rospy.sleep(0.5)
    js = velma.getLastJointState()

    if isConfigurationClose(q_map, js[1], tolerance=1.6):
        velma.moveJoint(q_map, None, 2.0, start_time=0.5, position_tol=15.0/180.0*math.pi)
        velma.waitForJoint()
    else:
        oml = OctomapListener("/octomap_binary")
        print "waiting for octomap update..."
        rospy.sleep(1.0)
        octomap = oml.getOctomap(timeout_s=5.0)

        print "planning..."

        p.processWorld(octomap)

        goal_constraint_1 = qMapToConstraints(q_map, tolerance=0.1, group=velma.getJointGroup("impedance_joints"))

        print "moving whole body to initial pose (jimp)"
        js = velma.getLastJointState()
        for i in range(10):
            traj = p.plan(js[1], [goal_constraint_1], "impedance_joints", max_velocity_scaling_factor=0.01)
            if traj != None:
                break
        if traj == None:
            print "ERROR: planning"
            exitError(4)
        if not velma.moveJointTraj(traj, start_time=0.5):
            exitError(5)
        if velma.waitForJoint() != 0:
            exitError(6)
        rospy.sleep(0.5)
        js = velma.getLastJointState()
        if not isConfigurationClose(q_map, js[1], tolerance=0.2):
            exitError(7)

    # body position: right

    print "moving to position right"
    q_map['torso_0_joint'] = -1.2
    velma.moveJoint(q_map, None, 4.0, start_time=0.5, position_tol=15.0/180.0*math.pi)
    velma.waitForJoint()

    print "moving head to position: left, up"
    q_dest = (-1.5, -0.9)
    velma.moveHead(q_dest, 3.0, start_time=0.5)
    if velma.waitForHead() != 0:
        exitError(4)
    if not isHeadConfigurationClose( velma.getHeadCurrentConfiguration(), q_dest, 0.1 ):
        exitError(5)

    print "moving head to position: left, down"
    q_dest = (-1.5, 0.9)
    velma.moveHead(q_dest, 3.0, start_time=0.5)
    if velma.waitForHead() != 0:
        exitError(4)
    if not isHeadConfigurationClose( velma.getHeadCurrentConfiguration(), q_dest, 0.1 ):
        exitError(5)

    print "moving head to position: center, down"
    q_dest = (0, 0.9)
    velma.moveHead(q_dest, 3.0, start_time=0.5)
    if velma.waitForHead() != 0:
        exitError(4)
    if not isHeadConfigurationClose( velma.getHeadCurrentConfiguration(), q_dest, 0.1 ):
        exitError(5)

    print "moving head to position: center, up"
    q_dest = (0, -0.9)
    velma.moveHead(q_dest, 3.0, start_time=0.5)
    if velma.waitForHead() != 0:
        exitError(4)
    if not isHeadConfigurationClose( velma.getHeadCurrentConfiguration(), q_dest, 0.1 ):
        exitError(5)

    # body position: central

    print "moving to position central"
    q_map['torso_0_joint'] = 0.0
    velma.moveJoint(q_map, None, 4.0, start_time=0.5, position_tol=15.0/180.0*math.pi)
    if velma.waitForJoint() != 0:
        exitError(6)

    print "moving head to position: center, down"
    q_dest = (0, 0.9)
    velma.moveHead(q_dest, 3.0, start_time=0.5)
    if velma.waitForHead() != 0:
        exitError(4)
    if not isHeadConfigurationClose( velma.getHeadCurrentConfiguration(), q_dest, 0.1 ):
        exitError(5)

    # body position: left

    print "moving to position left"
    q_map['torso_0_joint'] = 1.2
    velma.moveJoint(q_map, None, 4.0, start_time=0.5, position_tol=15.0/180.0*math.pi)
    if velma.waitForJoint() != 0:
        exitError(6)

    print "moving head to position: left, down"
    q_dest = (1.5, 0.9)
    velma.moveHead(q_dest, 3.0, start_time=0.5)
    if velma.waitForHead() != 0:
        exitError(4)
    if not isHeadConfigurationClose( velma.getHeadCurrentConfiguration(), q_dest, 0.1 ):
        exitError(5)

    print "moving head to position: left, up"
    q_dest = (1.5, -0.9)
    velma.moveHead(q_dest, 3.0, start_time=0.5)
    if velma.waitForHead() != 0:
        exitError(4)
    if not isHeadConfigurationClose( velma.getHeadCurrentConfiguration(), q_dest, 0.1 ):
        exitError(5)

    print "moving head to position: center, up"
    q_dest = (0, -0.9)
    velma.moveHead(q_dest, 3.0, start_time=0.5)
    if velma.waitForHead() != 0:
        exitError(4)
    if not isHeadConfigurationClose( velma.getHeadCurrentConfiguration(), q_dest, 0.1 ):
        exitError(5)

    print "moving head to position: center"
    q_dest = (0, 0)
    velma.moveHead(q_dest, 3.0, start_time=0.5)
    if velma.waitForHead() != 0:
        exitError(4)
    if not isHeadConfigurationClose( velma.getHeadCurrentConfiguration(), q_dest, 0.1 ):
        exitError(5)

    # body position: central

    print "moving to position central"
    q_map['torso_0_joint'] = 0.0
    velma.moveJoint(q_map, None, 4.0, start_time=0.5, position_tol=15.0/180.0*math.pi)
    if velma.waitForJoint() != 0:
        exitError(6)

#    print "moving head to position: center"
#    q_dest = (0, 0)
#    velma.moveHead(q_dest, 3.0, start_time=0.5)
#    if velma.waitForHead() != 0:
#        exitError(4)
#    if not isHeadConfigurationClose( velma.getHeadCurrentConfiguration(), q_dest, 0.1 ):
#        exitError(5)

    exit(0)
